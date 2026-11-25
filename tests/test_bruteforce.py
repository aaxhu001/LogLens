"""Brute-force verification test for LogLens query engine.

This test compares the results of QueryEngine against an independent,
naive linear-scan brute-force matcher over an entire log dataset.

It tests EVERY query type mandated by the PRD:
  1. Single token
  2. Multi-token AND
  3. Combined tokens with numbers (e.g. '3000ms')
  4. Explicit OR
  5. Explicit NOT
  6. Prefix NOT (-token)
  7. Level filter
  8. Service filter
  9. Time range filter
 10. Complex combined filters (token + level + service + time + NOT)
 11. Non-existent tokens (zero match edge case)

For every query, the set of matched LineIDs from QueryEngine must be
100% IDENTICAL to the brute-force scan.
"""

from datetime import datetime, timezone
import json
import os
import unittest
from typing import List, Optional, Set

from loglens.ingestor import Ingestor
from loglens.parser import LogParser
from loglens.query_engine import QueryEngine
from loglens.structured_index import parse_iso_to_epoch
from loglens.tokenizer import tokenize


class BruteForceLine:
    """Simple record container for naive linear scan verification."""
    def __init__(self, line_id: int, epoch: int, level: str, service: str, message: str, tokens: Set[str]):
        self.line_id = line_id
        self.epoch = epoch
        self.level = level
        self.service = service
        self.message = message
        self.tokens = tokens


class BruteForceScanner:
    """Independent naive log scanner with zero index acceleration."""

    def __init__(self, log_path: str):
        self.records: List[BruteForceLine] = []
        parser = LogParser()

        with open(log_path, "r", encoding="utf-8") as f:
            valid_id = 0
            for line_no, raw_line in enumerate(f, 1):
                parsed = parser.parse_line(raw_line, line_number=line_no)
                if parsed:
                    msg_tokens = set(tokenize(parsed.message))
                    self.records.append(
                        BruteForceLine(
                            line_id=valid_id,
                            epoch=parsed.epoch_seconds,
                            level=parsed.level,
                            service=parsed.service,
                            message=parsed.message,
                            tokens=msg_tokens,
                        )
                    )
                    valid_id += 1

    def match(
        self,
        and_tokens: Optional[List[str]] = None,
        or_tokens: Optional[List[str]] = None,
        not_tokens: Optional[List[str]] = None,
        level: Optional[str] = None,
        service: Optional[str] = None,
        from_epoch: Optional[int] = None,
        to_epoch: Optional[int] = None,
    ) -> List[int]:
        """Linearly scan all records and return matching line IDs."""
        and_tokens = and_tokens or []
        or_tokens = or_tokens or []
        not_tokens = not_tokens or []

        matched_ids: List[int] = []

        for rec in self.records:
            # 1. Level check
            if level and rec.level != level:
                continue

            # 2. Service check
            if service and rec.service != service:
                continue

            # 3. Time bounds check
            if from_epoch is not None and rec.epoch < from_epoch:
                continue
            if to_epoch is not None and rec.epoch > to_epoch:
                continue

            # 4. AND tokens check (all must be in tokens)
            if and_tokens:
                if not all(tok in rec.tokens for tok in and_tokens):
                    continue

            # 5. OR tokens check (at least one must be in tokens)
            if or_tokens:
                if not any(tok in rec.tokens for tok in or_tokens):
                    continue

            # 6. NOT tokens check (none must be in tokens)
            if not_tokens:
                if any(tok in rec.tokens for tok in not_tokens):
                    continue

            matched_ids.append(rec.line_id)

        return matched_ids


