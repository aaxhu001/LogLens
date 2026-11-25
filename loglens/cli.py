"""Command-line interface for LogLens.

Provides commands to generate synthetic data, ingest log archives, rebuild indices,
execute searches, inspect index statistics, run benchmarks, analyze anomalies, and
launch the HTTP API server.

Usage:
  python -m loglens.cli <command> [options]

Commands:
  generate    Create synthetic log streams with injected error spikes
  ingest      Ingest log files and build persistent inverted/structured indices
  rebuild     Re-ingest logs from scratch and overwrite existing index
  search      Query indexed logs with boolean terms and structured filters
  stats       Display summary statistics of an indexed dataset
  serve       Start the local JSON API and web interface server
  benchmark   Run performance benchmark suite measuring p50/p95 latency
  analyze     Perform 3-sigma error spike analysis and export charts/CSVs
"""

import argparse
import json
import os
import sys
import time
from typing import List, Optional

from loglens.generator import generate_logs
from loglens.ingestor import Ingestor
from loglens.line_store import LineStore
from loglens.persistence import load_index, rebuild_index, save_index
from loglens.query_engine import QueryEngine
from loglens.query_parser import QuerySyntaxError


DEFAULT_INDEX_DIR = ".loglens_index"


def cmd_generate(args: argparse.Namespace) -> None:
    """Handle the 'generate' command."""
    print(f"[*] Generating {args.lines:,} lines across {args.days} days into '{args.out}'...")
    start = time.perf_counter()
    total_written, spikes = generate_logs(
        num_lines=args.lines,
        days=args.days,
        seed=args.seed,
        output_path=args.out,
        spikes_truth_path=args.spikes_out,
        malformed_ratio=args.malformed_ratio,
    )
    elapsed = time.perf_counter() - start
    print(f"[✓] Finished in {elapsed:.2f}s. Total lines: {total_written:,}")
    print(f"[✓] Ground truth spike days written to '{args.spikes_out}': {spikes}")


def cmd_ingest(args: argparse.Namespace) -> None:
    """Handle the 'ingest' command."""
    print(f"[*] Ingesting log data from '{args.path}' into index at '{args.index_dir}'...")
    ingestor = Ingestor()
    try:
        report = ingestor.ingest_path(args.path)
    except Exception as exc:
        print(f"[!] Ingestion failed: {exc}", file=sys.stderr)
        sys.exit(1)

    # Save index structures to disk
    save_index(
        line_store=ingestor.line_store,
        inverted_index=ingestor.inverted_index,
        structured_index=ingestor.structured_index,
        index_dir=args.index_dir,
        extra_metadata=report.to_dict(),
    )

    print("\n" + "=" * 55)
    print("           LOGLENS INGESTION SUMMARY")
    print("=" * 55)
    print(f"Files Indexed       : {len(report.files_indexed)}")
    print(f"Lines Successfully Parsed: {report.lines_parsed:,}")
    print(f"Lines Rejected (Malformed): {report.lines_rejected:,}")
    print(f"Unique Vocabulary Size   : {report.vocabulary_size:,} tokens")
    print(f"Total Postings Tracked   : {report.total_postings:,}")
    print(f"Ingest Duration          : {report.build_seconds:.3f} seconds")
    print(f"Estimated Index RAM      : {report.memory_mb:.2f} MB")
    print(f"Out-of-Order Timestamps  : {report.out_of_order_count}")
    print("=" * 55)

    if report.rejection_samples:
        print("\nDiagnostic Sample of Rejected Lines:")
        for idx, sample in enumerate(report.rejection_samples[:5], 1):
            print(f"  [{idx}] Line {sample.get('line_number')}: {sample.get('reason')}")
            print(f"      Text: {sample.get('raw_line')}")
    print(f"\n[✓] Index successfully persisted to '{args.index_dir}'.")


