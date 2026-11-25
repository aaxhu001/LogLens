"""Unit tests for the LogLens log parser and validation engine."""

import unittest
from datetime import datetime, timezone
from loglens.parser import LogParser, DEFAULT_LOG_PATTERN


class TestLogParser(unittest.TestCase):
    """Verify parsing correctness, validation rules, and rejection accounting."""

    def setUp(self):
        self.parser = LogParser()

    def test_parse_valid_info_line(self):
        line = "2026-03-01T10:15:32Z INFO auth user logged in successfully session 12345"
        record = self.parser.parse_line(line, line_number=1)

        self.assertIsNotNone(record)
        self.assertEqual(record.timestamp_str, "2026-03-01T10:15:32Z")
        self.assertEqual(record.level, "INFO")
        self.assertEqual(record.service, "auth")
        self.assertEqual(record.message, "user logged in successfully session 12345")
        self.assertEqual(record.raw_line, line)

        # Verify epoch calculation: 2026-03-01T10:15:32Z
        expected_epoch = int(
            datetime(2026, 3, 1, 10, 15, 32, tzinfo=timezone.utc).timestamp()
        )
        self.assertEqual(record.epoch_seconds, expected_epoch)

    def test_parse_valid_error_line_with_latency(self):
        line = "2026-03-01T10:15:32Z ERROR payments timeout calling db after 3000ms"
        record = self.parser.parse_line(line, line_number=2)

        self.assertIsNotNone(record)
        self.assertEqual(record.level, "ERROR")
        self.assertEqual(record.service, "payments")
        self.assertEqual(record.message, "timeout calling db after 3000ms")

    def test_parse_valid_warn_line(self):
        line = "2026-03-02T12:00:00Z WARN db slow query 1500ms"
        record = self.parser.parse_line(line)
        self.assertIsNotNone(record)
        self.assertEqual(record.level, "WARN")

    def test_reject_unrecognized_level(self):
        line = "2026-03-01T10:15:32Z DEBUG payments cache hit"
        record = self.parser.parse_line(line, line_number=3)

        self.assertIsNone(record)
        self.assertEqual(self.parser.total_rejected, 1)
        self.assertIn("Invalid level 'DEBUG'", self.parser.rejected_samples[0].reason)

    def test_reject_missing_timestamp(self):
        line = "ERROR payments database connection failed"
        record = self.parser.parse_line(line, line_number=4)

        self.assertIsNone(record)
        self.assertEqual(self.parser.total_rejected, 1)

    def test_reject_invalid_timestamp(self):
        # February 31st is calendrically impossible
        line = "2026-02-31T10:00:00Z ERROR payments invalid date"
        record = self.parser.parse_line(line, line_number=5)

        self.assertIsNone(record)
        self.assertEqual(self.parser.total_rejected, 1)
        self.assertIn("Invalid ISO-8601 timestamp", self.parser.rejected_samples[0].reason)

    def test_reject_empty_or_whitespace_line(self):
        self.assertIsNone(self.parser.parse_line("", line_number=6))
        self.assertIsNone(self.parser.parse_line("    \n", line_number=7))
        self.assertEqual(self.parser.total_rejected, 2)

    def test_sample_rejections_quota_bounded(self):
        """Rejection buffer must not exceed max_rejected_samples."""
        parser = LogParser(max_rejected_samples=3)
        for i in range(10):
            parser.parse_line(f"MALFORMED LINE {i}", line_number=i + 1)

        self.assertEqual(parser.total_rejected, 10)
        self.assertEqual(len(parser.rejected_samples), 3)

    def test_summary_and_reset(self):
        self.parser.parse_line("2026-03-01T10:00:00Z INFO auth ok")
        self.parser.parse_line("INVALID LINE")

        summary = self.parser.get_summary()
        self.assertEqual(summary["total_attempted"], 2)
        self.assertEqual(summary["total_parsed"], 1)
        self.assertEqual(summary["total_rejected"], 1)

        self.parser.reset()
        self.assertEqual(self.parser.total_attempted, 0)
        self.assertEqual(self.parser.total_parsed, 0)
        self.assertEqual(self.parser.total_rejected, 0)
        self.assertEqual(len(self.parser.rejected_samples), 0)

    def test_custom_regex_configuration(self):
        """Parser must support custom regular expressions."""
        # Custom format: [LEVEL] [SERVICE] TIMESTAMP MESSAGE
        custom_pattern = (
            r"^\[(?P<level>INFO|WARN|ERROR)\]\s+"
            r"\[(?P<service>[a-z]+)\]\s+"
            r"(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s+"
            r"(?P<message>.*)$"
        )
        custom_parser = LogParser(pattern=custom_pattern)
        line = "[ERROR] [billing] 2026-03-01T10:00:00Z credit card declined"
        record = custom_parser.parse_line(line)

        self.assertIsNotNone(record)
        self.assertEqual(record.level, "ERROR")
        self.assertEqual(record.service, "billing")
        self.assertEqual(record.message, "credit card declined")


if __name__ == "__main__":
    unittest.main()
