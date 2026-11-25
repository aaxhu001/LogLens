"""Query execution engine for LogLens.

This module executes parsed search queries against InvertedIndex, StructuredIndex,
and LineStore using high-performance array operations.

Execution Strategy:
  1. Time Window Pruning: Time boundaries [from_time, to_time] are resolved in
     O(log N) time to an ID range [min_id, max_id] via bisect.
  2. Smallest-First Intersection: Positive candidate posting lists are sorted
     by length in ascending order. Intersecting the smallest list first shrinks
     the candidate set on the very first step.
  3. Short-Circuiting: If any mandatory term in an AND query has zero postings,
     or if the time window contains zero records, the engine immediately returns
     an empty response without scanning further.
  4. Two-Pointer & Bisect Merging: Uses two-pointer linear merge or binary search
     depending on list density ratios to guarantee sub-millisecond execution.
  5. Paginated Line Hydration: Only the lines needed for the current display page
     (default 100) are retrieved from disk via LineStore.
  6. Strict Latency Measurement: Query time is measured strictly around query
     evaluation and line hydration, excluding network overhead.
"""

from array import array
import bisect
from dataclasses import dataclass, field
import heapq
import math
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from loglens.inverted_index import InvertedIndex
from loglens.line_store import LineStore
from loglens.parser import LogParser, ParsedLogLine
from loglens.query_parser import ParsedQuery, QueryParser, QuerySyntaxError
from loglens.structured_index import StructuredIndex


@dataclass
class SearchResponse:
    """Standardized response payload from QueryEngine search execution."""
    lines: List[Dict[str, Any]]
    total_matches: int
    page: int
    page_size: int
    total_pages: int
    query_ms: float
    level_counts: Dict[str, int]
    matched_tokens: List[str]
    query_parsed: Dict[str, Any]


def intersect_two_sorted(a: array, b: array) -> array:
    """Intersect two sorted arrays of Line IDs.

    Uses binary search if one list is drastically smaller than the other,
    otherwise uses a two-pointer linear merge (O(len(a) + len(b))).

    Args:
        a: First sorted array('I').
        b: Second sorted array('I').

    Returns:
        Sorted array('I') of elements present in both a and b.
    """
    len_a = len(a)
    len_b = len(b)

    if len_a == 0 or len_b == 0:
        return array("I")

    # Ensure 'a' is the smaller array
    if len_a > len_b:
        a, b = b, a
        len_a, len_b = len_b, len_a

    result = array("I")

    # If the smaller array is drastically smaller, binary search into 'b'
    if len_a * 20 < len_b:
        for val in a:
            idx = bisect.bisect_left(b, val)
            if idx < len_b and b[idx] == val:
                result.append(val)
        return result

    # Otherwise, two-pointer linear scan
    idx_a = 0
    idx_b = 0
    while idx_a < len_a and idx_b < len_b:
        val_a = a[idx_a]
        val_b = b[idx_b]
        if val_a == val_b:
            result.append(val_a)
            idx_a += 1
            idx_b += 1
        elif val_a < val_b:
            idx_a += 1
        else:
            idx_b += 1

    return result


def union_sorted_lists(lists: List[array]) -> array:
    """Compute the union of multiple sorted arrays of Line IDs."""
    non_empty = [l for l in lists if len(l) > 0]
    if not non_empty:
        return array("I")
    if len(non_empty) == 1:
        return non_empty[0]

    # Combine and sort unique elements
    combined_set: Set[int] = set()
    for l in non_empty:
        combined_set.update(l)

    sorted_list = sorted(combined_set)
    res = array("I")
    res.extend(sorted_list)
    return res


def restrict_to_id_range(arr: array, min_id: int, max_id: int) -> array:
    """Slice a sorted array to only include IDs within [min_id, max_id] inclusive.

    Runs in O(log N) using bisect.
    """
    if len(arr) == 0 or min_id > max_id:
        return array("I")

    left_idx = bisect.bisect_left(arr, min_id)
    right_idx = bisect.bisect_right(arr, max_id)
    return arr[left_idx:right_idx]


