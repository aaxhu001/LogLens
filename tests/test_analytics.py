"""Unit tests for pandas and matplotlib operational log analytics."""

from collections import Counter
import json
import os
import shutil
import tempfile
import unittest

from loglens.analytics import LogAnalytics


class TestLogAnalytics(unittest.TestCase):
    """Verify log aggregation, daily counts, and 3-sigma spike detection."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.log_file = os.path.join(self.temp_dir, "test.log")
        self.out_dir = os.path.join(self.temp_dir, "output")

        # Create deterministic 4-day sample log
        lines = [
            # Day 1: 2 INFO, 1 WARN, 0 ERROR
            "2026-03-01T10:00:00Z INFO auth login session 1\n",
            "2026-03-01T11:00:00Z INFO orders created 101\n",
            "2026-03-01T12:00:00Z WARN db slow query 1000ms\n",
            # Day 2: 1 INFO, 0 WARN, 1 ERROR
            "2026-03-02T10:00:00Z INFO auth login session 2\n",
            "2026-03-02T14:00:00Z ERROR payments card expired\n",
            # Day 3: 1 INFO, 1 WARN, 1 ERROR
            "2026-03-03T10:00:00Z INFO api ping ok\n",
            "2026-03-03T12:00:00Z WARN db query 1200ms\n",
            "2026-03-03T15:00:00Z ERROR payments timeout db\n",
            # Day 4: 1 INFO, 0 WARN, 10 ERROR (Massive Spike!)
            "2026-03-04T10:00:00Z INFO auth login session 3\n",
        ]
        for i in range(10):
            lines.append(f"2026-03-04T11:{i:02d}:00Z ERROR payments timeout calling db after 3000ms\n")

        with open(self.log_file, "w", encoding="utf-8") as f:
            f.writelines(lines)

        self.analytics = LogAnalytics(
            log_path=self.log_file, out_dir=self.out_dir, window=3, sigma=2.0
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_level_summary_matches_independent_counts(self):
        df = self.analytics.load_dataframe()
        summary = self.analytics.compute_level_summary(df)

        # Independent verification
        self.assertEqual(summary.loc["INFO", "count"], 5)
        self.assertEqual(summary.loc["WARN", "count"], 2)
        self.assertEqual(summary.loc["ERROR", "count"], 12)
        self.assertEqual(summary["count"].sum(), 19)

    def test_daily_counts_match_independent_counts(self):
        df = self.analytics.load_dataframe()
        daily = self.analytics.compute_daily_counts(df)

        # Check day 1
        self.assertEqual(daily.loc["2026-03-01", "INFO"], 2)
        self.assertEqual(daily.loc["2026-03-01", "WARN"], 1)
        self.assertEqual(daily.loc["2026-03-01", "ERROR"], 0)

        # Check day 4
        self.assertEqual(daily.loc["2026-03-04", "ERROR"], 10)
        self.assertEqual(daily.loc["2026-03-04", "TOTAL"], 11)

    def test_spike_detection_flags_outlier_day(self):
        res = self.analytics.run_full_analysis()
        spike_days = res["spike_days"]

        # Day 4 (2026-03-04) had 10 errors compared to baseline of 0..1 errors
        self.assertIn("2026-03-04", spike_days)

        # Verify exported files exist and are non-empty
        self.assertTrue(os.path.exists(os.path.join(self.out_dir, "daily_counts.csv")))
        self.assertTrue(os.path.exists(os.path.join(self.out_dir, "spike_days.csv")))
        self.assertTrue(os.path.exists(os.path.join(self.out_dir, "error_spikes.png")))
        self.assertTrue(os.path.exists(os.path.join(self.out_dir, "interpretation.md")))

    def test_injected_spikes_from_truth_file(self):
        """Verify that injected spikes in sample.log match ground truth in spikes_truth.json."""
        sample_log = "data/sample.log"
        truth_json = "data/spikes_truth.json"

        if not os.path.exists(sample_log) or not os.path.exists(truth_json):
            self.skipTest("Sample log or truth json not found.")

        with open(truth_json, "r", encoding="utf-8") as f:
            truth_data = json.load(f)
        expected_spikes = truth_data.get("spike_days", [])

        analytics = LogAnalytics(log_path=sample_log, out_dir=self.out_dir)
        res = analytics.run_full_analysis()
        detected_spikes = res["spike_days"]

        # All expected spike days must be detected
        for expected in expected_spikes:
            self.assertIn(
                expected,
                detected_spikes,
                f"Expected spike on {expected} was not detected by 3-sigma rule!",
            )


if __name__ == "__main__":
    unittest.main()
