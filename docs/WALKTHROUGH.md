# LogLens: System Walkthrough (`WALKTHROUGH.md`)

This guide explains how LogLens works in plain language, following the exact path that data travels through the system from raw log files to search results in your browser.

---

## 1. High-Level Architecture Overview

LogLens is organized as a unidirectional data pipeline:

```
[ Raw Log Files ]
       │
       ▼
 [ LogParser ]  ──(Rejects)──>  [ IngestReport Sample ]
       │
   (Valid lines)
       │
       ├─────────────────────────┬────────────────────────┐
       ▼                         ▼                        ▼
 [ LineStore ]           [ InvertedIndex ]       [ StructuredIndex ]
(File ID + Byte Offset)  (Token -> Array['I'])   (Level, Service, Time)
       │                         │                        │
       └─────────────────────────┼────────────────────────┘
                                 │
                                 ▼
                         [ QueryEngine ]
                   (Smallest-first intersection)
                                 │
                    ┌────────────┴────────────┐
                    ▼                         ▼
             [ CLI Interface ]         [ ApiServer ]
                                              │
                                              ▼
                                     [ Web Interface ]
                                    (Bootstrap 5 + JS)
```

---

## 2. Step-by-Step Data Flow

### Step 1: Tokenization (`loglens/tokenizer.py`)
- Every incoming log message and every user search query passes through the exact same tokenizer: `tokenize()`.
- The tokenizer downcases the text and extracts alphanumeric sequences using `re.findall(r'[a-zA-Z0-9]+', text.lower())`.
- For example:
  - Input: `timeout calling db after 3000ms [retries: 3]`
  - Output: `['timeout', 'calling', 'db', 'after', '3000ms', 'retries', '3']`
- Notice that `3000ms` stays intact as a single token, which is essential for searching latency thresholds.

### Step 2: Parsing & Validation (`loglens/parser.py`)
- Raw lines are read in binary mode to preserve exact byte offsets.
- Each line is decoded as UTF-8 and matched against the configurable regex pattern:
  `^(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)\s+(?P<level>INFO|WARN|ERROR)\s+(?P<service>[a-zA-Z0-9_-]+)\s+(?P<message>.*)$`
- If a line fails to match (e.g. missing timestamp, truncated text, unexpected level):
  - It is **never** added to the search index.
  - The rejection counter is incremented.
  - A sample record with line number, file name, content snippet, and reason is kept for diagnostic review.
- If valid, the parsed fields are extracted.

### Step 3: Ingestion & Index Construction (`loglens/ingestor.py`)
- A global, monotonically increasing `LineID` (`0, 1, 2, ...`) is assigned to each valid line. Line IDs are permanent and never recycled.
- The `LineStore` (`loglens/line_store.py`) saves the `(file_id, byte_offset)` in compact arrays.
- The `StructuredIndex` (`loglens/structured_index.py`) records:
  - The timestamp converted to Unix epoch seconds in an `array.array('I')`. Because log entries are strictly non-decreasing in time, this array remains sorted.
  - Line IDs categorized by logging level (`INFO`, `WARN`, `ERROR`).
  - Line IDs categorized by service name (`payments`, `auth`, `orders`, etc.).
- The `InvertedIndex` (`loglens/inverted_index.py`) takes the tokenized message:
  - For each distinct token in the message, the current `LineID` is appended to that token's posting list (`array.array('I')`).
  - Because lines are processed sequentially, each posting list is automatically pre-sorted by `LineID`!

### Step 4: Query Parsing (`loglens/query_parser.py`)
- When a user enters a search string, the `QueryParser` decomposes it into structured filters and boolean clauses:
  - `level:ERROR` -> level filter
  - `service:payments` -> service filter
  - `timeout database` -> AND tokens: `['timeout', 'database']`
  - `timeout OR crash` -> OR tokens: `['timeout', 'crash']`
  - `NOT retry` or `-retry` -> Exclusion tokens: `['retry']`
  - Time boundaries: `from` and `to` ISO timestamps.

### Step 5: Query Execution (`loglens/query_engine.py`)
- **Time Filter**: If a time window is specified, `bisect_left` and `bisect_right` on the sorted timestamp array pinpoint the exact contiguous `[min_id, max_id]` range in $O(\log N)$ time.
- **Structured Filters**: Intersect with the pre-filtered level and service posting lists.
- **Token Intersection**:
  - The engine gathers posting lists for each required term.
  - Posting lists are sorted by length in ascending order.
  - Intersecting the smallest list first dramatically prunes the candidate set on the very first step, keeping execution under 200 ms even across millions of lines.
- **Exclusion (NOT)**: Candidates matching negated terms are subtracted.
- **Aggregation**: Total matches are computed, per-level counts are tallied, and only the requested page (default 100 lines) of `LineID`s is returned.
- **Line Hydration**: `LineStore` seeks directly to the exact byte offsets on disk and reads only the required page lines.

### Step 6: Presentation (`web/` & `api_server.py`)
- The JSON API (`api_server.py`) returns search results, elapsed query time (measured strictly around engine evaluation), pagination details, and level counts.
- The browser interface (`web/index.html` + `app.js`):
  - Escapes all HTML characters to eliminate injection risks.
  - Highlights matched query tokens using `<mark class="query-highlight">`.
  - Presents formatted log records with color-coded badges and monotonic line IDs.
  - Allows one-click inspection of neighboring context lines.

### Step 7: Offline Analytics (`loglens/analytics.py`)
- Reads parsed logs into a pandas DataFrame.
- Aggregates daily error rates and evaluates the 3-sigma rolling anomaly rule:
  $$\text{threshold} = \mu_{\text{prev 7 days}} + 3 \times \sigma_{\text{prev 7 days}}$$
- Generates publication-ready matplotlib visual charts (`output/error_spikes.png`), summary tables (`output/daily_counts.csv`, `output/spike_days.csv`), and human-readable interpretations (`output/interpretation.md`).
