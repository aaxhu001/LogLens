"""Query parser for LogLens search expressions.

This module parses user search queries into structured filter criteria and
boolean token sets.

Supported Syntax:
  - Bare Tokens (Default AND): 'timeout database' -> requires both 'timeout' AND 'database'.
  - Explicit OR: 'timeout OR deadlock' -> matches any line containing either token.
  - Explicit NOT / Prefix Negation: 'timeout NOT retry' or 'timeout -retry' -> excludes 'retry'.
  - Structured Filters:
      - 'level:ERROR' or 'level:WARN' or 'level:INFO'
      - 'service:payments' or 'service:auth'
      - 'from:2026-03-01T00:00:00Z'
      - 'to:2026-03-10T23:59:59Z'

Error Handling:
  Invalid syntax raises `QuerySyntaxError` with an actionable error explanation
  and a concrete valid example.
"""

from dataclasses import dataclass, field
import re
from typing import List, Optional

from loglens.parser import VALID_LEVELS
from loglens.structured_index import parse_iso_to_epoch
from loglens.tokenizer import tokenize


EXAMPLE_QUERY = "level:ERROR service:payments timeout calling db NOT retry"


class QuerySyntaxError(ValueError):
    """Raised when a search query contains invalid syntax or unrecognized filters."""

    def __init__(self, message: str, example: str = EXAMPLE_QUERY) -> None:
        full_msg = f"{message}\nValid Example: '{example}'"
        super().__init__(full_msg)
        self.raw_message = message
        self.example = example


@dataclass
class ParsedQuery:
    """Deconstructed representation of a search expression."""
    raw_query: str
    and_tokens: List[str] = field(default_factory=list)
    or_tokens: List[str] = field(default_factory=list)
    not_tokens: List[str] = field(default_factory=list)
    level: Optional[str] = None
    service: Optional[str] = None
    from_time_str: Optional[str] = None
    to_time_str: Optional[str] = None
    from_epoch: Optional[int] = None
    to_epoch: Optional[int] = None

    def is_empty(self) -> bool:
        """Return True if no tokens or filters were specified."""
        return (
            not self.and_tokens
            and not self.or_tokens
            and not self.not_tokens
            and self.level is None
            and self.service is None
            and self.from_epoch is None
            and self.to_epoch is None
        )