def cmd_rebuild(args: argparse.Namespace) -> None:
    """Handle the 'rebuild' command."""
    print(f"[*] Rebuilding index at '{args.index_dir}' from '{args.path}'...")
    try:
        _, _, _, report = rebuild_index(source_path=args.path, index_dir=args.index_dir)
        print(f"[✓] Rebuilt {report.lines_parsed:,} lines in {report.build_seconds:.2f}s.")
    except Exception as exc:
        print(f"[!] Rebuild failed: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_search(args: argparse.Namespace) -> None:
    """Handle the 'search' command."""
    if not os.path.exists(args.index_dir):
        print(
            f"[!] Error: Index directory '{args.index_dir}' does not exist.\n"
            f"    Please run 'python -m loglens.cli ingest <path>' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        line_store, inverted_index, structured_index, _ = load_index(args.index_dir)
    except Exception as exc:
        print(f"[!] Failed to load index: {exc}", file=sys.stderr)
        sys.exit(1)

    engine = QueryEngine(line_store, inverted_index, structured_index)

    try:
        response = engine.search(
            query=args.query,
            page=args.page,
            page_size=args.page_size,
            from_time=args.from_time,
            to_time=args.to_time,
            level_filter=args.level,
            service_filter=args.service,
        )
    except QuerySyntaxError as err:
        print(f"\n[!] Query Syntax Error:\n{err}", file=sys.stderr)
        sys.exit(1)

    print("\n" + "=" * 65)
    print(f"Search Query : '{args.query}'")
    print(
        f"Matches      : {response.total_matches:,} | "
        f"Page {response.page} of {max(1, response.total_pages)} | "
        f"Execution Time: {response.query_ms:.2f} ms"
    )
    print(
        f"Level Counts : INFO={response.level_counts['INFO']} | "
        f"WARN={response.level_counts['WARN']} | "
        f"ERROR={response.level_counts['ERROR']}"
    )
    print("=" * 65)

    if not response.lines:
        print("\nNo matching log lines found. Try broadening your terms or expanding the time window.\n")
        return

    for item in response.lines:
        line_id = item["line_id"]
        lvl = item.get("level", "UNKNOWN")
        svc = item.get("service", "")
        ts = item.get("timestamp", "")
        msg = item.get("message", item.get("raw_text", ""))

        # Simple terminal color coding for CLI output
        color_prefix = ""
        color_suffix = "\033[0m"
        if lvl == "ERROR":
            color_prefix = "\033[91m"  # Red
        elif lvl == "WARN":
            color_prefix = "\033[93m"  # Yellow
        elif lvl == "INFO":
            color_prefix = "\033[92m"  # Green

        print(
            f"[{line_id:06d}] {ts} {color_prefix}[{lvl:<5}]{color_suffix} {svc:<12} {msg}"
        )
    print()


def cmd_stats(args: argparse.Namespace) -> None:
    """Handle the 'stats' command."""
    if not os.path.exists(args.index_dir):
        print(
            f"[!] Error: Index directory '{args.index_dir}' not found.\n"
            f"    Please run 'python -m loglens.cli ingest <path>' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    line_store, inverted_index, structured_index, manifest = load_index(args.index_dir)

    print("\n" + "=" * 55)
    print("            LOGLENS INDEX STATISTICS")
    print("=" * 55)
    print(f"Total Lines Indexed : {line_store.total_lines():,}")
    print(f"Vocabulary Size     : {inverted_index.vocabulary_size():,} tokens")
    print(f"Total Postings      : {inverted_index.total_postings():,}")
    print(f"Estimated Index RAM : {line_store.memory_bytes() / (1024*1024):.2f} MB (LineStore)")
    print(
        f"                    : {inverted_index.memory_bytes() / (1024*1024):.2f} MB (InvertedIndex)"
    )
    print(
        f"                    : {structured_index.memory_bytes() / (1024*1024):.2f} MB (StructuredIndex)"
    )
    print(f"Tracked Services    : {', '.join(structured_index.get_all_services())}")
    print(f"Level Postings      : INFO={len(structured_index.get_level_postings('INFO')):,} | "
          f"WARN={len(structured_index.get_level_postings('WARN')):,} | "
          f"ERROR={len(structured_index.get_level_postings('ERROR')):,}")
    print(f"Saved At            : {manifest.get('saved_at', 'unknown')}")
    print("=" * 55 + "\n")


def cmd_serve(args: argparse.Namespace) -> None:
    """Handle the 'serve' command."""
    from loglens.api_server import start_server
    start_server(
        index_dir=args.index_dir,
        port=args.port,
        host=args.host,
    )


def cmd_benchmark(args: argparse.Namespace) -> None:
    """Handle the 'benchmark' command."""
    from loglens.benchmark import run_benchmark_suite
    run_benchmark_suite(
        lines_list=[int(x) for x in args.lines.split(",")],
        num_queries=args.runs,
        output_report=args.report,
    )


def cmd_analyze(args: argparse.Namespace) -> None:
    """Handle the 'analyze' command."""
    from loglens.analytics import LogAnalytics
    analytics = LogAnalytics(log_path=args.source, out_dir=args.out_dir)
    analytics.run_full_analysis()


def main() -> None:
    """Parse CLI arguments and dispatch to command handlers."""
    parser = argparse.ArgumentParser(
        prog="python -m loglens.cli",
        description="LogLens: High-performance log aggregation, indexing, and search engine.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True, help="Available subcommands")

    # generate
    p_gen = subparsers.add_parser("generate", help="Generate synthetic log files")
    p_gen.add_argument("--lines", type=int, default=1000000, help="Line count")
    p_gen.add_argument("--out", type=str, default="data/sample.log", help="Output log path")
    p_gen.add_argument("--seed", type=int, default=42, help="Random seed")
    p_gen.add_argument("--days", type=int, default=14, help="Days to span")
    p_gen.add_argument("--spikes-out", type=str, default="data/spikes_truth.json", help="Spikes path")
    p_gen.add_argument("--malformed-ratio", type=float, default=0.005, help="Malformed lines ratio")
    p_gen.set_defaults(func=cmd_generate)

    # ingest
    p_ing = subparsers.add_parser("ingest", help="Ingest logs into persistent index")
    p_ing.add_argument("path", nargs="?", default="data/", help="File or folder containing .log files")
    p_ing.add_argument("--index-dir", type=str, default=DEFAULT_INDEX_DIR, help="Index directory")
    p_ing.set_defaults(func=cmd_ingest)

    # rebuild
    p_reb = subparsers.add_parser("rebuild", help="Rebuild index from source logs")
    p_reb.add_argument("path", nargs="?", default="data/", help="File or folder of logs")
    p_reb.add_argument("--index-dir", type=str, default=DEFAULT_INDEX_DIR, help="Index directory")
    p_reb.set_defaults(func=cmd_rebuild)

    # search
    p_src = subparsers.add_parser("search", help="Execute search query")
    p_src.add_argument("query", type=str, help="Search expression (e.g. 'timeout database')")
    p_src.add_argument("--index-dir", type=str, default=DEFAULT_INDEX_DIR, help="Index directory")
    p_src.add_argument("--page", type=int, default=1, help="Page number (1-based)")
    p_src.add_argument("--page-size", type=int, default=20, help="Results per page")
    p_src.add_argument("--from-time", type=str, default=None, help="Start ISO timestamp")
    p_src.add_argument("--to-time", type=str, default=None, help="End ISO timestamp")
    p_src.add_argument("--level", type=str, default=None, help="Filter by level (INFO/WARN/ERROR)")
    p_src.add_argument("--service", type=str, default=None, help="Filter by service name")
    p_src.set_defaults(func=cmd_search)

    # stats
    p_sta = subparsers.add_parser("stats", help="Display index statistics")
    p_sta.add_argument("--index-dir", type=str, default=DEFAULT_INDEX_DIR, help="Index directory")
    p_sta.set_defaults(func=cmd_stats)

    # serve
    p_srv = subparsers.add_parser("serve", help="Launch HTTP API and web interface")
    p_srv.add_argument("--index-dir", type=str, default=DEFAULT_INDEX_DIR, help="Index directory")
    p_srv.add_argument("--port", type=int, default=8000, help="Port to listen on (default 8000)")
    p_srv.add_argument("--host", type=str, default="127.0.0.1", help="Host interface (default 127.0.0.1)")
    p_srv.set_defaults(func=cmd_serve)

    # benchmark
    p_bmk = subparsers.add_parser("benchmark", help="Run benchmark suite")
    p_bmk.add_argument("--lines", type=str, default="100000,500000,1000000", help="Comma-separated line counts")
    p_bmk.add_argument("--runs", type=int, default=200, help="Number of benchmark queries to execute")
    p_bmk.add_argument("--report", type=str, default="docs/benchmark_report.md", help="Destination markdown report")
    p_bmk.set_defaults(func=cmd_benchmark)

    # analyze
    p_ana = subparsers.add_parser("analyze", help="Run pandas error spike analytics")
    p_ana.add_argument("--source", type=str, default="data/sample.log", help="Source log file path")
    p_ana.add_argument("--out-dir", type=str, default="output", help="Directory for charts and tables")
    p_ana.set_defaults(func=cmd_analyze)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
