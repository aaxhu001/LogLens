"""Unit tests for the canonical LogLens tokenizer."""

import unittest
from loglens.tokenizer import tokenize


class TestTokenizer(unittest.TestCase):
    """Verify tokenizer normalization, token preservation, and boundary handling."""

    def test_basic_tokenization(self):
        text = "timeout calling db after 3000ms"
        expected = ["timeout", "calling", "db", "after", "3000ms"]
        self.assertEqual(tokenize(text), expected)

    def test_lowercasing(self):
        text = "ERROR Payments TIMEOUT"
        expected = ["error", "payments", "timeout"]
        self.assertEqual(tokenize(text), expected)

    def test_combined_alphanumeric_preservation(self):
        """Tokens like 3000ms, v2, client42, port8080 must remain single tokens."""
        text = "latency=3000ms on port8080 using api_v2 for client42"
        expected = ["latency", "3000ms", "on", "port8080", "using", "api", "v2", "for", "client42"]
        self.assertEqual(tokenize(text), expected)

    def test_punctuation_and_delimiters(self):
        """All non-alphanumeric punctuation acts as token separators."""
        text = '{"status": 500, "msg": "failed/aborted: [db-error.v1]"}'
        expected = ["status", "500", "msg", "failed", "aborted", "db", "error", "v1"]
        self.assertEqual(tokenize(text), expected)

    def test_ip_addresses(self):
        text = "host 192.168.1.100 connected"
        expected = ["host", "192", "168", "1", "100", "connected"]
        self.assertEqual(tokenize(text), expected)

    def test_empty_and_whitespace(self):
        self.assertEqual(tokenize(""), [])
        self.assertEqual(tokenize("   \t\n  "), [])
        self.assertEqual(tokenize("!@#$%^&*()_+=-~`"), [])

    def test_query_and_log_consistency(self):
        """Ensure search query tokens match indexed message tokens."""
        log_message = "timeout calling db after 3000ms"
        user_query = "TIMEOUT 3000MS"
        log_tokens = set(tokenize(log_message))
        query_tokens = tokenize(user_query)

        for q in query_tokens:
            self.assertIn(q, log_tokens)


if __name__ == "__main__":
    unittest.main()
