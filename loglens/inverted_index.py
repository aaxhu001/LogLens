"""Inverted index for full-text search over log messages.

This module implements an inverted index mapping lowercased alphanumeric
tokens to sorted arrays of Line IDs:
    token -> array('I', [line_id_1, line_id_2, ...])

Performance Highlights:
  - Line IDs are monotonically increasing, so appending during sequential ingest
    naturally keeps every posting list strictly sorted without sorting overhead.
  - Memory footprint is minimized by using primitive 32-bit unsigned integers
    (array('I')), requiring only 4 bytes per posting.
  - Pre-sorted posting lists enable $O(N)$ linear-merge and smallest-first
    intersections during multi-token queries.
"""

from array import array
from typing import Dict, Iterable, Optional, Set


class InvertedIndex:
    """Token to sorted Line ID posting lists."""

    def __init__(self) -> None:
        """Initialize an empty inverted index."""
        # Mapping: token string -> array('I') of sorted line IDs
        self.postings: Dict[str, array] = {}

    def add_line_tokens(self, line_id: int, tokens: Iterable[str]) -> None:
        """Index the tokens for a single log line.

        Duplicate tokens within the same line are deduplicated using a set so
        that each LineID appears at most once per token posting list.

        Args:
            line_id: The monotonic LineID assigned to this log line.
            tokens: Sequence of tokens extracted by the canonical tokenizer.
        """
        # Deduplicate tokens for this single line.
        unique_tokens: Set[str] = set(tokens)

        for token in unique_tokens:
            if token not in self.postings:
                # Initialize a new 32-bit unsigned integer array.
                self.postings[token] = array("I")
            self.postings[token].append(line_id)

    def get_postings(self, token: str) -> array:
        """Retrieve the sorted posting list for a given token.

        Args:
            token: Lowercased search token.

        Returns:
            array('I') of Line IDs matching the token, or an empty array.
        """
        return self.postings.get(token, array("I"))

    def contains_token(self, token: str) -> bool:
        """Check if a token exists in the vocabulary."""
        return token in self.postings

    def vocabulary_size(self) -> int:
        """Return the number of unique indexed tokens."""
        return len(self.postings)

    def total_postings(self) -> int:
        """Return the aggregate count of all postings across all tokens."""
        return sum(len(p) for p in self.postings.values())

    def memory_bytes(self) -> int:
        """Estimate the total memory occupied by the underlying posting arrays."""
        return sum(
            arr.buffer_info()[1] * arr.itemsize for arr in self.postings.values()
        )
