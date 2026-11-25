r"""Structured index for metadata filtering (level, service, and timestamps).

This module manages structured indexes that allow instant filtering on:
  1. Log Level (INFO, WARN, ERROR)
  2. Service Name (payments, auth, db, api, etc.)
  3. Time Ranges ([from_time, to_time])

Timestamps are stored in an `array('I')` strictly parallel to Line IDs.
Because log lines arrive sequentially with non-decreasing timestamps,
a time range query `[from_time, to_time]` is converted into an inclusive
Line ID range `[min_id, max_id]` in $O(\log N)$ time using Python's `bisect`.
"""

from array import array
import bisect
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple


def parse_iso_to_epoch(iso_str: str) -> int:
    """Parse an ISO-8601 UTC timestamp string to Unix epoch seconds.

    Supports formats like '2026-03-01T10:15:32Z' and '2026-03-01T10:15:32+00:00'.

    Args:
        iso_str: ISO-8601 timestamp string.

    Returns:
        Unix epoch integer seconds.
    """
    normalized = iso_str.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


class StructuredIndex:
    """Stores structured metadata indexes for fast attribute filtering."""

    def __init__(self) -> None:
        """Initialize empty structured posting arrays."""
        # Parallel timestamp array: LineID -> Unix epoch seconds
        self.timestamps: array = array("I")

        # Level postings: level string -> array('I') of Line IDs
        self.level_postings: Dict[str, array] = {
            "INFO": array("I"),
            "WARN": array("I"),
            "ERROR": array("I"),
        }

        # Level codes array parallel to LineID: 0=INFO, 1=WARN, 2=ERROR
        # Enables O(1) level lookup without dictionary overhead.
        self.level_codes: array = array("B")

        # Service postings: service string -> array('I') of Line IDs
        self.service_postings: Dict[str, array] = {}

        # Tracking for chronological anomalies
        self.out_of_order_count: int = 0
        self._last_timestamp: int = 0

    def add_entry(
        self, line_id: int, epoch_seconds: int, level: str, service: str
    ) -> None:
        """Record metadata for a newly ingested line.

        Args:
            line_id: The monotonic LineID (0, 1, 2, ...).
            epoch_seconds: Timestamp in Unix epoch seconds.
            level: Log level ("INFO", "WARN", "ERROR").
            service: Service name ("payments", "auth", etc.).
        """
        # 1. Detect any out-of-order timestamps.
        if line_id > 0 and epoch_seconds < self._last_timestamp:
            self.out_of_order_count += 1
        self._last_timestamp = epoch_seconds

        # 2. Append timestamp to parallel array.
        self.timestamps.append(epoch_seconds)

        # 3. Add to level postings and level code array.
        upper_level = level.upper()
        if upper_level not in self.level_postings:
            self.level_postings[upper_level] = array("I")
        self.level_postings[upper_level].append(line_id)

        code_map = {"INFO": 0, "WARN": 1, "ERROR": 2}
        self.level_codes.append(code_map.get(upper_level, 0))

        # 4. Add to service postings.
        lower_service = service.lower()
        if lower_service not in self.service_postings:
            self.service_postings[lower_service] = array("I")
        self.service_postings[lower_service].append(line_id)

    def time_range_to_id_range(
        self, from_epoch: Optional[int] = None, to_epoch: Optional[int] = None
    ) -> Tuple[int, int]:
        """Convert a time range into an inclusive LineID range [min_id, max_id].

        Boundaries are INCLUSIVE:
          - from_epoch: first line with timestamp >= from_epoch
          - to_epoch: last line with timestamp <= to_epoch

        If the range is empty or invalid, returns (min_id, max_id) where min_id > max_id.

        Algorithm:
          Uses `bisect_left` for lower boundary and `bisect_right` for upper boundary.
          Time complexity: O(log N).

        Args:
            from_epoch: Optional starting Unix epoch seconds (inclusive).
            to_epoch: Optional ending Unix epoch seconds (inclusive).

        Returns:
            Tuple of (min_id, max_id) inclusive.
        """
        total = len(self.timestamps)
        if total == 0:
            return 0, -1

        # Determine lower boundary
        if from_epoch is None:
            min_id = 0
        else:
            min_id = bisect.bisect_left(self.timestamps, from_epoch)

        # Determine upper boundary
        if to_epoch is None:
            max_id = total - 1
        else:
            # bisect_right returns insertion point after any elements equal to to_epoch.
            # Subtract 1 to get the last inclusive element index.
            max_id = bisect.bisect_right(self.timestamps, to_epoch) - 1

        return min_id, max_id

    def get_level_postings(self, level: str) -> array:
        """Return the sorted Line ID array for a given level."""
        return self.level_postings.get(level.upper(), array("I"))

    def get_service_postings(self, service: str) -> array:
        """Return the sorted Line ID array for a given service."""
        return self.service_postings.get(service.lower(), array("I"))

    def get_all_services(self) -> List[str]:
        """Return list of all indexed service names."""
        return sorted(self.service_postings.keys())

    def get_level_for_id(self, line_id: int) -> str:
        """Return the level name for a given LineID."""
        code_to_name = {0: "INFO", 1: "WARN", 2: "ERROR"}
        if line_id < 0 or line_id >= len(self.level_codes):
            return "UNKNOWN"
        return code_to_name.get(self.level_codes[line_id], "UNKNOWN")

    def memory_bytes(self) -> int:
        """Estimate total memory occupied by structured arrays."""
        mem = self.timestamps.buffer_info()[1] * self.timestamps.itemsize
        mem += self.level_codes.buffer_info()[1] * self.level_codes.itemsize
        for arr in self.level_postings.values():
            mem += arr.buffer_info()[1] * arr.itemsize
        for arr in self.service_postings.values():
            mem += arr.buffer_info()[1] * arr.itemsize
        return mem
