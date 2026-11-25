"""Zero-dependency JSON API and static web server using http.server.

This module provides the HTTP server for LogLens:
  - Pure Python standard library: http.server, urllib.parse, json, mimetypes.
  - Endpoints:
      GET /search?query=&level=&service=&from=&to=&page=&pageSize=
      GET /stats
      GET /line/<id>?window=3
      GET /analytics
      Static files: /index.html, /styles.css, /app.js, /output/<file>
  - Thread-safe, non-blocking concurrent request handling via ThreadingHTTPServer.
  - Strict read-only index access during serving.
  - Built-in CORS support for local development.
  - Clear JSON error responses and HTTP status codes.
"""

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
import re
import sys
from typing import Any, Dict, Optional, Tuple
import urllib.parse

from loglens.line_store import LineStore
from loglens.parser import LogParser
from loglens.persistence import load_index
from loglens.query_engine import QueryEngine
from loglens.query_parser import QuerySyntaxError


class LogLensServerState:
    """Thread-safe read-only application state container."""

    def __init__(self, index_dir: str, web_dir: str, output_dir: str) -> None:
        self.index_dir = index_dir
        self.web_dir = os.path.abspath(web_dir)
        self.output_dir = os.path.abspath(output_dir)

        self.line_store: Optional[LineStore] = None
        self.query_engine: Optional[QueryEngine] = None
        self.manifest: Dict[str, Any] = {}
        self.index_loaded = False
        self.parser = LogParser()

        self.reload_index()

    def reload_index(self) -> bool:
        """Attempt to load index structures from disk."""
        if not os.path.exists(self.index_dir):
            self.index_loaded = False
            return False

        try:
            store, inv, struct, manifest = load_index(self.index_dir)
            self.line_store = store
            self.manifest = manifest
            self.query_engine = QueryEngine(store, inv, struct, self.parser)
            self.index_loaded = True
            return True
        except Exception as exc:
            print(f"[!] Warning: Could not load index from '{self.index_dir}': {exc}", file=sys.stderr)
            self.index_loaded = False
            return False


class LogLensRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler supporting JSON endpoints and static assets."""

    server_state: LogLensServerState

    def do_OPTIONS(self) -> None:
        """Handle CORS pre-flight requests."""
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        """Route GET requests to API handlers or static file serving."""
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        query_params = urllib.parse.parse_qs(parsed_url.query)

        # 1. API: /search or /api/search
        if path in ("/search", "/api/search"):
            self.handle_search(query_params)
            return

        # 2. API: /stats or /api/stats
        if path in ("/stats", "/api/stats"):
            self.handle_stats()
            return

        # 3. API: /line/<id> or /api/line/<id>
        line_match = re.match(r"^/(?:api/)?line/(\d+)$", path)
        if line_match:
            line_id = int(line_match.group(1))
            self.handle_line_context(line_id, query_params)
            return

        # 4. API: /analytics or /api/analytics
        if path in ("/analytics", "/api/analytics"):
            self.handle_analytics()
            return

        # 5. Static Files from output directory (e.g., /output/error_spikes.png)
        if path.startswith("/output/"):
            rel_file = path[len("/output/"):]
            file_path = os.path.join(self.server_state.output_dir, rel_file)
            self.serve_static_file(file_path)
            return

        # 6. Static Web Assets (index.html, styles.css, app.js)
        if path in ("/", "/index.html"):
            file_path = os.path.join(self.server_state.web_dir, "index.html")
            self.serve_static_file(file_path)
            return

        # Sanitize and serve other files directly from web_dir
        clean_name = os.path.basename(path.lstrip("/"))
        file_path = os.path.join(self.server_state.web_dir, clean_name)
        if os.path.exists(file_path) and os.path.isfile(file_path):
            self.serve_static_file(file_path)
            return

        # Not found
        self.send_json_response(
            HTTPStatus.NOT_FOUND,
            {"error": "Not Found", "message": f"Resource '{path}' does not exist."},
        )

    def handle_search(self, params: Dict[str, list]) -> None:
        """Handle log search requests."""
        if not self.server_state.index_loaded or not self.server_state.query_engine:
            self.send_json_response(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {
                    "error": "IndexNotBuilt",
                    "message": "Search index is not built or unavailable.",
                    "command": "python -m loglens.cli ingest data/",
                },
            )
            return

        query_str = params.get("query", [""])[0]
        level = params.get("level", [None])[0]
        service = params.get("service", [None])[0]
        from_time = params.get("from", [None])[0]
        to_time = params.get("to", [None])[0]

        try:
            page = int(params.get("page", ["1"])[0])
        except ValueError:
            page = 1

        try:
            page_size = int(params.get("pageSize", ["100"])[0])
        except ValueError:
            page_size = 100

        try:
            response = self.server_state.query_engine.search(
                query=query_str,
                page=page,
                page_size=page_size,
                from_time=from_time,
                to_time=to_time,
                level_filter=level,
                service_filter=service,
            )

            payload = {
                "lines": response.lines,
                "totalMatches": response.total_matches,
                "page": response.page,
                "pageSize": response.page_size,
                "totalPages": response.total_pages,
                "queryMs": response.query_ms,
                "levelCounts": response.level_counts,
                "matchedTokens": response.matched_tokens,
                "queryParsed": response.query_parsed,
            }
            self.send_json_response(HTTPStatus.OK, payload)

        except QuerySyntaxError as err:
            self.send_json_response(
                HTTPStatus.BAD_REQUEST,
                {
                    "error": "QuerySyntaxError",
                    "message": err.raw_message,
                    "example": err.example,
                },
            )
        except Exception as exc:
            self.send_json_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "InternalError", "message": str(exc)},
            )

    def handle_stats(self) -> None:
        """Handle index statistics requests."""
        if not self.server_state.index_loaded or not self.server_state.line_store:
            self.send_json_response(
                HTTPStatus.OK,
                {
                    "status": "not_built",
                    "message": "Index not built yet.",
                    "command": "python -m loglens.cli ingest data/",
                },
            )
            return

        store = self.server_state.line_store
        engine = self.server_state.query_engine
        struct = engine.structured_index if engine else None
        inv = engine.inverted_index if engine else None

        payload = {
            "status": "ready",
            "totalLines": store.total_lines(),
            "vocabularySize": inv.vocabulary_size() if inv else 0,
            "totalPostings": inv.total_postings() if inv else 0,
            "services": struct.get_all_services() if struct else [],
            "levelCounts": {
                "INFO": len(struct.get_level_postings("INFO")) if struct else 0,
                "WARN": len(struct.get_level_postings("WARN")) if struct else 0,
                "ERROR": len(struct.get_level_postings("ERROR")) if struct else 0,
            },
            "memoryMb": round(
                (
                    store.memory_bytes()
                    + (inv.memory_bytes() if inv else 0)
                    + (struct.memory_bytes() if struct else 0)
                )
                / (1024 * 1024),
                2,
            ),
            "manifest": self.server_state.manifest,
        }
        self.send_json_response(HTTPStatus.OK, payload)

    def handle_line_context(self, line_id: int, params: Dict[str, list]) -> None:
        """Handle neighboring context retrieval for a specific LineID."""
        if not self.server_state.index_loaded or not self.server_state.line_store:
            self.send_json_response(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "IndexNotBuilt", "message": "Index unavailable."},
            )
            return

        window_str = params.get("window", ["3"])[0]
        try:
            window = max(1, min(20, int(window_str)))
        except ValueError:
            window = 3

        try:
            raw_context = self.server_state.line_store.get_context(line_id, window=window)
            lines_data = []
            for curr_id, raw_line, is_target in raw_context:
                parsed = self.server_state.parser.parse_line(raw_line)
                lines_data.append({
                    "line_id": curr_id,
                    "raw_text": raw_line,
                    "timestamp": parsed.timestamp_str if parsed else "",
                    "level": parsed.level if parsed else "UNKNOWN",
                    "service": parsed.service if parsed else "",
                    "message": parsed.message if parsed else raw_line,
                    "is_target": is_target,
                })

            self.send_json_response(
                HTTPStatus.OK,
                {"target_id": line_id, "window": window, "lines": lines_data},
            )
        except IndexError:
            self.send_json_response(
                HTTPStatus.NOT_FOUND,
                {"error": "LineNotFound", "message": f"Line ID {line_id} does not exist."},
            )

    def handle_analytics(self) -> None:
        """Serve analytics summary metadata and spike detection results."""
        spike_csv = os.path.join(self.server_state.output_dir, "spike_days.csv")
        daily_csv = os.path.join(self.server_state.output_dir, "daily_counts.csv")
        chart_png = os.path.join(self.server_state.output_dir, "error_spikes.png")
        interp_md = os.path.join(self.server_state.output_dir, "interpretation.md")

        if not os.path.exists(spike_csv) or not os.path.exists(chart_png):
            self.send_json_response(
                HTTPStatus.OK,
                {
                    "available": False,
                    "message": "Analytics have not been generated yet.",
                    "command": "python -m loglens.cli analyze",
                },
            )
            return

        # Read interpretation markdown if available
        interp_text = ""
        if os.path.exists(interp_md):
            with open(interp_md, "r", encoding="utf-8") as f:
                interp_text = f.read()

        # Parse spike days CSV
        spikes = []
        with open(spike_csv, "r", encoding="utf-8") as f:
            headers = f.readline().strip().split(",")
            for row in f:
                if row.strip():
                    parts = row.strip().split(",")
                    spikes.append(dict(zip(headers, parts)))

        self.send_json_response(
            HTTPStatus.OK,
            {
                "available": True,
                "chartUrl": "/output/error_spikes.png",
                "spikes": spikes,
                "interpretation": interp_text,
            },
        )

    def serve_static_file(self, file_path: str) -> None:
        """Serve static files with appropriate MIME types and caching headers."""
        if not os.path.exists(file_path) or not os.path.isfile(file_path):
            self.send_json_response(
                HTTPStatus.NOT_FOUND,
                {"error": "FileNotFound", "message": f"File '{file_path}' not found."},
            )
            return

        mime_type, _ = mimetypes.guess_type(file_path)
        if mime_type is None:
            if file_path.endswith(".js"):
                mime_type = "application/javascript"
            elif file_path.endswith(".css"):
                mime_type = "text/css"
            elif file_path.endswith(".html"):
                mime_type = "text/html"
            elif file_path.endswith(".csv"):
                mime_type = "text/csv"
            else:
                mime_type = "application/octet-stream"

        try:
            with open(file_path, "rb") as f:
                content = f.read()

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", f"{mime_type}; charset=utf-8" if "text" in mime_type or "javascript" in mime_type else mime_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache")
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(content)
        except Exception as exc:
            self.send_json_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "FileReadError", "message": str(exc)},
            )

    def send_json_response(self, status: int, data: Dict[str, Any]) -> None:
        """Helper to send JSON response with standard CORS headers."""
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_cors_headers(self) -> None:
        """Attach CORS headers to support local development."""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default stdout request logging for clean test output."""
        return


def start_server(
    index_dir: str = ".loglens_index",
    port: int = 8000,
    host: str = "127.0.0.1",
    web_dir: str = "web",
    output_dir: str = "output",
) -> None:
    """Start the LogLens HTTP server."""
    state = LogLensServerState(index_dir=index_dir, web_dir=web_dir, output_dir=output_dir)
    LogLensRequestHandler.server_state = state

    server_address = (host, port)
    httpd = ThreadingHTTPServer(server_address, LogLensRequestHandler)

    print(f"\n" + "=" * 60)
    print(f"  LogLens Server running at: http://{host}:{port}/")
    print(f"  Search API Endpoint      : http://{host}:{port}/search")
    print(f"  Index Status             : {'READY' if state.index_loaded else 'NOT BUILT'}")
    print(f"  Press Ctrl+C to stop.")
    print("=" * 60 + "\n")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] Shutting down LogLens server...")
        httpd.server_close()