class TestBruteForceComparison(unittest.TestCase):
    """Compare QueryEngine against BruteForceScanner across a battery of queries."""

    @classmethod
    def setUpClass(cls):
        cls.log_path = "data/sample.log"
        if not os.path.exists(cls.log_path):
            raise FileNotFoundError(f"'{cls.log_path}' not found. Generate it first.")

        # Build indexed engine
        cls.ingestor = Ingestor()
        cls.report = cls.ingestor.ingest_path(cls.log_path)
        cls.engine = QueryEngine(
            cls.ingestor.line_store,
            cls.ingestor.inverted_index,
            cls.ingestor.structured_index,
        )

        # Build naive scanner
        cls.scanner = BruteForceScanner(cls.log_path)

    def _verify_query(
        self,
        query_str: str,
        and_tokens: Optional[List[str]] = None,
        or_tokens: Optional[List[str]] = None,
        not_tokens: Optional[List[str]] = None,
        level: Optional[str] = None,
        service: Optional[str] = None,
        from_str: Optional[str] = None,
        to_str: Optional[str] = None,
    ) -> None:
        """Run query through QueryEngine and compare against brute-force scanner."""
        from_epoch = parse_iso_to_epoch(from_str) if from_str else None
        to_epoch = parse_iso_to_epoch(to_str) if to_str else None

        # 1. Brute-force ground truth
        expected_ids = self.scanner.match(
            and_tokens=and_tokens,
            or_tokens=or_tokens,
            not_tokens=not_tokens,
            level=level,
            service=service,
            from_epoch=from_epoch,
            to_epoch=to_epoch,
        )

        # 2. QueryEngine execution (with max page_size to retrieve all matching IDs)
        resp = self.engine.search(
            query=query_str,
            page=1,
            page_size=len(self.scanner.records) + 10,
            from_time=from_str,
            to_time=to_str,
            level_filter=level,
            service_filter=service,
        )

        actual_ids = [line["line_id"] for line in resp.lines]

        # Verify exact equality of match counts and line ID sequences
        self.assertEqual(
            resp.total_matches,
            len(expected_ids),
            f"Query '{query_str}': expected {len(expected_ids)} matches, got {resp.total_matches}",
        )
        self.assertEqual(
            actual_ids,
            expected_ids,
            f"Query '{query_str}': Line IDs do not match brute-force ground truth!",
        )

    def test_single_token_timeout(self):
        self._verify_query("timeout", and_tokens=["timeout"])

    def test_single_token_3000ms(self):
        self._verify_query("3000ms", and_tokens=["3000ms"])

    def test_multi_token_and(self):
        self._verify_query("timeout database", and_tokens=["timeout", "database"])

    def test_multi_token_and_with_service_token(self):
        self._verify_query("calling db after 3000ms", and_tokens=["calling", "db", "after", "3000ms"])

    def test_explicit_or(self):
        self._verify_query("timeout OR connection", or_tokens=["timeout", "connection"])

    def test_explicit_not(self):
        self._verify_query("timeout NOT table", and_tokens=["timeout"], not_tokens=["table"])

    def test_prefix_not(self):
        self._verify_query("timeout -table", and_tokens=["timeout"], not_tokens=["table"])

    def test_level_filter_error(self):
        self._verify_query("level:ERROR", level="ERROR")

    def test_level_filter_warn(self):
        self._verify_query("level:WARN", level="WARN")

    def test_level_filter_info(self):
        self._verify_query("level:INFO", level="INFO")

    def test_service_filter(self):
        self._verify_query("service:payments", service="payments")

    def test_service_and_level_combined(self):
        self._verify_query(
            "level:ERROR service:payments",
            level="ERROR",
            service="payments",
        )

    def test_time_range_window(self):
        self._verify_query(
            "level:ERROR",
            level="ERROR",
            from_str="2026-03-01T00:00:00Z",
            to_str="2026-03-05T23:59:59Z",
        )

    def test_fully_combined_query(self):
        # Combines level + service + token + NOT + time range
        self._verify_query(
            "level:ERROR service:payments timeout NOT deadlock",
            and_tokens=["timeout"],
            not_tokens=["deadlock"],
            level="ERROR",
            service="payments",
            from_str="2026-03-01T00:00:00Z",
            to_str="2026-03-10T23:59:59Z",
        )

    def test_nonexistent_token_returns_zero(self):
        self._verify_query("nonexistenttoken99999", and_tokens=["nonexistenttoken99999"])


if __name__ == "__main__":
    unittest.main()
