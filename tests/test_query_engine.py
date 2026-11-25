"""Unit tests for QueryParser and QueryEngine."""

import os
import shutil
import tempfile
import unittest

from loglens.ingestor import Ingestor
from loglens.query_engine import QueryEngine
from loglens.query_parser import QueryParser, QuerySyntaxError


class TestQueryParser(unittest.TestCase):
    """Verify syntax parsing, boolean operator extraction, and error handling."""

    def test_default_and_parsing(self):
        query = QueryParser.parse("timeout database")
        self.assertEqual(query.and_tokens, ["timeout", "database"])
        self.assertEqual(query.or_tokens, [])
        self.assertEqual(query.not_tokens, [])

    def test_explicit_or_parsing(self):
        query = QueryParser.parse("timeout OR deadlock")
        self.assertIn("timeout", query.or_tokens)
        self.assertIn("deadlock", query.or_tokens)
        self.assertEqual(query.and_tokens, [])

    def test_explicit_not_parsing(self):
        query = QueryParser.parse("timeout NOT retry")
        self.assertEqual(query.and_tokens, ["timeout"])
        self.assertEqual(query.not_tokens, ["retry"])

    def test_prefix_negation_parsing(self):
        query = QueryParser.parse("timeout -retry")
        self.assertEqual(query.and_tokens, ["timeout"])
        self.assertEqual(query.not_tokens, ["retry"])

    def test_structured_filters_inline(self):
        query = QueryParser.parse("level:ERROR service:payments timeout")
        self.assertEqual(query.level, "ERROR")
        self.assertEqual(query.service, "payments")
        self.assertEqual(query.and_tokens, ["timeout"])

    def test_invalid_filter_raises_with_example(self):
        with self.assertRaises(QuerySyntaxError) as ctx:
            QueryParser.parse("foo:bar timeout")
        err_msg = str(ctx.exception)
        self.assertIn("Unknown filter 'foo'", err_msg)
        self.assertIn("Valid Example:", err_msg)

    def test_invalid_level_raises(self):
        with self.assertRaises(QuerySyntaxError) as ctx:
            QueryParser.parse("level:CRITICAL")
        self.assertIn("Invalid level 'CRITICAL'", str(ctx.exception))

    def test_dangling_not_raises(self):
        with self.assertRaises(QuerySyntaxError) as ctx:
            QueryParser.parse("timeout NOT")
        self.assertIn("Dangling 'NOT' operator", str(ctx.exception))


class TestQueryEngineExecution(unittest.TestCase):
    """Verify query engine search execution, filtering, and pagination."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.log_file = os.path.join(self.temp_dir, "test.log")
        lines = [
            "2026-03-01T10:00:00Z INFO auth user logged in session 1\n",
            "2026-03-01T10:01:00Z WARN payments slow database query 1500ms\n",
            "2026-03-01T10:02:00Z ERROR payments timeout calling db after 3000ms\n",
            "2026-03-01T10:03:00Z ERROR auth signature mismatch invalid token\n",
            "2026-03-01T10:04:00Z ERROR payments connection refused retry 1\n",
            "2026-03-01T10:05:00Z INFO orders order created successfully id 500\n",
        ]
        with open(self.log_file, "w", encoding="utf-8") as f:
            f.writelines(lines)

        self.ingestor = Ingestor()
        self.ingestor.ingest_path(self.log_file)
        self.engine = QueryEngine(
            self.ingestor.line_store,
            self.ingestor.inverted_index,
            self.ingestor.structured_index,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_single_token_search(self):
        resp = self.engine.search("timeout")
        self.assertEqual(resp.total_matches, 1)
        self.assertEqual(resp.lines[0]["level"], "ERROR")
        self.assertEqual(resp.lines[0]["service"], "payments")

    def test_multi_token_and_search(self):
        resp = self.engine.search("timeout calling db")
        self.assertEqual(resp.total_matches, 1)
        self.assertEqual(resp.lines[0]["line_id"], 2)

    def test_or_search(self):
        resp = self.engine.search("timeout OR refused")
        self.assertEqual(resp.total_matches, 2)
        matched_ids = [line["line_id"] for line in resp.lines]
        self.assertEqual(sorted(matched_ids), [2, 4])

    def test_not_search(self):
        # All ERROR lines except those with 'timeout'
        resp = self.engine.search("level:ERROR NOT timeout")
        self.assertEqual(resp.total_matches, 2)
        matched_ids = [line["line_id"] for line in resp.lines]
        self.assertEqual(sorted(matched_ids), [3, 4])

    def test_level_and_service_filters(self):
        resp = self.engine.search("level:ERROR service:auth")
        self.assertEqual(resp.total_matches, 1)
        self.assertEqual(resp.lines[0]["message"], "signature mismatch invalid token")

    def test_time_range_filter(self):
        resp = self.engine.search(
            query="",
            from_time="2026-03-01T10:01:00Z",
            to_time="2026-03-01T10:03:00Z",
        )
        self.assertEqual(resp.total_matches, 3)  # Lines 1, 2, 3
        matched_ids = [line["line_id"] for line in resp.lines]
        self.assertEqual(matched_ids, [1, 2, 3])

    def test_pagination(self):
        # Query matching 3 ERROR lines
        resp_p1 = self.engine.search("level:ERROR", page=1, page_size=2)
        self.assertEqual(len(resp_p1.lines), 2)
        self.assertEqual(resp_p1.total_matches, 3)
        self.assertEqual(resp_p1.total_pages, 2)

        resp_p2 = self.engine.search("level:ERROR", page=2, page_size=2)
        self.assertEqual(len(resp_p2.lines), 1)

    def test_level_counts_aggregation(self):
        # Query without filters matches all 6 lines (2 INFO, 1 WARN, 3 ERROR)
        resp = self.engine.search("")
        self.assertEqual(resp.total_matches, 6)
        self.assertEqual(resp.level_counts["INFO"], 2)
        self.assertEqual(resp.level_counts["WARN"], 1)
        self.assertEqual(resp.level_counts["ERROR"], 3)


if __name__ == "__main__":
    unittest.main()
