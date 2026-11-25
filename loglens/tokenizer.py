"""Canonical text tokenizer for LogLens.

This module provides a single, uniform tokenization function used for BOTH
log message indexing and user search query parsing. Sharing the identical
tokenizer guarantees that search terms will match indexed tokens exactly,
preventing search misses caused by inconsistent splitting rules.

Design & Splitting Rules:
1. Normalization: All text is converted to lowercase so search is case-insensitive.
2. Alphanumeric Preservation: Sequences of alphanumeric characters ([a-zA-Z0-9]+)
   are extracted as distinct tokens.
3. Alphanumeric combinations: Units and codes such as '3000ms', 'v2', 'port8080',
   or 'err500' contain both numbers and letters without delimiters, so they are
   retained as intact single tokens.
4. Delimiters: All non-alphanumeric symbols (punctuation, spaces, brackets, quotes,
   slashes, colons, hyphens) serve as token boundaries and are discarded.
"""

import re
from typing import List

# Pre-compile the regex pattern for maximum performance during bulk ingest.
# [a-zA-Z0-9]+ extracts contiguous runs of ASCII letters and digits.
_TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9]+")


def tokenize(text: str) -> List[str]:
    """Extract lowercased alphanumeric tokens from an input string.

    Args:
        text: The raw string to tokenize (log message or search query).

    Returns:
        A list of string tokens in order of appearance.

    Examples:
        >>> tokenize("timeout calling db after 3000ms")
        ['timeout', 'calling', 'db', 'after', '3000ms']
        >>> tokenize("ERROR: payments [code=404]")
        ['error', 'payments', 'code', '404']
    """
    if not text:
        return []

    # 1. Lowercase the entire string to ensure case-insensitivity.
    lowered = text.lower()

    # 2. Extract all contiguous alphanumeric character sequences.
    # Non-alphanumeric symbols like punctuation, quotes, and whitespace
    # are naturally excluded and act as delimiters.
    return _TOKEN_PATTERN.findall(lowered)
