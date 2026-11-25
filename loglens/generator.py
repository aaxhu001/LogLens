"""Synthetic log generator for LogLens.

This module generates realistic, structured log streams for benchmarking,
testing, and demonstration.

Key Features:
  - Strict determinism via `--seed`.
  - Configurable line counts (e.g., 1,000 to 1,000,000+ lines).
  - Multi-day spanning (default >= 14 days) with strictly non-decreasing UTC timestamps.
  - Multi-service distribution (payments, auth, db, api, search, orders, etc.).
  - Realistic operational log messages with varied latency and status metadata.
  - Injected error spikes on predefined days with ground truth saved to JSON.
  - A controlled proportion of malformed lines to test parser rejection accounting.
"""

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
import random
from typing import Dict, List, Tuple


SERVICES = [
    "payments",
    "auth",
    "db",
    "api",
    "orders",
    "search",
    "inventory",
    "notifications",
]

NORMAL_LEVEL_WEIGHTS = {
    "INFO": 80,
    "WARN": 15,
    "ERROR": 5,
}

SPIKE_LEVEL_WEIGHTS = {
    "INFO": 45,
    "WARN": 15,
    "ERROR": 40,
}

MESSAGES: Dict[str, List[str]] = {
    "INFO": [
        "user logged in successfully session {token}",
        "order created with id {order_id} total amount {amount}usd",
        "cache refreshed for key {cache_key}",
        "health check ping response 200 ok",
        "processed payment batch size {batch_size}",
        "token verified for client application {client_id}",
        "search query executed in {latency}ms with {results_count} results",
        "background sync worker completed synchronization",
        "kafka event published to partition {partition_id}",
        "database connection pool acquired lease",
    ],
    "WARN": [
        "high memory usage threshold 85% reached in pod {pod_id}",
        "slow database query took {latency}ms on table {table}",
        "retrying request attempt {retry_num} of 3 after transient network failure",
        "circuit breaker half-open state for service {service_target}",
        "deprecated api version v1 called by client {client_id}",
        "rate limit approaching 90% quota for client {client_id}",
        "connection pool utilization at 88% capacity",
        "cache miss rate exceeded 20% in the last 5 minutes",
    ],
    "ERROR": [
        "timeout calling db after 3000ms on table {table}",
        "connection refused on port {port} during upstream dispatch",
        "payment failed card expired or insufficient funds for order {order_id}",
        "authentication signature mismatch for bearer token",
        "deadlock detected on table {table} transaction aborted",
        "cache node unresponsive after {retry_num} retries",
        "fatal unhandled exception in worker thread out of memory",
        "upstream service gateway timeout 504 from {service_target}",
    ],
}

MALFORMED_TEMPLATES = [
    "MALFORMED_LINE_NO_TIMESTAMP ERROR payments connection dropped",
    "{timestamp} INVALID_LEVEL auth user auth failed unexpected status",
    "{timestamp} INFO",  # Missing service and message
    "2026-99-99T99:99:99Z ERROR db invalid calendar date",
    "{timestamp} ERROR payments",  # Missing message
]


def format_message(template: str, rng: random.Random) -> str:
    """Fill placeholder tokens with random but realistic values."""
    return template.format(
        token=rng.randint(100000, 999999),
        order_id=rng.randint(1000, 9999),
        amount=rng.randint(10, 500),
        cache_key=rng.choice(["user_profile", "session_cache", "catalog_item", "rate_limit"]),
        batch_size=rng.randint(10, 200),
        client_id=f"client_{rng.randint(1, 50)}",
        latency=rng.randint(150, 4500),
        results_count=rng.randint(0, 150),
        partition_id=rng.randint(0, 8),
        pod_id=f"pod-{rng.randint(1, 10)}",
        table=rng.choice(["users", "orders", "transactions", "audit_log"]),
        retry_num=rng.randint(1, 3),
        service_target=rng.choice(["inventory", "notifications", "billing"]),
        port=rng.choice([5432, 6379, 8080, 9092]),
    )


