# LogLens: User Journey Diagrams (`journey_diagrams.md`)

This document visualizes the four core user journeys supported by LogLens using Mermaid workflow diagrams.

---

## Journey 1: Developer Root-Cause Debugging (Priya)
*Scenario*: Dev Priya notices payment failures on staging and needs to inspect error context.

```mermaid
flowchart TD
    A["Dev Priya launches Web UI (http://localhost:8000)"] --> B["Types query: 'level:ERROR service:payments timeout'"]
    B --> C["Hits Search Button"]
    C --> D["QueryEngine evaluates smallest posting list first (9.9ms)"]
    D --> E["Results Table displays matched lines with highlighted tokens"]
    E --> F["Priya clicks 'Context' button on Line #224"]
    F --> G["LineStore reads adjacent byte offsets on disk"]
    G --> H["Modal displays 4 lines before and after showing preceding database deadlock"]
    H --> I["Priya identifies database lock issue and applies fix"]
```

---

## Journey 2: On-Call / SRE Incident Response (Sam)
*Scenario*: SRE Sam responds to a PagerDuty alert indicating a potential service outage.

```mermaid
flowchart TD
    A["SRE Sam opens LogLens 'Error Analytics' tab"] --> B["Observes red circular spike markers on 2026-03-08 and 2026-03-16"]
    B --> C["Inspects Spike Days Table: Error count 32 exceeds 3-sigma threshold (6.5)"]
    C --> D["Reviews SRE Diagnostic Interpretation: Top error 'timeout calling db after 3000ms'"]
    D --> E["Switches to 'Search Explorer' with pre-filled time window: '2026-03-08'"]
    E --> F["Queries: 'level:ERROR service:db OR service:payments'"]
    F --> G["Verifies connection pool saturation across services"]
    G --> H["Executes hot-reload configuration to increase DB connection limits"]
    H --> I["Resolves incident and logs post-mortem notes"]
```

---

## Journey 3: Engineering Manager Weekly Review (Maya)
*Scenario*: Manager Maya conducts weekly reliability reviews and audits release impact.

```mermaid
flowchart TD
    A["Manager Maya runs: 'python -m loglens.cli analyze'"] --> B["LogAnalytics computes daily level counts and detects spikes"]
    B --> C["Exports 'output/daily_counts.csv' and 'output/error_spikes.png'"]
    C --> D["Maya inspects 'output/interpretation.md' executive summary"]
    D --> E["Notes that ERROR percentage is 4.8% of total 1M corpus (within 5% SLA)"]
    E --> F["Identifies Wednesday release anomaly resolved within 2 hours"]
    F --> G["Attaches matplotlib error chart to weekly stakeholder report"]
```

---

## Journey 4: Platform Admin Index Lifecycle & Maintenance (Alex)
*Scenario*: Admin Alex sets up LogLens on a CI/CD test runner and verifies index health.

```mermaid
flowchart TD
    A["Admin Alex ingests raw logs: 'python -m loglens.cli ingest data/'"] --> B["LogParser validates 1,000,000+ lines in binary mode"]
    B --> C["Reports: 998,005 valid lines, 1,995 malformed lines rejected"]
    C --> D["Inspects Diagnostic Rejection Samples for format anomalies"]
    D --> E["Runs benchmark suite: 'python -m loglens.cli benchmark'"]
    E --> F["Verifies: p95 latency is 19.44 ms (well below 200 ms target)"]
    F --> G["Index RAM: 50.90 MB (well within 200 MB container budget)"]
    G --> H["Deploys daemon: 'python -m loglens.cli serve --port 8000'"]
```
