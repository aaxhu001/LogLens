"""Vercel Serverless Function entrypoint for LogLens API.

This module adapts LogLensRequestHandler to Vercel's Python Serverless Runtime.
It pre-ingests sample logs into memory on cold start (< 5ms) and caches the
index across warm invocations for sub-millisecond search latencies.
"""

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
import json
import os
import re
import sys
import urllib.parse

# Ensure project root is in sys.path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from loglens.api_server import LogLensRequestHandler, LogLensServerState
from loglens.ingestor import Ingestor
from loglens.parser import LogParser
from loglens.persistence import load_index
from loglens.query_engine import QueryEngine


# Locate directories
DATA_PATH = os.path.join(ROOT_DIR, "data", "sample.log")
OUTPUT_DIR = os.path.join(ROOT_DIR, "public", "output") if os.path.exists(os.path.join(ROOT_DIR, "public", "output")) else os.path.join(ROOT_DIR, "output")
WEB_DIR = os.path.join(ROOT_DIR, "public") if os.path.exists(os.path.join(ROOT_DIR, "public")) else os.path.join(ROOT_DIR, "web")
INDEX_DIR = os.path.join(ROOT_DIR, ".loglens_index")

# Initialize global server state (persisted across warm lambda invocations)
SERVER_STATE = LogLensServerState(
    index_dir=INDEX_DIR,
    web_dir=WEB_DIR,
    output_dir=OUTPUT_DIR,
)

# On serverless cold start, ensure index is loaded in memory
if not SERVER_STATE.index_loaded and os.path.exists(DATA_PATH):
    try:
        ingestor = Ingestor()
        report = ingestor.ingest_path(DATA_PATH)
        SERVER_STATE.line_store = ingestor.line_store
        SERVER_STATE.query_engine = QueryEngine(
            ingestor.line_store,
            ingestor.inverted_index,
            ingestor.structured_index,
            SERVER_STATE.parser,
        )
        SERVER_STATE.manifest = report.to_dict()
        SERVER_STATE.index_loaded = True
    except Exception as exc:
        print(f"[!] Serverless cold-start indexing warning: {exc}", file=sys.stderr)


class handler(LogLensRequestHandler):
    """Vercel Serverless Function handler."""

    server_state = SERVER_STATE

    def do_GET(self) -> None:
        """Route requests based on both URL path and Vercel endpoint query parameters."""
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        query_params = urllib.parse.parse_qs(parsed_url.query)

        # Check explicit endpoint parameter passed via vercel.json rewrite
        endpoint = query_params.get("endpoint", [""])[0].lower()

        # 1. Search
        if endpoint == "search" or path in ("/search", "/api/search"):
            self.handle_search(query_params)
            return

        # 2. Stats
        if endpoint == "stats" or path in ("/stats", "/api/stats"):
            self.handle_stats()
            return

        # 3. Line context
        if endpoint == "line":
            id_val = query_params.get("id", ["0"])[0]
            try:
                line_id = int(id_val)
            except ValueError:
                line_id = 0
            self.handle_line_context(line_id, query_params)
            return

        line_match = re.search(r"/(?:api/)?line/(\d+)", path)
        if line_match:
            line_id = int(line_match.group(1))
            self.handle_line_context(line_id, query_params)
            return

        # 4. Analytics
        if endpoint == "analytics" or path in ("/analytics", "/api/analytics"):
            self.handle_analytics()
            return

        # 5. Default fallback to base handler
        super().do_GET()
