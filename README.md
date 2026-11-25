# LogLens 🔍
*High-Performance Zero-Dependency Log Aggregation, Inverted Indexing, and Search Pipeline in Python 3*

> [!NOTE]
> **Synthetic Data Notice**: All log files, services, and operational incidents in this repository are **100% synthetically generated** for benchmarking and architectural validation. No real customer or proprietary data is used.

---

## Table of Contents
1. [Overview & Highlights](#1-overview--highlights)
2. [Architecture & Data Pipeline](#2-architecture--data-pipeline)
3. [Setup & Installation](#3-setup--installation)
4. [Log Format Specification](#4-log-format-specification)
5. [Canonical Tokenizer](#5-canonical-tokenizer)
6. [Query Engine & Search Syntax](#6-query-engine--search-syntax)
7. [HTTP REST API Contract](#7-http-rest-api-contract)
8. [Web Interface](#8-web-interface)
9. [Operational Error Analytics & 3-Sigma Anomaly Detection](#9-operational-error-analytics--3-sigma-anomaly-detection)
10. [Performance Benchmarks](#10-performance-benchmarks)
11. [How to Reproduce](#11-how-to-reproduce)
12. [Known Limitations](#12-known-limitations)

---

## 1. Overview & Highlights

LogLens is an end-to-end log aggregation and search engine engineered strictly with Python's standard library for its core indexing and search components. It demonstrates that with thoughtful data structures and cache-conscious algorithm design, pure Python can index and query millions of records with sub-20ms latencies without the operational overhead of Lucene, Elasticsearch, or SQLite FTS.

### Key Architectural Strengths:
- **Zero Search Libraries**: The ingestion pipeline, tokenizer, inverted index, structured metadata index, line store, and HTTP server rely 100% on the Python standard library (`array`, `bisect`, `re`, `collections`, `http.server`, `pickle`).
- **Sub-20ms p95 Latency on 1M+ Lines**: Achieved **19.44 ms** p95 latency across 200 mixed queries on a 1,000,000-line corpus (surpassing the < 200 ms requirement by 10x).
- **Extreme Memory Efficiency**:
  - `LineStore` maps 1M lines to disk byte offsets in only **14 MB** of RAM (14 bytes per line: 16-bit file ID + 64-bit byte offset + 32-bit length).
  - Total index RAM across 1M lines is **50.90 MB**, avoiding Python garbage collection pauses.
- **Smallest-First Posting List Intersection**: Multi-term AND queries intersect the shortest candidate list first, pruning candidate sets by 99% in the initial step.
- **Logarithmic Time Range Slicing**: Timestamps are stored in line-ID order, enabling $O(\log N)$ range resolution to line IDs via binary search (`bisect`).
- **Configurable Anomaly Spike Detection**: Batch analysis module (using pandas and matplotlib) detects statistical error anomalies exceeding rolling 7-day mean + 3 standard deviations.

---

## 2. Architecture & Data Pipeline

```
[ Raw Log Files (.log) ]
           │
           ▼
     [ LogParser ]  ──(Rejection)──> [ IngestReport Sample ]
           │
     (Valid lines)
           │
           ├──────────────────────────┬─────────────────────────┐
           ▼                          ▼                         ▼
     [ LineStore ]            [ InvertedIndex ]        [ StructuredIndex ]
  (File ID + Byte Offset)     (Token -> array['I'])    (Level, Service, Time)
           │                          │                         │
           └──────────────────────────┼─────────────────────────┘
                                      │
                                      ▼
                               [ QueryEngine ]
                        (Smallest-first intersection)
                                      │
                         ┌────────────┴────────────┐
                         ▼                         ▼
                  [ CLI Interface ]          [ ApiServer ]
                                                   │
                                                   ▼
                                          [ Web Interface ]
                                         (Bootstrap 5 + JS)
```

For an exhaustive component-by-component explanation, see [`docs/WALKTHROUGH.md`](docs/WALKTHROUGH.md).

---

## 3. Setup & Installation

### Requirements:
- **Python**: Version `3.10`, `3.11`, or `3.12+`
- **Optional Dependencies** (for batch analysis & test runner only):
  - `pandas>=2.0.0`
  - `matplotlib>=3.7.0`
  - `pytest>=7.0.0`

### Quick Start:

```bash
# 1. Clone the repository
git clone https://github.com/aaxhu001/LogLens.git
cd LogLens

# 2. Create virtual environment and install requirements
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Ingest sample data and build the persistent index
python -m loglens.cli ingest data/

# 4. Launch the local web console and JSON API server
python -m loglens.cli serve --port 8000
```
Open **`http://localhost:8000`** in any browser.

---

## 4. Log Format Specification

LogLens parses standardized microservice server logs with the following format:
```text
2026-03-01T10:15:32Z ERROR payments timeout calling db after 3000ms
```

### Field Definitions:
1. **Timestamp**: ISO-8601 UTC timestamp (`YYYY-MM-DDTHH:MM:SSZ` or with timezone offset).
2. **Level**: Logging level (`INFO`, `WARN`, or `ERROR`).
3. **Service**: Alphanumeric identifier of the issuing service (`payments`, `auth`, `db`, `api`, `orders`, `search`, `inventory`, `notifications`).
4. **Message**: Free-form text message containing contextual attributes, latency metrics, and identifiers.

### Regex Configurability:
The log parser regex is configurable in `LogParser`. The canonical default pattern is:
```python
r"^(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))\s+(?P<level>[a-zA-Z]+)\s+(?P<service>[a-zA-Z0-9_.\-]+)\s+(?P<message>.*)$"
```
Lines failing this format or containing invalid calendar dates (e.g. `2026-02-31`) are **never indexed**, always counted, and a bounded diagnostic sample is preserved in the ingestion report.

---

## 5. Canonical Tokenizer

LogLens uses a single canonical tokenizer (`loglens/tokenizer.py`) for **both log message indexing and search query execution**. Sharing the identical tokenizer guarantees 100% search recall.

### Tokenization Rules:
- **Normalization**: Downcases all characters to ensure case-insensitivity.
- **Alphanumeric Splitting**: Extracts contiguous runs of alphanumeric characters: `re.findall(r'[a-zA-Z0-9]+', text.lower())`.
- **Handling of Numbers and Compound Units**:
  - Compound alphanumeric units like `3000ms`, `port8080`, `v2`, or `err404` contain both letters and digits without delimiters and are **preserved intact as single search tokens**.
- **Punctuation & Delimiters**:
  - All non-alphanumeric symbols (punctuation, spaces, slashes, hyphens, colons, brackets, quotes) act as delimiters and are omitted. For example, `192.168.1.1` becomes `['192', '168', '1', '1']`.

---

## 6. Query Engine & Search Syntax

The query parser supports intuitive Google-like search expressions:

| Syntax Type | Example Expression | Description |
| :--- | :--- | :--- |
| **Default AND** | `timeout database` | Matches lines containing both `timeout` AND `database`. |
| **Explicit OR** | `timeout OR deadlock` | Matches lines containing either `timeout` OR `deadlock`. |
| **Explicit NOT** | `timeout NOT retry` | Matches lines with `timeout` but excludes any with `retry`. |
| **Prefix Negation** | `timeout -retry` | Equivalent to `NOT retry`. |
| **Level Filter** | `level:ERROR` | Filters lines by level (`INFO`, `WARN`, `ERROR`). |
| **Service Filter** | `service:payments` | Restricts search to a specific microservice. |
| **Time Range** | `from:2026-03-01T00:00:00Z to:2026-03-05T23:59:59Z` | Inclusive ISO-8601 time window. |
| **Combined** | `level:ERROR service:payments timeout NOT deadlock` | Full multi-attribute search. |

*Invalid queries raise a clear error with a concrete valid example.*

---

## 7. HTTP REST API Contract

The `api_server.py` daemon provides a zero-dependency HTTP JSON API with local CORS enabled:

### Endpoints:

#### 1. `GET /search`
- **Query Parameters**:
  - `query` (str): Search expression (e.g. `timeout database`).
  - `level` (str, optional): `INFO`, `WARN`, `ERROR`.
  - `service` (str, optional): Target service name.
  - `from` (str, optional): Lower bound ISO-8601 UTC timestamp.
  - `to` (str, optional): Upper bound ISO-8601 UTC timestamp.
  - `page` (int, default `1`): 1-based page number.
  - `pageSize` (int, default `100`): Results per page.
- **Response `200 OK`**:
```json
{
  "lines": [
    {
      "line_id": 224,
      "raw_text": "2026-03-08T10:15:32Z ERROR payments timeout calling db after 3000ms",
      "timestamp": "2026-03-08T10:15:32Z",
      "level": "ERROR",
      "service": "payments",
      "message": "timeout calling db after 3000ms"
    }
  ],
  "totalMatches": 15,
  "page": 1,
  "pageSize": 100,
  "totalPages": 1,
  "queryMs": 5.18,
  "levelCounts": { "INFO": 0, "WARN": 0, "ERROR": 15 },
  "matchedTokens": ["timeout", "db"]
}
```

#### 2. `GET /stats`
- Returns index size, line counts, vocabulary size, RAM usage, and tracked services.

#### 3. `GET /line/<id>?window=3`
- Returns the target line plus $k$ neighboring lines before and after for context inspection.

#### 4. `GET /analytics`
- Serves 3-sigma anomaly spike tables, interpretation text, and the URL for the matplotlib chart.

---

## 8. Web Interface

The web frontend (`web/index.html`) is built using **Bootstrap 5** and **vanilla JavaScript**:
- **Monospace Developer Look**: Terminal aesthetic with high-contrast level badges.
- **Token Highlighting**: Safe HTML escaping first, followed by `<mark class="query-highlight">` token injection.
- **Line Context Modal**: One-click inspection of neighboring lines directly from disk.
- **Fully Responsive**:
  - **Phone (<576px)**: Stacked cards with $\ge 44\text{px}$ touch targets.
  - **Tablet (768px)**: Flexible filters with wide data table.
  - **Desktop (1280px+)**: Integrated layout with stats and results.
- **Accessible (WCAG 2.2 AA)**: Semantic elements, visible focus indicators, and `aria-live` screen-reader announcements.

---

## 9. Operational Error Analytics & 3-Sigma Anomaly Detection

LogLens includes an automated operational anomaly detector (`loglens/analytics.py`):

$$\text{Threshold}_{\text{day } t} = \mu_{\text{prev 7 days}} + 3 \times \sigma_{\text{prev 7 days}}$$

- If a day's error count exceeds this threshold, it is flagged as an operational spike.
- A high-resolution chart is exported to `output/error_spikes.png`.
- Summary tables and narrative diagnoses are saved to `output/daily_counts.csv`, `output/spike_days.csv`, and `output/interpretation.md`.

---

## 10. Performance Benchmarks

Conducted on Apple Silicon (M-series, 16 GB RAM, Python 3.12, macOS Darwin):

| Corpus Size | Lines Parsed | Postings Tracked | Ingest Time | Throughput | Index RAM | Query p50 | Query p95 (Target < 200ms) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **100,000** | 99,805 | 660,580 | 0.29s | 341,012 lines/s | 5.09 MB | 1.46 ms | **2.89 ms** |
| **500,000** | 498,972 | 3,303,151 | 1.49s | 334,828 lines/s | 25.45 MB | 3.11 ms | **9.96 ms** |
| **1,000,000+** | 998,005 | 6,606,999 | 3.00s | 332,566 lines/s | 50.90 MB | 5.18 ms | **19.44 ms** |

*Full benchmark conditions and architectural analysis can be viewed in [`docs/benchmark_report.md`](docs/benchmark_report.md).*

---

## 11. How to Reproduce

```bash
# 1. Run all unit, integration, and brute-force tests
pytest

# 2. Run the brute-force equivalence test specifically
pytest tests/test_bruteforce.py

# 3. Run the performance benchmark across 100k, 500k, and 1M lines
python -m loglens.cli benchmark --lines 100000,500000,1000000 --runs 200

# 4. Run the 3-sigma error spike analytics
python -m loglens.cli analyze --source data/sample.log --out-dir output

# 5. Start the web server and open http://localhost:8000
python -m loglens.cli serve
```

---

## 12. Known Limitations
- **Single-Node In-Memory Indices**: The inverted index is designed to fit in RAM on a single machine (requires ~50 MB RAM per 1M lines).
- **Exact Alphanumeric Matching**: Punctuation within tokens is split into constituent words; regex wildcard searching within tokens is not part of the standard index pass.
- **Append-Only Assumptions**: Timestamps are indexed monotonically in file order. Out-of-order logs are detected and reported in ingestion diagnostics.
