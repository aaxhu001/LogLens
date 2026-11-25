"""Log ingestion pipeline for LogLens.

This module coordinates the end-to-end ingestion flow:
  1. Scans log files and reads them in binary mode ('rb') to capture exact byte offsets.
  2. Passes each raw line to `LogParser` for format validation.
  3. Records valid lines into `LineStore` (file ID, byte offset, byte length).
  4. Records structured metadata into `StructuredIndex` (epoch timestamp, level, service).
  5. Extracts alphanumeric tokens via `tokenize()` and indexes them into `InvertedIndex`.
  6. Collects diagnostic metrics into an `IngestReport`:
     - Lines parsed vs. rejected
     - Vocabulary size and total postings
     - Ingestion duration (seconds)
     - Memory footprint (bytes / MB)
     - Out-of-order timestamp count
     - Bounded diagnostic samples of rejected lines
"""

from dataclasses import dataclass, field
import os
import time
from typing import Dict, List, Optional, Union

from loglens.inverted_index import InvertedIndex
from loglens.line_store import LineStore
from loglens.parser import LogParser, ParsedLogLine
from loglens.structured_index import StructuredIndex
from loglens.tokenizer import tokenize


@dataclass
class IngestReport:
    """Diagnostic report summarizing an ingestion run."""
    lines_parsed: int
    lines_rejected: int
    vocabulary_size: int
    total_postings: int
    build_seconds: float
    memory_bytes: int
    memory_mb: float
    out_of_order_count: int
    files_indexed: List[str]
    rejection_samples: List[Dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        """Convert report to dictionary format for JSON reporting."""
        return {
            "lines_parsed": self.lines_parsed,
            "lines_rejected": self.lines_rejected,
            "total_attempted": self.lines_parsed + self.lines_rejected,
            "vocabulary_size": self.vocabulary_size,
            "total_postings": self.total_postings,
            "build_seconds": round(self.build_seconds, 3),
            "memory_mb": round(self.memory_mb, 2),
            "memory_bytes": self.memory_bytes,
            "out_of_order_count": self.out_of_order_count,
            "files_indexed": self.files_indexed,
            "rejection_samples": self.rejection_samples,
        }


class Ingestor:
    """Orchestrates log file parsing, offset tracking, and index construction."""

    def __init__(
        self,
        line_store: Optional[LineStore] = None,
        inverted_index: Optional[InvertedIndex] = None,
        structured_index: Optional[StructuredIndex] = None,
        parser: Optional[LogParser] = None,
    ) -> None:
        """Initialize Ingestor with index targets."""
        self.line_store = line_store if line_store is not None else LineStore()
        self.inverted_index = inverted_index if inverted_index is not None else InvertedIndex()
        self.structured_index = structured_index if structured_index is not None else StructuredIndex()
        self.parser = parser if parser is not None else LogParser()

    def ingest_path(self, target_path: str) -> IngestReport:
        """Ingest a single log file or all .log files in a directory.

        Args:
            target_path: Path to a .log file or directory containing .log files.

        Returns:
            IngestReport with ingestion statistics and performance metrics.

        Raises:
            FileNotFoundError: If target_path does not exist.
        """
        if not os.path.exists(target_path):
            raise FileNotFoundError(f"Target path '{target_path}' does not exist.")

        start_time = time.perf_counter()

        # Discover all target files
        if os.path.isfile(target_path):
            files_to_ingest = [target_path]
        else:
            files_to_ingest = []
            for root, _, files in os.walk(target_path):
                for f in sorted(files):
                    if f.endswith(".log") or f.endswith(".txt"):
                        files_to_ingest.append(os.path.join(root, f))
            files_to_ingest.sort()

        if not files_to_ingest:
            raise ValueError(f"No .log files found in directory '{target_path}'.")

        # Ingest each file sequentially
        for file_path in files_to_ingest:
            self._ingest_single_file(file_path)

        build_seconds = time.perf_counter() - start_time
        mem_bytes = (
            self.line_store.memory_bytes()
            + self.inverted_index.memory_bytes()
            + self.structured_index.memory_bytes()
        )
        mem_mb = mem_bytes / (1024 * 1024)

        parser_summary = self.parser.get_summary()

        return IngestReport(
            lines_parsed=self.line_store.total_lines(),
            lines_rejected=self.parser.total_rejected,
            vocabulary_size=self.inverted_index.vocabulary_size(),
            total_postings=self.inverted_index.total_postings(),
            build_seconds=build_seconds,
            memory_bytes=mem_bytes,
            memory_mb=mem_mb,
            out_of_order_count=self.structured_index.out_of_order_count,
            files_indexed=files_to_ingest,
            rejection_samples=parser_summary["sample_rejections"],  # type: ignore
        )

    def _ingest_single_file(self, file_path: str) -> None:
        """Read a single file in binary mode to track exact byte offsets."""
        file_id = self.line_store.register_file(file_path)

        with open(file_path, "rb") as f:
            line_number = 0
            while True:
                # 1. Capture exact starting byte offset before reading
                byte_offset = f.tell()
                raw_bytes = f.readline()
                if not raw_bytes:
                    break

                line_number += 1
                byte_length = len(raw_bytes)

                # 2. Decode line as UTF-8
                line_str = raw_bytes.decode("utf-8", errors="replace")

                # 3. Parse and validate format
                parsed = self.parser.parse_line(line_str, line_number=line_number)
                if parsed is None:
                    # Malformed line: parser already recorded rejection. Skip indexing!
                    continue

                # 4. Assign monotonic LineID and record disk location in LineStore
                line_id = self.line_store.add_line(
                    file_id=file_id,
                    byte_offset=byte_offset,
                    byte_length=byte_length,
                )

                # 5. Index structured attributes
                self.structured_index.add_entry(
                    line_id=line_id,
                    epoch_seconds=parsed.epoch_seconds,
                    level=parsed.level,
                    service=parsed.service,
                )

                # 6. Tokenize message and index into InvertedIndex
                tokens = tokenize(parsed.message)
                self.inverted_index.add_line_tokens(line_id=line_id, tokens=tokens)
