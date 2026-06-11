# pmwatch: The Insider Trading Forensics Terminal for Kalshi Prediction Markets

`pmwatch` is a high-signal, local-first quantitative forensics terminal built to track, map, and expose political and macroeconomic insider trading patterns across Kalshi prediction markets.

When policy shifts, regulatory rulings, or economic metrics are drafted behind closed doors, individuals with access to material non-public information can leave a footprint in alternative asset liquidity pools before press releases drop. While retail chart scanners look retroactively at price action, `pmwatch` monitors volume anomalies, order-book microstructure, whale positioning, and primary-source news timing to surface suspected information leakage windows.

![pmwatch dashboards](https://raw.githubusercontent.com/lweiss01/pmwatch/main/screenshot.png)

---

## Dashboard Views

The terminal UI exposes five nav views (plus category filters on the anomaly and cluster feeds):

* **Anomaly Feed** — Volume spikes, block-trade signals, and price divergence scored per contract (capped at 100, with MNPI clearance-tier weighting). Category tabs filter by watchlist category (Executive, SCOTUS, Economic, Geopolitical, Congressional). Drill-down shows forensic metrics and a structured **score breakdown** (`base × block_mod + price_bonus`).
* **Anomaly Clusters** — Repeated anomaly events on the same ticker within a 72-hour window, scored by directional consistency and escalation trend. Same category filters as the anomaly feed.
* **Leakage Correlations** — Maps anomaly timestamps against ingested news articles with temporal lead-time scoring and subject-aware matching. Drill-down shows a structured **correlation explanation** (match rationale, temporal band, confidence sub-scores, expected-event boosts).
* **Microstructure Alerts** — Spoofing walls, wash-trading patterns, and order-book manipulation heuristics.
* **Operator Settings** — Edit `config.json` tunables without hand-editing JSON: scheduler interval, scheduled-event floors/windows, detection thresholds, calendar refresh, and maintenance actions (rebuild correlations).

**Cross-market clusters** (anomalies across 2+ series sharing the same MNPI actor group within 24 hours) are computed by the scheduler and available via `GET /api/cross-market-clusters` but are not yet a dedicated dashboard view.

The dashboard polls `/api/*` every 30 seconds. If the API is unreachable, it falls back to embedded simulation data and shows a warning banner.

---

## Core Architecture

The framework runs entirely on local-first engineering principles using publicly available Kalshi and government data.

### Order Book Manipulation Forensics

Prediction market contracts trade within fixed bounds ($0.00–$1.00). `pmwatch` tracks depth-of-book fluctuations via a stateful in-memory queue:

* **Pre-Cancellation Spoofing Heuristics** — Detects large liquidity walls cancelled within 120 seconds alongside opposite-side execution.
* **Wash Trading Cross-Detection** — Pairs identical execution sizes on opposite sides within a tight time window to flag artificial volume inflation.

### Volume Anomaly Scoring (`scorer.py`)

* **Compound scoring** from volume Z-score (robust median/MAD), block-trade ratio, price divergence, and open-interest delta.
* **Signal windows** — block trades use a 120-minute window; price divergence uses 360 minutes (not full 7-day history).
* **Formula versioning** — `score_history.formula_version` tracks scorer changes (current: v4); adaptive thresholds filter by current version. Formula versions 1 and 2 never shipped separately (Phases 1, 6, and 7 deployed together at version 3), so score_history has no v1/v2 strata.
* **Score cap at 100** — prevents uncapped outliers from inflating downstream correlation confidence.
* **Score-delta deduplication** — suppresses re-flags within a configurable window (default 2 hours) unless the new score is materially higher (default ≥20%).
* **MNPI clearance tiers** — watchlist actor structure (tiers 1–3) applies multipliers to base scores for higher-trust actor groups.
* **Structured score components** — new anomalies persist `score_components_json` (base score, block modifier, price bonus, clearance multiplier, trigger type) for API and dashboard breakdown panels.

### Information Leakage Correlation (`correlation_engine.py`)

Correlations are no longer naive keyword substring matches. The pipeline uses:

* **Anchor + signal matching** (`keyword_matcher.py`) — requires co-occurring context (e.g. `"federal reserve"` anchor + `"fed funds rate"` signal for `KXFED`). Blocklists suppress known false positives (e.g. Fed *stress test* announcements must not match rate markets).
* **Feed topic scoping** — Federal Reserve press releases only evaluate `KXFED`; TreasuryDirect feeds are metadata-only; disclosure feeds use dedicated parsers.
* **Subject-aware gating** (`market_subject.py`) — person-specific contracts (nominee pools, individual pardon lines) resolve the actual subject from Kalshi `yes_sub_title`, market rules, or ticker suffix (`TCRU` → Ted Cruz, `GMAX` → Ghislaine Maxwell). Articles must mention that person, not just the parent series topic.
* **Temporal lead-time bands** — pre-news windows weighted by tier (0–2h: 1.8×, 2–8h: 1.3×, 8–48h: linear decay). Post-news reaction window: 0–6h at 0.7×.
* **Expected-event temporal floors** (`expected_events.py`) — FOMC and CPI release windows in `config.json` raise the temporal multiplier floor for long pre-news leads inside scheduled windows (reduces false negatives on recurring macro events).
* **Minimum confidence threshold** (default 12.0, configurable) — weak matches are discarded.
* **Minimum match quality** (default 0.35, configurable) — applied at ingest and correlation time.
* **Structured explanations** — accepted correlations persist `explanation_json`; tuning pairs persist in `correlation_decisions` (with `matcher_version` for before/after diffs).
* **Source weighting** — disclosure filings 2.0×, primary government 1.5×, mainstream news 1.0×.

### News & Disclosure Ingestion (`feed_ingestion.py`)

Direct pipes into authoritative and primary sources:

* Federal Reserve press (Atom)
* Congress.gov floor and presented-to-president RSS
* White House Briefings
* Federal Register API (daily documents + executive orders)
* TreasuryDirect offerings (metadata only, no series matching)
* House Financial Disclosure PTR index (ZIP/XML)
* Senate eFD PTR JSON (graceful skip if blocked)
* SEC EDGAR Form 4 (Atom fallback)
* NYT Politics and Politico Playbook RSS

`news_engine.py` is a thin orchestrator; ingestion and correlation logic live in dedicated modules.

### Whale Flow Profiling

* **Contract-specific percentiles** — 99th-percentile trade size calculated per ticker.
* **Hourly accumulation rollups** — YES vs. NO directional whale flow for charting.

### Watchlist & MNPI Actors (`watchlist_loader.py`)

`watchmarket_watchlist.json` drives which Kalshi series are monitored. Top-level keys are **category slugs** (`executive_actions`, `economic_data`, `scotus`, etc.) used by dashboard filters. Entries support a legacy `risk` string or a structured `actors` array with `role` and `clearance_tier`. The collector persists actor metadata, category, and clearance tiers to `watched_markets`.

---

## Configuration (`config.json`)

All operator-tunable settings live in `config.json` at the project root. Saves via the Settings API or dashboard are **atomic** (write to `config.json.tmp`, then rename) and create `config.json.bak` before overwrite.

### Scheduler

| Key | Default | Notes |
|-----|---------|-------|
| `scheduler_interval_minutes` | `30` | Collection + scoring interval. **Restart `scheduler.py`** after changing. |

### Detection thresholds

Read at runtime by `scorer.py`, `keyword_matcher.py`, and `correlation_engine.py`. Changes take effect on the next ingest/score/correlation run (no scheduler restart).

| Section | Key | Default | Purpose |
|---------|-----|---------|---------|
| `correlation` | `min_confidence` | `12.0` | Drop correlations below this confidence score |
| `correlation` | `min_match_quality` | `0.35` | Minimum anchor+signal match quality at correlation time |
| `matcher` | `min_ingest_quality` | `0.35` | Minimum quality to tag a series on article ingest |
| `scorer` | `yellow_score` | `25.0` | Minimum normalized score to flag an anomaly |
| `scorer` | `red_score` | `60.0` | High-tier display threshold (must be > `yellow_score`) |
| `scorer` | `dedup_hours` | `2` | Suppress repeat flags within this window |
| `scorer` | `score_delta_threshold` | `0.20` | Re-flag only if new score exceeds prior by this fraction |

After changing correlation thresholds, run **Rebuild correlations** (dashboard Settings or `POST /api/correlations/rebuild`) to re-evaluate recent rows.

### Scheduled events (FOMC / CPI)

```json
"scheduled_events": {
  "enabled": true,
  "refresh": { "enabled": true, "schedule": "monthly", "last_refresh": null, "last_status": "pending" },
  "events": [
    {
      "label": "FOMC rate decision",
      "series": ["KXFED"],
      "dates": ["2026-06-18", "..."],
      "window_hours_before": 48,
      "window_hours_after": 6,
      "temporal_floor": 0.85
    }
  ]
}
```

* **`enabled`** — toggle expected-event temporal floors globally.
* **`events[].temporal_floor`** — minimum temporal multiplier inside the event window.
* **`events[].window_hours_before/after`** — hours before/after each listed date.
* **`refresh`** — metadata updated by the monthly calendar refresh job.

### Event calendar refresh (`event_calendar_refresh.py`)

Keeps FOMC and CPI `dates` arrays current:

* **Automated** — `scheduler.py` runs on the **1st of each month at 06:00 UTC**.
* **Manual (CLI)** — `python event_calendar_refresh.py` (add `--dry-run` to preview without writing).
* **Manual (API / dashboard)** — `POST /api/settings/refresh-calendar` or **Refresh event calendar** in Settings.
* **Failure policy** — if a source fetch fails (e.g. BLS CPI returning 403), existing dates are preserved; partial refresh is allowed.

Sources: Federal Reserve FOMC calendar page and BLS CPI release schedule (stdlib HTML parsing).

---

## Operator Settings (Dashboard)

Open **Settings** in the left nav (⚙). The panel is **localhost-only with no authentication** — do not expose the API to untrusted networks.

Editable from the UI:

* Scheduler poll interval (minutes)
* Scheduled-events enable toggle
* Per-event temporal floor and before/after windows (FOMC, CPI)
* Detection thresholds (correlation confidence, match/ingest quality, yellow/red scores, dedup window, score-delta)
* **Reset thresholds to defaults** button

Maintenance actions in Settings:

* **Refresh event calendar** — fetch latest FOMC/CPI dates into `config.json`
* **Rebuild correlations** — re-run correlation matching (default 30-day lookback via API)

Read the help panel for restart guidance: changing `scheduler_interval_minutes` requires restarting `scheduler.py`.

---

## Module Map

| Module | Responsibility |
|--------|----------------|
| `config.py` | Load/save `config.json`, settings validation, threshold getters |
| `collector.py` | Poll Kalshi markets/trades; persist `subject_name` from `yes_sub_title` |
| `scorer.py` | Flag volume/block/price anomalies per watched market |
| `cluster_scorer.py` | Group repeated anomalies per ticker (72h gap) |
| `cross_market_scorer.py` | Group anomalies across series by shared MNPI actors (24h window) |
| `feed_ingestion.py` | Fetch and parse RSS, Atom, disclosures, Federal Register |
| `keyword_matcher.py` | Series rules, blocklists, feed scoping, match quality |
| `market_subject.py` | Resolve person behind a ticker; gate correlations by subject |
| `correlation_engine.py` | Temporal confidence, correlate anomalies ↔ news, rebuild |
| `expected_events.py` | Scheduled FOMC/CPI windows; temporal floor adjustments |
| `event_calendar_refresh.py` | Fetch and merge official FOMC/CPI dates into config |
| `news_engine.py` | Thin orchestrator re-exporting ingestion + correlation |
| `microstructure_watcher.py` | Spoofing and wash-trade detection |
| `watchlist_loader.py` | Load watchlist; series → category map for filters |
| `scheduler.py` | Background daemon: collection, scoring, feeds, microstructure, calendar refresh |
| `api.py` | FastAPI server + dashboard |
| `db.py` | SQLite schema, migrations, forensics persistence |

---

## Installation & Quickstart

`pmwatch` operates on publicly available data endpoints. No Kalshi API keys or trading account required.

### Step 1: Download

```powershell
git clone https://github.com/lweiss01/pmwatch.git
cd pmwatch
```

Or download the ZIP from GitHub and extract it.

### Step 2: Environment & Dependencies

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python db.py
```

This creates the SQLite database at `data/pmwatch.db`.

### Step 3: Run (Dual-Window Setup)

**Window 1 — Background scheduler:**

```powershell
.venv\Scripts\Activate.ps1
python scheduler.py
```

Runs on the configured interval (default 30 minutes from `config.json`):

* Collection + scoring + per-ticker clusters + cross-market clusters
* News ingestion every 15 minutes
* Microstructure analysis every 15 seconds
* Historical data prune daily
* FOMC/CPI calendar refresh monthly (1st, 06:00 UTC)

**Window 2 — Web server:**

```powershell
.venv\Scripts\Activate.ps1
python -m uvicorn api:app --port 8000
```

### Step 4: Open the Dashboard

```
http://localhost:8000
```

Use the **Manual Run** button in the header to trigger an immediate collection cycle, or call `POST /api/collector/trigger`.

---

## Maintenance: Rebuilding Derived Data

After upgrading correlation logic, changing detection thresholds, or capping historical scores, refresh derived tables against existing collected data:

```powershell
# 1. Refresh market metadata (populates subject_name from Kalshi yes_sub_title)
python collector.py

# 2. Rebuild correlations — clears stale rows, caps scores at 100, re-matches with current rules
python correlation_engine.py --lookback-days 60

# 3. Refresh per-ticker clusters (recalculates scores from capped anomalies)
python cluster_scorer.py

# 4. Refresh cross-market clusters
python -c "import db; from cross_market_scorer import run_cross_market_scorer; db.init_db(); print(run_cross_market_scorer(lookback_days=60))"

# 5. Refresh FOMC/CPI event dates (optional; also runs monthly via scheduler)
python event_calendar_refresh.py
python event_calendar_refresh.py --dry-run   # preview only
```

API equivalents (server running):

```text
POST /api/correlations/rebuild?lookback_days=60
POST /api/clusters/refresh?lookback_days=60
POST /api/settings/refresh-calendar
PUT  /api/settings                          # partial config patch (see Settings endpoints)
```

**Note:** Correlation `explanation_json` and anomaly `score_components_json` are **forward-only** — only rows created after those features shipped have structured JSON. Older rows retain text `notes` only.

---

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | Dashboard SPA (`dashboard.html`) |
| `GET /api/stats` | Market/trade/anomaly counts, last run, next poll time |
| `GET /api/anomalies` | Recent flagged anomalies (`category`, `score_components`) |
| `GET /api/clusters` | Per-ticker accumulation clusters (`category`) |
| `GET /api/cluster/{ticker}/{first_seen_ts}/events` | Anomaly timeline for one cluster |
| `GET /api/cross-market-clusters` | Cross-series MNPI actor clusters |
| `GET /api/correlations` | News-to-anomaly leakage correlations (`explanation`) |
| `GET /api/microstructure/alerts` | Spoofing and wash-trade alerts |
| `GET /api/markets` | Watched markets with category and actor metadata |
| `GET /api/market/{ticker}/trades` | Recent trades for a contract |
| `GET /api/market/{ticker}/clusters` | Full cluster history for a ticker |
| `GET /api/market/{ticker}/whale-flow` | Hourly whale flow rollups |
| `GET /api/series-categories` | Watchlist series → category slug map |
| `GET /api/settings` | Operator settings snapshot + threshold defaults |
| `PUT /api/settings` | Partial settings update (validated allowlist) |
| `POST /api/settings/refresh-calendar` | Refresh FOMC/CPI dates into `config.json` |
| `POST /api/collector/trigger` | Manual collection + scoring run (background thread) |
| `POST /api/correlations/rebuild` | Clear and re-run correlation matching |
| `POST /api/clusters/refresh` | Re-run cluster scorer |

### Settings API example

```powershell
# Read current settings
curl http://localhost:8000/api/settings

# Raise correlation confidence floor
curl -X PUT http://localhost:8000/api/settings `
  -H "Content-Type: application/json" `
  -d '{"correlation": {"min_confidence": 15.0}}'
```

Allowlisted top-level keys: `scheduler_interval_minutes`, `scheduled_events`, `correlation`, `matcher`, `scorer`. Invalid values return HTTP 400 with an `errors` array.

---

## Tech Stack

* **Core:** Python 3.11+, FastAPI, NumPy, APScheduler
* **Storage:** SQLite (`data/pmwatch.db`) with automatic schema migrations (`explanation_json`, `score_components_json`, actor columns, etc.)
* **Frontend:** Single-page dashboard (`dashboard.html`) with SVG whale-flow charts, category filters, and forensic drill-down panels
* **Logs:** `logs/scheduler.log`, `logs/collector.log`

---

## Verification

```powershell
python -m unittest discover tests -v
```

The test suite (117+ tests) covers keyword matching (including regression cases like Fed stress-test false positives), temporal correlation bands, expected-event floors, subject gating, feed parsing, scorer hygiene, score components, cluster scoring, settings validation, event calendar refresh, and API endpoints.

---

## The Founding Thesis

Prediction markets are vulnerable to asymmetric information corruption. Legal custodians of public policy data — legislative aides, agency staff, regulatory draft writers — possess non-public insights that can leave footprints in order books before public announcements.

### The Catalyst: The George Santos Precedent

`pmwatch` was built in response to exploitation of alternative asset pools by public figures, highlighted by federal insider trading investigations into former Congressman George Santos.

### Real-World Micro-Case Study: The 9-Hour Edge

During an alpha run, the engine flagged an extreme structural anomaly. At 12:41 AM, the volume Z-score for a high-profile federal nomination contract spiked to 21.44σ. The move was driven by a concentrated asymmetric whale position. Nine hours and thirteen minutes later, mainstream political media confirmed the shift. `pmwatch` had mapped the information leakage window nearly half a day earlier.

`pmwatch` turns order book and news stream data into structured forensic intelligence for public transparency research.

---

## Disclaimer

pmwatch is a public transparency tool. It detects market-level anomalies using only publicly available data. It does not identify individual traders, make accusations, or constitute legal or financial advice. High anomaly scores indicate unusual market activity that may warrant further investigation — nothing more.
