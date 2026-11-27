"""Compact storage for log lines using binary file offsets.

To index millions of log lines in memory without exhausting RAM, LogLens
does not store raw message text in memory. Instead, `LineStore` maps each
monotonic LineID to a tuple of (file_id, byte_offset, byte_length).

When a query matches line IDs, `LineStore` retrieves the exact lines on
demand directly from disk by seeking to the recorded byte offset.

Memory Efficiency:
  - file_id: unsigned 16-bit int (array('H')), up to 65,535 files (2 bytes/line)
  - byte_offset: unsigned 64-bit int (array('Q')), up to 18 exabytes (8 bytes/line)
  - byte_length: unsigned 32-bit int (array('I')), up to 4 GB lines (4 bytes/line)
  - Total per-line RAM footprint: exactly 14 bytes!
  - 1,000,000 lines = ~14 MB of RAM.
"""

from array import array
import os
from typing import Dict, List, Optional, Tuple


class LineStore:
    """Manages file paths and line byte offsets for fast random disk retrieval."""

    def __init__(self) -> None:
        """Initialize empty LineStore structures."""
        # Mapping from file_path string to integer file_id
        self.file_paths: List[str] = []
        self._path_to_id: Dict[str, int] = {}

        # Parallel compact primitive arrays indexed by LineID (0, 1, 2, ...)
        self.file_ids: array = array("H")
        self.byte_offsets: array = array("Q")
        self.byte_lengths: array = array("I")

    def register_file(self, file_path: str) -> int:
        """Register a file path and return its unique integer file_id.

        Args:
            file_path: Absolute or normalized relative path to the log file.

        Returns:
            The integer file_id assigned to this file.
        """
        normalized = os.path.abspath(file_path)
        if normalized not in self._path_to_id:
            file_id = len(self.file_paths)
            self._path_to_id[normalized] = file_id
            self.file_paths.append(normalized)
            return file_id
        return self._path_to_id[normalized]

    def add_line(self, file_id: int, byte_offset: int, byte_length: int) -> int:
        """Record the location of a new line.

        Line IDs are assigned sequentially in ingestion order (0, 1, 2, ...).

        Args:
            file_id: The ID of the file containing the line.
            byte_offset: The exact starting byte offset in the file.
            byte_length: The length of the line in bytes.

        Returns:
            The assigned LineID.
        """
        line_id = len(self.byte_offsets)
        self.file_ids.append(file_id)
        self.byte_offsets.append(byte_offset)
        self.byte_lengths.append(byte_length)
        return line_id

    def get_line(self, line_id: int) -> str:
        """Fetch the raw text of a log line by its LineID.

        Seeks directly to the byte offset on disk and reads the exact line bytes.

        Args:
            line_id: The LineID to retrieve.

        Returns:
            The decoded raw line string without trailing newline.

        Raises:
            IndexError: If line_id is out of range.
            FileNotFoundError: If the underlying log file was moved or deleted.
        """
        if line_id < 0 or line_id >= len(self.byte_offsets):
            raise IndexError(f"LineID {line_id} is out of range (0..{len(self.byte_offsets)-1})")

        file_id = self.file_ids[line_id]
        offset = self.byte_offsets[line_id]
        length = self.byte_lengths[line_id]
        file_path = self.file_paths[file_id]

        # Portability fallback: if stored path does not exist on this host, check relative locations
        if not os.path.exists(file_path):
            basename = os.path.basename(file_path)
            candidate_paths = [
                os.path.join(os.getcwd(), "data", basename),
                os.path.join(os.getcwd(), basename),
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", basename),
            ]
            for candidate in candidate_paths:
                normalized_candidate = os.path.abspath(candidate)
                if os.path.exists(normalized_candidate):
                    file_path = normalized_candidate
                    self.file_paths[file_id] = file_path
                    break

        with open(file_path, "rb") as f:
            f.seek(offset)
            raw_bytes = f.read(length)

        # Decode using UTF-8 with replacement for any unexpected binary bytes.
        decoded = raw_bytes.decode("utf-8", errors="replace")
        return decoded.rstrip("\r\n")

    def get_context(
        self, line_id: int, window: int = 3
    ) -> List[Tuple[int, str, bool]]:
        """Retrieve neighboring log lines for context inspection.

        Args:
            line_id: The target LineID.
            window: Number of lines before and after to include.

        Returns:
            A list of tuples: (neighbour_line_id, line_text, is_target_line).
        """
        total = len(self.byte_offsets)
        if line_id < 0 or line_id >= total:
            raise IndexError(f"LineID {line_id} is out of range (0..{total-1})")

        start_id = max(0, line_id - window)
        end_id = min(total - 1, line_id + window)

        context: List[Tuple[int, str, bool]] = []
        for curr_id in range(start_id, end_id + 1):
            text = self.get_line(curr_id)
            context.append((curr_id, text, curr_id == line_id))

        return context

    def total_lines(self) -> int:
        """Return the total number of lines indexed."""
        return len(self.byte_offsets)

    def memory_bytes(self) -> int:
        """Estimate the memory in bytes used by the offset arrays."""
        return (
            self.file_ids.buffer_info()[1] * self.file_ids.itemsize
            + self.byte_offsets.buffer_info()[1] * self.byte_offsets.itemsize
            + self.byte_lengths.buffer_info()[1] * self.byte_lengths.itemsize
        )
