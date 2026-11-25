# LogLens: UI Wireframes & Layout Specification (`wireframes.md`)

This document outlines the visual structure, layout responsiveness, and UI state designs for the LogLens web interface.

---

## 1. Laptop / Desktop Layout (1280px+)

On large desktop displays, all filters, metrics, and search results are arranged in an integrated developer console interface:

```
+---------------------------------------------------------------------------------------------------------+
| [LogLens v1.0]                                     [Search Explorer]  [Error Analytics]  [Diagnostics]  |
+---------------------------------------------------------------------------------------------------------+
| SEARCH FILTERS                                                                                          |
| +------------------------------------+ +------------+ +------------+ +----------+ +----------+ +-----+ |
| | Query Expression (e.g. timeout db) | | Level: ALL | | Service:   | | From:    | | To:      | | [🔍]| |
| +------------------------------------+ +------------+ +------------+ +----------+ +----------+ +-----+ |
| Syntax hints: Default AND. Explicit OR, NOT, level:ERROR, service:payments, from:ISO, to:ISO           |
+---------------------------------------------------------------------------------------------------------+
| STATUS STRIP                                                                                            |
| Matches: 1,420 | Latency: 5.18 ms | [ERROR: 320] [WARN: 180] [INFO: 920]             Page 1 of 15 [<] [>]|
+---------------------------------------------------------------------------------------------------------+
| RESULTS TABLE                                                                                           |
| Line ID | Timestamp (UTC)      | Level   | Service  | Message & Context                         | Action|
|---------+----------------------+---------+----------+-------------------------------------------+-------|
| 000224  | 2026-03-08T10:15:32Z | [ERROR] | payments | timeout calling db after 3000ms           | [Ctx] |
| 000225  | 2026-03-08T10:15:33Z | [WARN]  | db       | slow database query took 1250ms on users  | [Ctx] |
| 000226  | 2026-03-08T10:15:35Z | [INFO]  | auth     | user logged in successfully session 94812 | [Ctx] |
+---------------------------------------------------------------------------------------------------------+
```

---

## 2. Tablet Layout (768px)

On tablet screens, filters wrap gracefully into two rows, and the table enables horizontal scroll with sticky headers:

```
+-------------------------------------------------------------------------------+
| [LogLens v1.0]                                [Search]  [Analytics]  [Stats]  |
+-------------------------------------------------------------------------------+
| Query: [ timeout database OR deadlock NOT retry                           ]   |
| Level: [ ERROR v ]  Service: [ payments v ]  From: [ 2026-03-01 ]  [ Search ] |
+-------------------------------------------------------------------------------+
| Matches: 320 | Latency: 3.12 ms | [ERROR: 320]                    Page 1 of 4 |
+-------------------------------------------------------------------------------+
| Line ID | Timestamp       | Level   | Service  | Message              | Action|
| 000224  | 2026-03-08T...  | [ERROR] | payments | timeout calling...   | [Ctx] |
+-------------------------------------------------------------------------------+
```

---

## 3. Mobile Phone Layout (360px)

On mobile viewports, the table converts to vertically stacked cards with minimum 44px touch targets:

```
+---------------------------------------+
| [LogLens]                  [☰ Menu]   |
+---------------------------------------+
| Query:                                |
| [ timeout database                  ] |
|                                       |
| Level: [ All Levels               v ] |
| Service: [ All Services           v ] |
| [       🔍 Run Search (44px)        ] |
+---------------------------------------+
| Matches: 15 | Latency: 2.89 ms        |
| [ERROR: 15]   [WARN: 0]   [INFO: 0]   |
+---------------------------------------+
| STACKED CARD #1                       |
| [#000224] [ERROR]            payments |
| 2026-03-08T10:15:32Z                  |
| timeout calling db after 3000ms       |
| [       Inspect Context (44px)      ] |
+---------------------------------------+
| STACKED CARD #2                       |
| [#000225] [ERROR]                  db |
| 2026-03-08T10:16:01Z                  |
| connection refused on port 5432       |
| [       Inspect Context (44px)      ] |
+---------------------------------------+
| [ < Prev (44px) ]  [ Next > (44px) ]  |
+---------------------------------------+
```

---

## 4. Line Context Modal

```
+-------------------------------------------------------------------------------------+
| Line Context: ID #000224                                                        [X] |
+-------------------------------------------------------------------------------------+
| Adjacent lines retrieved directly from disk via file byte offsets around line 224:   |
|                                                                                     |
| [000222] [INFO]  auth     user logged in session 82910                              |
| [000223] [WARN]  db       connection pool utilization at 88% capacity               |
|                                                                                     |
| >>> TARGET LINE <<<                                                                 |
| [000224] [ERROR] payments timeout calling db after 3000ms on table transactions     |
|                                                                                     |
| [000225] [WARN]  payments retrying request attempt 1 of 3 after transient network   |
| [000226] [INFO]  orders   order created with id 4920 total amount 120usd            |
+-------------------------------------------------------------------------------------+
|                                                                       [ Close (44px)]|
+-------------------------------------------------------------------------------------+
```

---

## 5. UI States & Edge Conditions

1. **State: Index Not Built**:
   Displays a warning banner with terminal command: `python -m loglens.cli ingest data/` and a "Check Index" refresh button.
2. **State: Loading**:
   Centered animated spinner with accessible `aria-live="polite"` status announcement.
3. **State: Invalid Query Syntax**:
   Danger card displaying the parser error message and a highlighted valid example box: `level:ERROR service:payments timeout`.
4. **State: No Matches Found**:
   Displays an informative empty-state with a "Clear Specific Filters" action button.
5. **State: Server Offline / Error**:
   Retains user query in the input field and displays a red retry banner with a "Retry Search" button.
6. **State: Analytics Unavailable**:
   Prompting user to run `python -m loglens.cli analyze` to generate charts and tables.
