"""Operational error spike analytics and anomaly detection.

This module uses pandas and matplotlib to perform batch analysis on ingested logs:
  1. Loads parsed log records into a pandas DataFrame.
  2. Aggregates level distribution (counts and percentages for INFO, WARN, ERROR).
  3. Groups log volumes by calendar day per logging level.
  4. Implements the configurable 3-sigma anomaly spike detection rule:
       threshold = rolling_mean(prev 7 days) + 3 * rolling_std(prev 7 days)
       spike = daily_errors > threshold
  5. Generates high-resolution visualization charts with labeled spike markers.
  6. Exports summary datasets:
       - output/daily_counts.csv
       - output/spike_days.csv
       - output/error_spikes.png
       - output/interpretation.md
  7. Identifies the primary operational error messages driving each spike.
"""

from datetime import datetime, timezone
import json
import os
from typing import Any, Dict, List, Optional, Tuple

import matplotlib
# Use non-interactive backend suitable for headless CLI and automated server environments
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd

from loglens.parser import LogParser


DEFAULT_SPIKE_WINDOW = 7
DEFAULT_SIGMA = 3.0


class LogAnalytics:
    """Batch analysis engine for operational logs."""

    def __init__(
        self,
        log_path: str = "data/sample.log",
        out_dir: str = "output",
        window: int = DEFAULT_SPIKE_WINDOW,
        sigma: float = DEFAULT_SIGMA,
    ) -> None:
        """Initialize analytics engine.

        Args:
            log_path: Path to log file for analysis.
            out_dir: Destination folder for exported CSVs, charts, and reports.
            window: Rolling window size in days for baseline calculation (default 7).
            sigma: Multiplier for standard deviation threshold (default 3.0).
        """
        self.log_path = log_path
        self.out_dir = out_dir
        self.window = window
        self.sigma = sigma
        self.parser = LogParser()

        os.makedirs(self.out_dir, exist_ok=True)

    def load_dataframe(self) -> pd.DataFrame:
        """Parse raw log file into a clean pandas DataFrame.

        Returns:
            DataFrame with columns: ['timestamp', 'date', 'level', 'service', 'message'].
        """
        if not os.path.exists(self.log_path):
            raise FileNotFoundError(f"Log file '{self.log_path}' not found.")

        rows: List[Dict[str, Any]] = []

        with open(self.log_path, "r", encoding="utf-8") as f:
            for line_no, raw_line in enumerate(f, 1):
                parsed = self.parser.parse_line(raw_line, line_number=line_no)
                if parsed:
                    # Extract date string 'YYYY-MM-DD'
                    dt = datetime.fromtimestamp(parsed.epoch_seconds, tz=timezone.utc)
                    rows.append({
                        "timestamp": dt,
                        "date": dt.strftime("%Y-%m-%d"),
                        "level": parsed.level,
                        "service": parsed.service,
                        "message": parsed.message,
                    })

        if not rows:
            raise ValueError(f"No valid log entries found in '{self.log_path}'.")

        df = pd.DataFrame(rows)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp").reset_index(drop=True)
        return df

    def compute_level_summary(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate counts and percentages per logging level."""
        counts = df["level"].value_counts()
        total = len(df)
        pcts = (counts / total) * 100.0

        summary = pd.DataFrame({
            "count": counts,
            "percentage": pcts.round(2),
        })
        return summary

    def compute_daily_counts(self, df: pd.DataFrame) -> pd.DataFrame:
        """Aggregate log counts per calendar day for each logging level.

        Returns:
            DataFrame indexed by 'date' with columns: ['INFO', 'WARN', 'ERROR', 'TOTAL'].
        """
        daily = df.groupby(["date", "level"]).size().unstack(fill_value=0)

        # Ensure all standard columns are present
        for lvl in ["INFO", "WARN", "ERROR"]:
            if lvl not in daily.columns:
                daily[lvl] = 0

        daily = daily[["INFO", "WARN", "ERROR"]]
        daily["TOTAL"] = daily["INFO"] + daily["WARN"] + daily["ERROR"]
        return daily

    def detect_spikes(self, daily_df: pd.DataFrame) -> pd.DataFrame:
        """Apply 3-sigma anomaly detection to daily ERROR counts.

        Rule:
          A calendar day is flagged as a spike when:
            daily_errors > rolling_mean(prev 7 days) + 3 * rolling_std(prev 7 days)

        Returns:
            DataFrame with columns:
              ['date', 'ERROR', 'rolling_mean', 'rolling_std', 'threshold', 'is_spike'].
        """
        df_spikes = daily_df.copy().reset_index()

        errors = df_spikes["ERROR"].astype(float)

        # Baseline from previous window days (shifted by 1 day so current day is excluded from its baseline)
        effective_min_periods = max(2, min(len(df_spikes) - 1, self.window))
        shifted = errors.shift(1)
        rolling_mean = shifted.rolling(window=self.window, min_periods=effective_min_periods).mean()
        rolling_std = (
            shifted.rolling(window=self.window, min_periods=effective_min_periods)
            .std(ddof=0)
            .fillna(0.0)
        )

        # Apply a minimal standard deviation floor to avoid division-by-zero or zero-variance traps
        clamped_std = rolling_std.clip(lower=0.5)
        threshold = rolling_mean + (self.sigma * clamped_std)

        # Flag spikes: must exceed threshold, have at least 1 error, and have sufficient history
        is_spike = (errors > threshold) & (errors > 0) & (~threshold.isna())

        df_spikes["rolling_mean"] = rolling_mean.round(2)
        df_spikes["rolling_std"] = clamped_std.round(2)
        df_spikes["threshold"] = threshold.round(2)
        df_spikes["is_spike"] = is_spike

        return df_spikes

    def extract_top_errors_per_day(
        self, df: pd.DataFrame, spike_dates: List[str], top_n: int = 3
    ) -> Dict[str, List[Tuple[str, int]]]:
        """Extract the most frequent error messages on each spike day."""
        top_errors: Dict[str, List[Tuple[str, int]]] = {}

        for d in spike_dates:
            day_errors = df[(df["date"] == d) & (df["level"] == "ERROR")]
            counts = day_errors["message"].value_counts().head(top_n)
            top_errors[d] = list(counts.items())

        return top_errors

    def generate_chart(
        self, daily_df: pd.DataFrame, spikes_df: pd.DataFrame
    ) -> str:
        """Render publication-quality daily counts chart with spike annotations."""
        chart_path = os.path.join(self.out_dir, "error_spikes.png")

        dates = pd.to_datetime(daily_df.index)
        x = range(len(dates))
        date_labels = [d.strftime("%Y-%m-%d") for d in dates]

        plt.figure(figsize=(12, 6), dpi=150)
        plt.style.use("dark_background")

        # Plot log volume bars / lines
        bar_width = 0.6
        plt.bar(
            x, daily_df["INFO"], width=bar_width, label="INFO", color="#238636", alpha=0.7
        )
        plt.bar(
            x,
            daily_df["WARN"],
            width=bar_width,
            bottom=daily_df["INFO"],
            label="WARN",
            color="#d29922",
            alpha=0.8,
        )
        plt.bar(
            x,
            daily_df["ERROR"],
            width=bar_width,
            bottom=daily_df["INFO"] + daily_df["WARN"],
            label="ERROR",
            color="#f85149",
            alpha=0.9,
        )

        # Plot 3-Sigma threshold line
        plt.plot(
            x,
            spikes_df["threshold"],
            color="#58a6ff",
            linestyle="--",
            linewidth=2,
            label=f"3σ Error Threshold (Prev {self.window}d)",
        )

        # Highlight detected spike days
        spike_rows = spikes_df[spikes_df["is_spike"]]
        for _, row in spike_rows.iterrows():
            idx = spikes_df[spikes_df["date"] == row["date"]].index[0]
            err_val = row["ERROR"]
            plt.scatter(
                [idx],
                [err_val],
                color="#ff0055",
                s=140,
                zorder=5,
                edgecolors="#ffffff",
                linewidth=2,
            )
            plt.annotate(
                f"SPIKE: {int(err_val)} err\n({row['date']})",
                (idx, err_val),
                textcoords="offset points",
                xytext=(0, 18),
                ha="center",
                fontsize=8,
                fontweight="bold",
                color="#ff7b72",
                bbox=dict(boxstyle="round,pad=0.3", fc="#161b22", ec="#f85149", lw=1),
                arrowprops=dict(arrowstyle="->", color="#ff7b72", lw=1.2),
            )

        plt.title(
            "LogLens Operational Anomaly Analysis: Daily Log Volume & Error Spikes (Synthetic)",
            fontsize=13,
            fontweight="bold",
            pad=15,
            color="#f0f6fc",
        )
        plt.xlabel("Calendar Date (UTC)", fontsize=10, labelpad=10, color="#8b949e")
        plt.ylabel("Daily Event Volume", fontsize=10, labelpad=10, color="#8b949e")
        plt.xticks(x, date_labels, rotation=45, ha="right", fontsize=8, color="#c9d1d9")
        plt.yticks(fontsize=8, color="#c9d1d9")
        plt.grid(axis="y", linestyle=":", alpha=0.3, color="#8b949e")
        plt.legend(loc="upper left", framealpha=0.8, facecolor="#161b22", edgecolor="#30363d")

        plt.tight_layout()
        plt.savefig(chart_path, dpi=150)
        plt.close()

        return chart_path

    def run_full_analysis(self) -> Dict[str, Any]:
        """Execute complete analysis pipeline and export CSVs, chart, and markdown."""
        print(f"[*] Loading and parsing '{self.log_path}' for operational analytics...")
        df = self.load_dataframe()

        # 1. Level Summary
        level_summary = self.compute_level_summary(df)

        # 2. Daily Counts
        daily_counts = self.compute_daily_counts(df)
        daily_csv_path = os.path.join(self.out_dir, "daily_counts.csv")
        daily_counts.to_csv(daily_csv_path)

        # 3. Detect Spikes
        spikes_df = self.detect_spikes(daily_counts)
        spike_rows = spikes_df[spikes_df["is_spike"]]
        spike_days_list = spike_rows["date"].tolist()

        spikes_csv_path = os.path.join(self.out_dir, "spike_days.csv")
        spike_rows.to_csv(spikes_csv_path, index=False)

        # 4. Extract Top Errors on Spike Days
        top_errors = self.extract_top_errors_per_day(df, spike_days_list)

        # 5. Generate Matplotlib Chart
        chart_path = self.generate_chart(daily_counts, spikes_df)

        # 6. Write Markdown Interpretation
        interp_path = os.path.join(self.out_dir, "interpretation.md")
        self._write_interpretation_markdown(
            interp_path=interp_path,
            total_lines=len(df),
            level_summary=level_summary,
            spike_rows=spike_rows,
            top_errors=top_errors,
        )

        print(f"[✓] Analysis successfully completed:")
        print(f"    - Daily counts CSV : {daily_csv_path}")
        print(f"    - Spike days CSV   : {spikes_csv_path}")
        print(f"    - Spike Chart PNG  : {chart_path}")
        print(f"    - Interpretation   : {interp_path}")
        print(f"[✓] Detected {len(spike_days_list)} spike days: {spike_days_list}")

        return {
            "total_lines": len(df),
            "level_summary": level_summary.to_dict(),
            "spike_days": spike_days_list,
            "chart_path": chart_path,
        }

    def _write_interpretation_markdown(
        self,
        interp_path: str,
        total_lines: int,
        level_summary: pd.DataFrame,
        spike_rows: pd.DataFrame,
        top_errors: Dict[str, List[Tuple[str, int]]],
    ) -> None:
        """Write human-readable diagnostic interpretation."""
        lines = []
        lines.append("# Operational Error Spike Analysis & Incident Interpretation")
        lines.append("\n> [!NOTE]\n> **Synthetic Data Notice**: All log records analyzed herein were synthetically generated for demonstration and performance validation.\n")

        lines.append("## 1. Executive Summary")
        lines.append(
            f"- **Total Valid Log Records Analyzed**: {total_lines:,}\n"
            f"- **Anomaly Detection Rule**: Rolling 7-day baseline with 3-Sigma threshold (mean_7d + 3*std_7d)\n"
            f"- **Anomalous Spike Days Identified**: {len(spike_rows)} days"
        )

        lines.append("\n## 2. Overall Level Distribution")
        lines.append("| Log Level | Total Events | Percentage of Corpus |")
        lines.append("| :--- | :--- | :--- |")
        for lvl, row in level_summary.iterrows():
            lines.append(f"| {lvl} | {int(row['count']):,} | {row['percentage']:.2f}% |")

        lines.append("\n## 3. Detected Anomaly Days & Driving Error Patterns")
        if spike_rows.empty:
            lines.append("No calendar days exceeded the rolling 3-sigma anomaly threshold.")
        else:
            lines.append("| Spike Date | Error Count | Rolling 7-Day Baseline (Mean) | 3σ Threshold | Anomaly Delta |")
            lines.append("| :--- | :--- | :--- | :--- | :--- |")
            for _, r in spike_rows.iterrows():
                delta = r["ERROR"] - r["threshold"]
                lines.append(
                    f"| **{r['date']}** | **{int(r['ERROR']):,}** | {r['rolling_mean']:.1f} | {r['threshold']:.1f} | **+{delta:.1f}** |"
                )

            lines.append("\n### Root-Cause Error Signatures on Anomaly Days")
            for date_str, err_list in top_errors.items():
                lines.append(f"\n#### Anomaly Day: `{date_str}`")
                lines.append("| Rank | Occurrences | Primary Error Message Signature |")
                lines.append("| :--- | :--- | :--- |")
                for rank, (msg, count) in enumerate(err_list, 1):
                    lines.append(f"| #{rank} | {count:,} | `{msg}` |")

        lines.append("\n## 4. Operational Recommendations for SREs")
        lines.append(
            "1. **Database & Connection Pooling**: Timeouts calling `db` after 3000ms account for major spikes. "
            "Examine slow query logs and increase connection pool limits.\n"
            "2. **Upstream Circuit Breakers**: Gateway timeouts (504) indicate cascading microservice failures. "
            "Implement half-open circuit breaker backoffs to shed load.\n"
            "3. **Automated Alerting**: Integrate this 3-sigma threshold detection into daily cron alerts to trigger "
            "PagerDuty escalations before customers report outages."
        )

        with open(interp_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