class QueryParser:
    """Parses raw search queries into structured ParsedQuery objects."""

    @staticmethod
    def parse(
        query_str: str,
        from_time: Optional[str] = None,
        to_time: Optional[str] = None,
        level_filter: Optional[str] = None,
        service_filter: Optional[str] = None,
    ) -> ParsedQuery:
        """Parse query string and explicit parameters into a unified ParsedQuery.

        Args:
            query_str: Search string containing tokens, operators, and/or filters.
            from_time: Optional ISO timestamp override for range start.
            to_time: Optional ISO timestamp override for range end.
            level_filter: Optional level filter override (e.g. from UI dropdown).
            service_filter: Optional service filter override.

        Returns:
            ParsedQuery instance ready for execution by QueryEngine.

        Raises:
            QuerySyntaxError: If the query contains syntax errors or invalid filters.
        """
        raw_str = (query_str or "").strip()
        parsed = ParsedQuery(raw_query=raw_str)

        # Apply explicit parameters first if provided
        if level_filter:
            lvl = level_filter.strip().upper()
            if lvl not in VALID_LEVELS:
                raise QuerySyntaxError(
                    f"Invalid level filter '{level_filter}'. Allowed: {sorted(VALID_LEVELS)}"
                )
            parsed.level = lvl

        if service_filter:
            parsed.service = service_filter.strip().lower()

        if from_time:
            try:
                parsed.from_time_str = from_time.strip()
                parsed.from_epoch = parse_iso_to_epoch(parsed.from_time_str)
            except Exception as exc:
                raise QuerySyntaxError(f"Invalid 'from' timestamp parameter '{from_time}': {exc}")

        if to_time:
            try:
                parsed.to_time_str = to_time.strip()
                parsed.to_epoch = parse_iso_to_epoch(parsed.to_time_str)
            except Exception as exc:
                raise QuerySyntaxError(f"Invalid 'to' timestamp parameter '{to_time}': {exc}")

        if not raw_str:
            return parsed

        # Split query string into whitespace-separated tokens while preserving quotes if any
        raw_parts = raw_str.split()

        i = 0
        n = len(raw_parts)

        # Temporary lists for boolean categorization
        and_terms: List[str] = []
        or_terms: List[str] = []
        not_terms: List[str] = []

        # We keep track of whether the previous token was 'OR'
        pending_or = False

        while i < n:
            part = raw_parts[i]
            upper_part = part.upper()

            # 1. Check for key:value structured filters (e.g., level:ERROR, service:payments)
            if ":" in part and not part.startswith("http"):
                colon_idx = part.find(":")
                key = part[:colon_idx].strip().lower()
                val = part[colon_idx + 1:].strip()

                if not val:
                    raise QuerySyntaxError(f"Empty value for filter '{key}:'.")

                if key == "level":
                    val_upper = val.upper()
                    if val_upper not in VALID_LEVELS:
                        raise QuerySyntaxError(
                            f"Invalid level '{val}'. Allowed levels: {sorted(VALID_LEVELS)}"
                        )
                    parsed.level = val_upper
                elif key == "service":
                    parsed.service = val.lower()
                elif key == "from":
                    try:
                        parsed.from_time_str = val
                        parsed.from_epoch = parse_iso_to_epoch(val)
                    except Exception as exc:
                        raise QuerySyntaxError(f"Invalid 'from' timestamp '{val}': {exc}")
                elif key == "to":
                    try:
                        parsed.to_time_str = val
                        parsed.to_epoch = parse_iso_to_epoch(val)
                    except Exception as exc:
                        raise QuerySyntaxError(f"Invalid 'to' timestamp '{val}': {exc}")
                else:
                    raise QuerySyntaxError(
                        f"Unknown filter '{key}'. Supported filters are: level, service, from, to."
                    )
                i += 1
                continue

            # 2. Check for explicit 'NOT' operator
            if upper_part == "NOT":
                if i + 1 >= n:
                    raise QuerySyntaxError("Dangling 'NOT' operator at end of query.")
                next_part = raw_parts[i + 1]
                if next_part.upper() in ("AND", "OR", "NOT"):
                    raise QuerySyntaxError(f"Unexpected operator '{next_part}' immediately following 'NOT'.")
                negated_tokens = tokenize(next_part)
                if not negated_tokens:
                    raise QuerySyntaxError(f"No valid alphanumeric tokens in negated term '{next_part}'.")
                not_terms.extend(negated_tokens)
                i += 2
                continue

            # 3. Check for prefix negation (e.g. '-retry')
            if part.startswith("-") and len(part) > 1:
                negated_tokens = tokenize(part[1:])
                if negated_tokens:
                    not_terms.extend(negated_tokens)
                i += 1
                continue

            # 4. Check for 'OR' operator
            if upper_part == "OR":
                if i == 0 or i == n - 1 or pending_or:
                    raise QuerySyntaxError("Misplaced 'OR' operator. 'OR' must appear between two terms.")
                # The preceding token should move from and_terms to or_terms if it was just added
                if and_terms:
                    last_term = and_terms.pop()
                    or_terms.append(last_term)
                pending_or = True
                i += 1
                continue

            # 5. Regular search term
            tokens = tokenize(part)
            if not tokens:
                i += 1
                continue

            if pending_or:
                or_terms.extend(tokens)
                pending_or = False
            else:
                and_terms.extend(tokens)

            i += 1

        if pending_or:
            raise QuerySyntaxError("Query ends with trailing 'OR' operator.")

        # Validate time ordering if both from and to are specified
        if (
            parsed.from_epoch is not None
            and parsed.to_epoch is not None
            and parsed.from_epoch > parsed.to_epoch
        ):
            raise QuerySyntaxError(
                f"'from' timestamp ({parsed.from_time_str}) cannot be after 'to' timestamp ({parsed.to_time_str})."
            )

        parsed.and_tokens = and_terms
        parsed.or_tokens = or_terms
        parsed.not_tokens = not_terms
        return parsed
