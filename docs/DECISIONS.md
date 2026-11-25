# LogLens: Architectural Decisions Log (`DECISIONS.md`)

This document records the architectural and design decisions made for LogLens, explaining the context, options considered, and why each decision was chosen.

---

## 1. Zero Search-Library Core Engine
- **Context**: The engine must ingest, index, and query 1M+ log lines in pure Python standard library. No Elasticsearch, Lucene, Solr, Whoosh, or SQLite FTS allowed.
- **Decision**: Implemented an Inverted Index using `dict[str, array.array('I')]` and structured posting lists for metadata.
- **Why**: 
  - Standard Python dictionaries provide $O(1)$ average token lookups.
  - Python's `array.array('I')` uses contiguous 32-bit unsigned integers (4 bytes per line ID). 1,000,000 line IDs occupy only ~4 MB of RAM, compared to ~8 MB for Python lists of 64-bit integers and ~50+ MB for Python `set`s of int objects.
  - Keeps external runtime dependencies at zero for the entire engine, API, and CLI.

---

## 2. Tokenizer: Alphanumeric Splitting & Token Preservation
- **Context**: The tokenizer must be a single canonical function shared by both indexing and search query execution.
- **Decision**: Use `re.findall(r'[a-zA-Z0-9]+', text.lower())`.
- **Why**:
  - Automatically handles lowercasing (`ERROR` -> `error`, `Payments` -> `payments`).
  - Naturally preserves combined alphanumeric tokens like `3000ms`, `v2`, `port8080`, `err404` as single search tokens.
  - Punctuation (colons, slashes, dashes, quotes, brackets, dots) acts as separators: `calling db:5432 [retry=2]` yields `['calling', 'db', '5432', 'retry', '2']`.
  - Exactly consistent between query parser and log parser.

---

## 3. Timestamp Indexing: Monotonic UTC Epoch Seconds & Bisect Range Queries
- **Context**: Users search by time range (`from` and `to`). Querying 1M lines by evaluating timestamp strings or linear filtering is too slow.
- **Decision**: Convert ISO-8601 UTC timestamps (`YYYY-MM-DDTHH:MM:SSZ`) to integer Unix epoch seconds and store them in an `array.array('I')` strictly parallel to Line IDs (`0, 1, 2, ...`).
- **Why**:
  - Synthetic and production forward-appended logs have non-decreasing timestamps.
  - With sorted timestamps in an array, a time range `[from_time, to_time]` translates in $O(\log N)$ to a line ID range `[min_id, max_id]` via Python's standard `bisect.bisect_left` and `bisect.bisect_right`.
  - Out-of-order timestamps are detected during ingestion: if found, the ingest report notes the count and falls back to safe indexing.

---

## 4. Line Storage: File Offsets Over In-Memory Strings
- **Context**: Storing raw log message strings for 1,000,000 lines in memory consumes 100+ MB of RAM and stresses Python's garbage collector.
- **Decision**: `LineStore` stores only file identifiers and 64-bit byte offsets (`array.array('Q')`). Raw log lines are fetched on demand from disk via `file.seek(offset)` using binary mode.
- **Why**:
  - Line IDs directly index the offset array in $O(1)$ time.
  - Memory consumption drops from hundreds of megabytes to just 8 bytes per line for byte offsets plus 2 bytes for file index.
  - Disk reads for paginated views (default 100 lines) take less than 1 ms.
  - Allows seamless context retrieval (fetching $k$ neighboring lines before and after a target line ID).

---

## 5. Query Evaluation: Smallest-List-First Intersection & Set Difference
- **Context**: Multi-token AND queries can involve common words (long posting lists) and rare terms (short posting lists).
- **Decision**: 
  - For default `AND` queries, sort posting lists by length ascending (`len(list)`), and intersect starting with the shortest list.
  - Intersection uses sorted two-pointer traversal or Python set intersection on slices depending on density.
  - `NOT` terms are applied as set exclusions after the candidate set is identified.
  - `OR` terms form unions of posting lists.

---

## 6. Configurable Log Parser & Rejection Accounting
- **Context**: Real-world ingestion encounters malformed lines, partial writes, or corrupted entries.
- **Decision**: `LogParser` accepts a configurable regex with named groups (`(?P<timestamp>...) (?P<level>...) (?P<service>...) (?P<message>...)`). Lines failing the pattern are never indexed; instead, a rejection counter is incremented, and a bounded sample of rejected lines with explanations is retained in the `IngestReport`.
- **Why**: Protects index integrity while providing developers complete visibility into ingest errors.

---

## 7. 3-Sigma Rolling Spike Detection in Analytics
- **Context**: SREs and engineering managers need automated detection of anomalies in daily error rates.
- **Decision**: In `loglens/analytics.py`, compute daily error counts, calculate rolling 7-day mean $\mu$ and standard deviation $\sigma$, and flag any day where $\text{errors} > \mu + 3\sigma$.
- **Why**: 3-sigma over a 7-day rolling baseline is standard statistical process control; it adapts to shifting baselines while flagging true outliers.

---

## 8. Persistence Format
- **Context**: Saving and loading index state to avoid re-parsing multi-gigabyte log archives.
- **Decision**: Use `pickle` with a version header and JSON manifest for index metadata.
- **Why**:
  - Native support for Python's `array.array` objects without serializing them into giant JSON arrays of text numbers.
  - Near-instantaneous binary serialization and deserialization.
  - Preserves exact equality between freshly ingested and reloaded indices.