class QueryEngine:
    """Executes structured search queries against indexed log data."""

    def __init__(
        self,
        line_store: LineStore,
        inverted_index: InvertedIndex,
        structured_index: StructuredIndex,
        parser: Optional[LogParser] = None,
    ) -> None:
        """Initialize QueryEngine with references to stores and indexes."""
        self.line_store = line_store
        self.inverted_index = inverted_index
        self.structured_index = structured_index
        self.parser = parser or LogParser()

    def search(
        self,
        query: Union[str, ParsedQuery],
        page: int = 1,
        page_size: int = 100,
        from_time: Optional[str] = None,
        to_time: Optional[str] = None,
        level_filter: Optional[str] = None,
        service_filter: Optional[str] = None,
    ) -> SearchResponse:
        """Execute search query with pagination, filters, and level counting.

        Latency timer starts immediately and stops before network return.

        Args:
            query: Raw search query string or pre-parsed ParsedQuery.
            page: 1-based page number (default 1).
            page_size: Results per page (default 100).
            from_time: Optional ISO timestamp lower bound.
            to_time: Optional ISO timestamp upper bound.
            level_filter: Optional explicit level filter ("INFO", "WARN", "ERROR").
            service_filter: Optional explicit service filter.

        Returns:
            SearchResponse containing matching line records, metrics, and facets.
        """
        # Start execution latency timer
        start_perf = time.perf_counter()

        # 1. Parse query if a string was provided
        if isinstance(query, str):
            parsed = QueryParser.parse(
                query_str=query,
                from_time=from_time,
                to_time=to_time,
                level_filter=level_filter,
                service_filter=service_filter,
            )
        else:
            parsed = query

        total_indexed_lines = self.line_store.total_lines()

        # 2. Time range to ID range conversion via bisect
        min_id, max_id = self.structured_index.time_range_to_id_range(
            from_epoch=parsed.from_epoch, to_epoch=parsed.to_epoch
        )

        # Short-circuit if time range is empty or invalid
        if min_id > max_id or total_indexed_lines == 0:
            query_ms = (time.perf_counter() - start_perf) * 1000.0
            return self._build_empty_response(parsed, page, page_size, query_ms)

        # 3. Gather positive candidate lists
        positive_lists: List[array] = []

        # (a) Structured level filter
        if parsed.level:
            lvl_postings = self.structured_index.get_level_postings(parsed.level)
            if len(lvl_postings) == 0:
                query_ms = (time.perf_counter() - start_perf) * 1000.0
                return self._build_empty_response(parsed, page, page_size, query_ms)
            positive_lists.append(lvl_postings)

        # (b) Structured service filter
        if parsed.service:
            svc_postings = self.structured_index.get_service_postings(parsed.service)
            if len(svc_postings) == 0:
                query_ms = (time.perf_counter() - start_perf) * 1000.0
                return self._build_empty_response(parsed, page, page_size, query_ms)
            positive_lists.append(svc_postings)

        # (c) AND search tokens
        for token in parsed.and_tokens:
            token_postings = self.inverted_index.get_postings(token)
            # In an AND query, if ANY required token has 0 postings, match is empty!
            if len(token_postings) == 0:
                query_ms = (time.perf_counter() - start_perf) * 1000.0
                return self._build_empty_response(parsed, page, page_size, query_ms)
            positive_lists.append(token_postings)

        # (d) OR search tokens
        if parsed.or_tokens:
            or_lists = [self.inverted_index.get_postings(tok) for tok in parsed.or_tokens]
            or_union = union_sorted_lists(or_lists)
            if len(or_union) == 0:
                query_ms = (time.perf_counter() - start_perf) * 1000.0
                return self._build_empty_response(parsed, page, page_size, query_ms)
            positive_lists.append(or_union)

        # 4. Evaluate intersection
        time_bounded = (min_id > 0) or (max_id < total_indexed_lines - 1)

        if not positive_lists:
            # Query is purely time-bounded or completely open
            if time_bounded:
                candidates = array("I", range(min_id, max_id + 1))
            else:
                candidates = array("I", range(total_indexed_lines))
        else:
            # Smallest list first! Sort lists by length ascending
            positive_lists.sort(key=len)

            # Start with the smallest candidate list
            candidates = positive_lists[0]

            # Restrict the smallest list to the time window first
            if time_bounded:
                candidates = restrict_to_id_range(candidates, min_id, max_id)

            # Intersect iteratively with the remaining lists
            for next_list in positive_lists[1:]:
                if len(candidates) == 0:
                    break
                candidates = intersect_two_sorted(candidates, next_list)

        # 5. Exclude NOT tokens
        if len(candidates) > 0 and parsed.not_tokens:
            not_set: Set[int] = set()
            for not_tok in parsed.not_tokens:
                not_postings = self.inverted_index.get_postings(not_tok)
                if not_postings:
                    not_set.update(not_postings)

            if not_set:
                filtered = array("I")
                for line_id in candidates:
                    if line_id not in not_set:
                        filtered.append(line_id)
                candidates = filtered

        total_matches = len(candidates)

        # 6. Compute counts per level for the matched set
        level_counts = {"INFO": 0, "WARN": 0, "ERROR": 0}
        level_codes = self.structured_index.level_codes
        code_to_level = {0: "INFO", 1: "WARN", 2: "ERROR"}

        for lid in candidates:
            if lid < len(level_codes):
                code = level_codes[lid]
                lvl_str = code_to_level.get(code, "INFO")
                level_counts[lvl_str] += 1

        # 7. Pagination calculation
        page = max(1, page)
        page_size = max(1, page_size)
        total_pages = math.ceil(total_matches / page_size) if total_matches > 0 else 0

        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        page_ids = candidates[start_idx:end_idx]

        # 8. Hydrate log lines from disk via LineStore
        lines_payload: List[Dict[str, Any]] = []
        for lid in page_ids:
            raw_text = self.line_store.get_line(lid)
            # Parse record fields for rich display
            parsed_line = self.parser.parse_line(raw_text)
            if parsed_line:
                lines_payload.append({
                    "line_id": lid,
                    "raw_text": raw_text,
                    "timestamp": parsed_line.timestamp_str,
                    "level": parsed_line.level,
                    "service": parsed_line.service,
                    "message": parsed_line.message,
                })
            else:
                lines_payload.append({
                    "line_id": lid,
                    "raw_text": raw_text,
                    "timestamp": "",
                    "level": "UNKNOWN",
                    "service": "",
                    "message": raw_text,
                })

        # Measure query execution time
        query_ms = (time.perf_counter() - start_perf) * 1000.0

        # Highlighting tokens (all positive search terms)
        highlight_tokens = list(set(parsed.and_tokens + parsed.or_tokens))

        return SearchResponse(
            lines=lines_payload,
            total_matches=total_matches,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            query_ms=round(query_ms, 2),
            level_counts=level_counts,
            matched_tokens=highlight_tokens,
            query_parsed={
                "raw": parsed.raw_query,
                "and_tokens": parsed.and_tokens,
                "or_tokens": parsed.or_tokens,
                "not_tokens": parsed.not_tokens,
                "level": parsed.level,
                "service": parsed.service,
                "from": parsed.from_time_str,
                "to": parsed.to_time_str,
            },
        )

    def _build_empty_response(
        self, parsed: ParsedQuery, page: int, page_size: int, query_ms: float
    ) -> SearchResponse:
        """Construct an empty SearchResponse for short-circuit paths."""
        return SearchResponse(
            lines=[],
            total_matches=0,
            page=page,
            page_size=page_size,
            total_pages=0,
            query_ms=round(query_ms, 2),
            level_counts={"INFO": 0, "WARN": 0, "ERROR": 0},
            matched_tokens=list(set(parsed.and_tokens + parsed.or_tokens)),
            query_parsed={
                "raw": parsed.raw_query,
                "and_tokens": parsed.and_tokens,
                "or_tokens": parsed.or_tokens,
                "not_tokens": parsed.not_tokens,
                "level": parsed.level,
                "service": parsed.service,
                "from": parsed.from_time_str,
                "to": parsed.to_time_str,
            },
        )