def generate_logs(
    num_lines: int = 1000,
    start_date: str = "2026-03-01T00:00:00Z",
    days: int = 14,
    seed: int = 42,
    output_path: str = "data/sample.log",
    spikes_truth_path: str = "data/spikes_truth.json",
    malformed_ratio: float = 0.005,
) -> Tuple[int, List[str]]:
    """Generate deterministic synthetic log data spanning multiple days with injected spikes.

    Args:
        num_lines: Total lines to generate.
        start_date: ISO-8601 UTC string for first timestamp.
        days: Minimum number of days to span across.
        seed: Random seed for deterministic reproducibility.
        output_path: Target log file destination.
        spikes_truth_path: JSON destination for ground truth spike days.
        malformed_ratio: Proportion of lines that are intentionally malformed (e.g. 0.005 = 0.5%).

    Returns:
        A tuple of (total_lines_written, list_of_injected_spike_days).
    """
    rng = random.Random(seed)

    # 1. Ensure parent directory exists.
    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    truth_dir = os.path.dirname(spikes_truth_path)
    if truth_dir:
        os.makedirs(truth_dir, exist_ok=True)

    # 2. Parse starting date.
    start_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
    total_seconds = days * 86400

    # 3. Designate at least 3 spike days.
    # We choose day 4, day 8, and day 12 (0-indexed day offsets: 3, 7, 11).
    spike_offsets = [3, 7, 11]
    # If the user requested fewer days, scale spike offsets accordingly.
    if days < 14:
        spike_offsets = [max(1, days // 4), max(2, days // 2), max(3, (3 * days) // 4)]
        spike_offsets = sorted(list(set(spike_offsets)))

    spike_dates = [
        (start_dt + timedelta(days=offset)).strftime("%Y-%m-%d")
        for offset in spike_offsets
    ]

    # Save ground truth spikes immediately so analysis tests can verify against them.
    truth_payload = {
        "synthetic": True,
        "description": "Ground truth injected anomaly days where error counts exceed 3 sigma",
        "start_date": start_dt.strftime("%Y-%m-%d"),
        "total_days": days,
        "spike_days": spike_dates,
    }
    with open(spikes_truth_path, "w", encoding="utf-8") as f_truth:
        json.dump(truth_payload, f_truth, indent=2)

    # 4. Generate timestamps monotonically.
    # To guarantee timestamps are non-decreasing and span the desired timeframe,
    # we compute time increments proportional to line progress.
    step_seconds = total_seconds / max(num_lines, 1)

    current_seconds = 0.0
    buffer: List[str] = []
    buffer_flush_size = 10000

    with open(output_path, "w", encoding="utf-8", newline="\n") as f_out:
        for line_idx in range(num_lines):
            # Advance time monotonically with slight jitter.
            jitter = rng.uniform(0.5, 1.5)
            current_seconds += step_seconds * jitter
            if current_seconds > total_seconds:
                current_seconds = total_seconds

            current_dt = start_dt + timedelta(seconds=current_seconds)
            current_date_str = current_dt.strftime("%Y-%m-%d")
            ts_str = current_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

            # Check if this line should be intentionally malformed.
            if rng.random() < malformed_ratio:
                template = rng.choice(MALFORMED_TEMPLATES)
                bad_line = template.format(timestamp=ts_str)
                buffer.append(bad_line + "\n")
                continue

            # Determine level distribution based on whether today is an injected spike day.
            if current_date_str in spike_dates:
                weights = SPIKE_LEVEL_WEIGHTS
            else:
                weights = NORMAL_LEVEL_WEIGHTS

            level = rng.choices(
                population=["INFO", "WARN", "ERROR"],
                weights=[weights["INFO"], weights["WARN"], weights["ERROR"]],
                k=1,
            )[0]

            service = rng.choice(SERVICES)
            msg_template = rng.choice(MESSAGES[level])
            msg = format_message(msg_template, rng)

            line = f"{ts_str} {level} {service} {msg}\n"
            buffer.append(line)

            if len(buffer) >= buffer_flush_size:
                f_out.writelines(buffer)
                buffer.clear()

        if buffer:
            f_out.writelines(buffer)
            buffer.clear()

    return num_lines, spike_dates


def main() -> None:
    """CLI entrypoint for standalone synthetic log generation."""
    parser = argparse.ArgumentParser(
        description="LogLens Synthetic Log Generator: generate realistic log data with spikes."
    )
    parser.add_argument(
        "--lines",
        type=int,
        default=1000000,
        help="Number of log lines to generate (default: 1,000,000).",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="data/sample.log",
        help="Path to write the log file (default: data/sample.log).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for deterministic generation (default: 42).",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=14,
        help="Number of days to span (default: 14).",
    )
    parser.add_argument(
        "--spikes-out",
        type=str,
        default="data/spikes_truth.json",
        help="Path to write ground truth spike dates (default: data/spikes_truth.json).",
    )
    parser.add_argument(
        "--malformed-ratio",
        type=float,
        default=0.005,
        help="Ratio of malformed lines to inject (default: 0.005 = 0.5%).",
    )

    args = parser.parse_args()

    print(f"Generating {args.lines:,} lines across {args.days} days into '{args.out}'...")
    total_written, spikes = generate_logs(
        num_lines=args.lines,
        days=args.days,
        seed=args.seed,
        output_path=args.out,
        spikes_truth_path=args.spikes_out,
        malformed_ratio=args.malformed_ratio,
    )
    print(f"Successfully generated {total_written:,} lines.")
    print(f"Injected spike days saved to '{args.spikes_out}': {spikes}")


if __name__ == "__main__":
    main()
