"""Log parsing and validation engine.

This module provides the `LogParser` class which parses individual raw log
lines into structured records using a configurable regular expression.

It enforces strict log format validation:
  1. Validates ISO-8601 UTC timestamp format and calendrical validity.
  2. Validates recognized log levels (INFO, WARN, ERROR).
  3. Validates service identifier format.
  4. Keeps track of total attempted lines, successfully parsed lines, and
     rejected lines.
  5. Retains a bounded diagnostic sample of rejected lines along with the
     exact reason for failure, preventing malformed lines from poisoning
     downstream search and analytics indices.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Dict, List, Optional, Pattern


# Canonical default log format:
# 2026-03-01T10:15:32Z ERROR payments timeout calling db after 3000ms
# Named groups allow the regex to be configured or customized while maintaining
# field extraction compatibility.
DEFAULT_LOG_PATTERN = (
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))\s+"
    r"(?P<level>[a-zA-Z]+)\s+"
    r"(?P<service>[a-zA-Z0-9_.\-]+)\s+"
    r"(?P<message>.*)$"
)

VALID_LEVELS = frozenset({"INFO", "WARN", "ERROR"})


@dataclass(frozen=True)
class ParsedLogLine:
    """Structured representation of a successfully parsed log entry."""
    timestamp_str: str       # e.g., "2026-03-01T10:15:32Z"
    epoch_seconds: int       # Unix timestamp in seconds (for fast bisect range queries)
    level: str               # "INFO", "WARN", or "ERROR"
    service: str             # Service name (e.g., "payments", "auth")
    message: str             # Free-form log message
    raw_line: str            # Original unparsed line


@dataclass(frozen=True)
class RejectedLine:
    """Diagnostic detail for a line that failed parsing or validation."""
    line_number: int
    raw_line: str
    reason: str


class LogParser:
    """Configurable regex-based log parser with rejection accounting.

    Attributes:
        pattern_str: The raw regex string used for pattern matching.
        max_rejected_samples: Maximum number of rejected line samples to store.
        total_attempted: Count of lines passed to parse_line.
        total_parsed: Count of successfully parsed lines.
        total_rejected: Count of malformed or invalid lines.
        rejected_samples: List of RejectedLine diagnostics (up to max_rejected_samples).
    """

    def __init__(
        self,
        pattern: str = DEFAULT_LOG_PATTERN,
        max_rejected_samples: int = 100,
    ) -> None:
        """Initialize the LogParser with a regex pattern.

        Args:
            pattern: Regular expression with named groups:
                     'timestamp', 'level', 'service', and 'message'.
            max_rejected_samples: Maximum number of rejection samples to retain in memory.
        """
        self.pattern_str = pattern
        self.regex: Pattern[str] = re.compile(pattern)
        self.max_rejected_samples = max_rejected_samples

        self.total_attempted: int = 0
        self.total_parsed: int = 0
        self.total_rejected: int = 0
        self.rejected_samples: List[RejectedLine] = []

    def parse_line(self, line: str, line_number: int = 0) -> Optional[ParsedLogLine]:
        """Parse a single raw log line.

        If the line conforms to the expected format, a ParsedLogLine is returned
        and total_parsed is incremented.
        If the line is malformed, None is returned, total_rejected is incremented,
        and a RejectedLine record is saved (if under sample quota).

        Args:
            line: The raw line string from the log file.
            line_number: Optional 1-based line number for diagnostic reporting.

        Returns:
            ParsedLogLine if valid, or None if malformed.
        """
        self.total_attempted += 1
        stripped = line.rstrip("\r\n")

        # 1. Empty lines are immediately rejected.
        if not stripped.strip():
            self._record_rejection(line_number, stripped, "Empty or blank line")
            return None

        # 2. Match against configured regex pattern.
        match = self.regex.match(stripped)
        if not match:
            self._record_rejection(
                line_number,
                stripped,
                "Does not match log format: '<ISO-8601> <LEVEL> <SERVICE> <MESSAGE>'"
            )
            return None

        groups = match.groupdict()
        ts_str = groups.get("timestamp", "")
        level = groups.get("level", "")
        service = groups.get("service", "")
        message = groups.get("message", "")

        # 3. Validate level.
        if level not in VALID_LEVELS:
            self._record_rejection(
                line_number,
                stripped,
                f"Invalid level '{level}'. Must be one of: {sorted(VALID_LEVELS)}"
            )
            return None

        # 4. Parse and validate timestamp into Unix epoch seconds.
        try:
            # Replace trailing 'Z' with '+00:00' for standard ISO-8601 parsing.
            normalized_ts = ts_str.replace("Z", "+00:00")
            dt = datetime.fromisoformat(normalized_ts)
            # Ensure timestamp is UTC-aware.
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            epoch_seconds = int(dt.timestamp())
        except (ValueError, OverflowError) as exc:
            self._record_rejection(
                line_number,
                stripped,
                f"Invalid ISO-8601 timestamp '{ts_str}': {exc}"
            )
            return None

        # 5. Successfully parsed.
        self.total_parsed += 1
        return ParsedLogLine(
            timestamp_str=ts_str,
            epoch_seconds=epoch_seconds,
            level=level,
            service=service,
            message=message,
            raw_line=stripped,
        )

    def _record_rejection(self, line_number: int, raw_line: str, reason: str) -> None:
        """Internal helper to count and sample rejected lines."""
        self.total_rejected += 1
        if len(self.rejected_samples) < self.max_rejected_samples:
            # Truncate raw line if excessively long for clean diagnostic reports.
            preview = raw_line[:120] + ("..." if len(raw_line) > 120 else "")
            self.rejected_samples.append(
                RejectedLine(line_number=line_number, raw_line=preview, reason=reason)
            )

    def get_summary(self) -> Dict[str, object]:
        """Return diagnostic metrics of the parsing run."""
        return {
            "total_attempted": self.total_attempted,
            "total_parsed": self.total_parsed,
            "total_rejected": self.total_rejected,
            "sample_rejections": [
                {
                    "line_number": r.line_number,
                    "raw_line": r.raw_line,
                    "reason": r.reason,
                }
                for r in self.rejected_samples
            ],
        }

    def reset(self) -> None:
        """Reset parser counters and sample buffer."""
        self.total_attempted = 0
        self.total_parsed = 0
        self.total_rejected = 0
        self.rejected_samples.clear()
