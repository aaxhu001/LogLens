# LogLens: User Personas (`personas.md`)

This document defines the four core user personas for LogLens, outlining their goals, workflows, pain points, and how LogLens fulfills their requirements.

---

## Persona 1: Dev Priya (Backend Software Engineer)
- **Role**: Senior Software Engineer responsible for microservices and payment workflows.
- **Goals**:
  - Quickly search through millions of local/staging log entries during debugging.
  - Isolate specific error messages and trace correlation IDs across services.
  - Inspect immediate log context (3-5 lines before and after a failure) to determine root cause.
- **Pain Points**:
  - Full-blown ELK/Elasticsearch clusters are heavy, require Docker/JVM, and consume gigabytes of RAM on development laptops.
  - `grep` is too slow on 1M+ lines and lacks structured boolean queries, level filtering, and time ranges.
- **How LogLens Solves It**:
  - Zero external search dependencies; runs locally in standard Python.
  - Sub-millisecond to 20ms search latencies across 1M+ lines with smallest-first intersection.
  - One-click "Context" modal retrieves neighboring lines directly from disk using exact byte offsets.

---

## Persona 2: SRE Sam (Site Reliability Engineer / On-Call Responder)
- **Role**: On-call engineer responding to high-severity alerts and service degradations.
- **Goals**:
  - Rapidly identify anomaly spikes in ERROR rates.
  - Correlate spikes with specific failure modes (e.g. `timeout calling db after 3000ms`, `connection refused on port 5432`).
  - Pinpoint exact start and end times of operational degradation.
- **Pain Points**:
  - Unclear whether an alert is a temporary blip or a statistically significant outage.
  - High noise from routine warnings and informational heartbeats.
- **How LogLens Solves It**:
  - Built-in 3-sigma anomaly detection against rolling 7-day baselines automatically flags anomalous spike days.
  - Visual matplotlib charts highlight anomaly days with red status markers.
  - Structured queries like `level:ERROR service:payments timeout NOT retry` instantly isolate high-severity issues.

---

## Persona 3: Manager Maya (Engineering Director / Technical Analyst)
- **Role**: Engineering Manager reviewing service health, release regressions, and SLAs.
- **Goals**:
  - Review weekly and monthly error trends across services.
  - Track service reliability improvements following architectural refactoring.
  - Export CSV summaries of daily counts and anomaly days for executive reporting.
- **Pain Points**:
  - Raw log files cannot be interpreted by non-developer stakeholders.
  - Difficulty answering "Did the new release on Wednesday cause a statistically significant error spike?"
- **How LogLens Solves It**:
  - Automated generation of `output/daily_counts.csv` and `output/spike_days.csv`.
  - Human-readable narrative reports (`output/interpretation.md`) summarizing top error signatures per spike day.
  - High-level index statistics and level percentage breakdowns in the web UI.

---

## Persona 4: Platform Admin Alex (Infrastructure & DevOps Lead)
- **Role**: Platform Administrator managing local test environments and log pipeline throughput.
- **Goals**:
  - Ensure zero runtime crashes caused by malformed or corrupted log entries.
  - Keep RAM and storage footprints bounded on CI/CD runner nodes.
  - Fast, deterministic rebuilds and cold-start index persistence.
- **Pain Points**:
  - Corrupted log lines crash naive parsers and poison indexes.
  - Storing raw string messages in memory causes Out-Of-Memory (OOM) errors on large log archives.
- **How LogLens Solves It**:
  - Robust regex validation rejects malformed lines, records rejection counts, and preserves diagnostic samples.
  - Memory-efficient `LineStore` stores only 14 bytes per line (file ID + 64-bit byte offset), keeping 1M lines under 51 MB of RAM.
  - Atomic serialization (`save_index`, `load_index`, `rebuild_index`) guarantees 100% reproducible results.
