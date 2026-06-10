# pmwatch: The Insider Trading Forensics Terminal for Kalshi Prediction Markets

`pmwatch` is a high-signal, local-first quantitative forensics terminal built to track, map, and expose political and macroeconomic insider trading patterns across Kalshi prediction markets.

When policy shifts, regulatory rulings, or economic metrics are drafted behind closed doors, individuals with access to material non-public information can leave a footprint in alternative asset liquidity pools before press releases drop. While retail chart scanners look retroactively at price action, `pmwatch` monitors volume anomalies, order-book microstructure, whale positioning, and primary-source news timing to surface suspected information leakage windows.

![pmwatch dashboards](https://raw.githubusercontent.com/lweiss01/pmwatch/main/screenshot.png)

---

## Dashboard Views

The terminal UI exposes four live forensic views:

* **Anomaly Feed** — Volume spikes, block-trade signals, and price divergence scored per contract (capped at 100, with MNPI clearance-tier weighting).
* **Accumulation Clusters** — Repeated anomaly events on the same ticker within a 72-hour window, scored by directional consistency and escalation trend.
* **Cross-Market Clusters** — Anomalies across 2+ series sharing the same MNPI actor group within 24 hours (e.g. coordinated multi-leg positioning).
* **Information Leakage Correlations** — Maps anomaly timestamps against ingested news articles with temporal lead-time scoring and subject-aware matching.
* **Microstructure Alerts** — Spoofing walls, wash-trading patterns, and order-book manipulation heuristics.

---

## Core Architecture

The framework runs entirely on local-first engineering principles using publicly available Kalshi and government data.

### Order Book Manipulation Forensics

Prediction market contracts trade within fixed bounds ($0.00–$1.00). `pmwatch` tracks depth-of-book fluctuations via a stateful in-memory queue:

* **Pre-Cancellation Spoofing Heuristics** — Detects large liquidity walls cancelled within 120 seconds alongside opposite-side execution.
* **Wash Trading Cross-Detection** — Pairs identical execution sizes on opposite sides within a tight time window to flag artificial volume inflation.

### Volume Anomaly Scoring (`scorer.py`)

* **Compound scoring** from volume Z-score, block-trade ratio, and price divergence.
* **Score cap at 100** — prevents uncapped outliers from inflating downstream correlation confidence.
* **Score-delta deduplication** — suppresses re-flags within 2 hours unless the new score is ≥20% higher.
* **MNPI clearance tiers** — watchlist actor structure (tiers 1–3) applies multipliers to base scores for higher-trust actor groups.

### Information Leakage Correlation (`correlation_engine.py`)

Correlations are no longer naive keyword substring matches. The pipeline uses:

* **Anchor + signal matching** (`keyword_matcher.py`) — requires co-occurring context (e.g. `"federal reserve"` anchor + `"fed funds rate"` signal for `KXFED`). Blocklists suppress known false positives (e.g. Fed *stress test* announcements must not match rate markets).
* **Feed topic scoping** — Federal Reserve press releases only evaluate `KXFED`; TreasuryDirect feeds are metadata-only; disclosure feeds use dedicated parsers.
* **Subject-aware gating** (`market_subject.py`) — person-specific contracts (nominee pools, individual pardon lines) resolve the actual subject from Kalshi `yes_sub_title`, market rules, or ticker suffix (`TCRU` → Ted Cruz, `GMAX` → Ghislaine Maxwell). Articles must mention that person, not just the parent series topic.
* **Temporal lead-time bands** — pre-news windows weighted by tier (0–2h: 1.8×, 2–8h: 1.3×, 8–48h: linear decay). Post-news reaction window: 0–6h at 0.7×.
* **Minimum confidence threshold** (12.0) — weak matches are discarded.
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

`watchmarket_watchlist.json` drives which Kalshi series are monitored. Entries support a legacy `risk` string or a structured `actors` array with `role` and `clearance_tier`. The collector persists actor metadata and clearance tiers to `watched_markets`.

---

## Module Map

| Module | Responsibility |
|--------|----------------|
| `collector.py` | Poll Kalshi markets/trades; persist `subject_name` from `yes_sub_title` |
| `scorer.py` | Flag volume/block/price anomalies per watched market |
| `cluster_scorer.py` | Group repeated anomalies per ticker (72h gap) |
| `cross_market_scorer.py` | Group anomalies across series by shared MNPI actors (24h window) |
| `feed_ingestion.py` | Fetch and parse RSS, Atom, disclosures, Federal Register |
| `keyword_matcher.py` | Series rules, blocklists, feed scoping, match quality |
| `market_subject.py` | Resolve person behind a ticker; gate correlations by subject |
| `correlation_engine.py` | Temporal confidence, correlate anomalies ↔ news, rebuild |
| `news_engine.py` | Thin orchestrator re-exporting ingestion + correlation |
| `microstructure_watcher.py` | Spoofing and wash-trade detection |
| `scheduler.py` | Background daemon for collection, scoring, feeds, microstructure |
| `api.py` | FastAPI server + dashboard |

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

Runs collection/scoring on the configured interval (default from `config.json`), news ingestion every 15 minutes, and microstructure analysis every 15 seconds.

**Window 2 — Web server:**

```powershell
.venv\Scripts\Activate.ps1
python -m uvicorn api:app --port 8080
```

### Step 4: Open the Dashboard

```
http://localhost:8080
```

Use the **Manual Run** button in the header to trigger an immediate collection cycle, or call `POST /api/collector/trigger`.

---

## Maintenance: Rebuilding Derived Data

After upgrading correlation logic or capping historical scores, refresh derived tables against existing collected data:

```powershell
# 1. Refresh market metadata (populates subject_name from Kalshi yes_sub_title)
python collector.py

# 2. Rebuild correlations — clears stale rows, caps scores at 100, re-matches with current rules
python correlation_engine.py --lookback-days 60

# 3. Refresh per-ticker clusters (recalculates scores from capped anomalies)
python cluster_scorer.py

# 4. Refresh cross-market clusters
python -c "import db; from cross_market_scorer import run_cross_market_scorer; db.init_db(); print(run_cross_market_scorer(lookback_days=60))"
```

API equivalents (server running):

```text
POST /api/correlations/rebuild?lookback_days=60
POST /api/clusters/refresh?lookback_days=60
```

---

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/anomalies` | Recent flagged anomalies |
| `GET /api/clusters` | Per-ticker accumulation clusters |
| `GET /api/cross-market-clusters` | Cross-series MNPI actor clusters |
| `GET /api/correlations` | News-to-anomaly leakage correlations |
| `GET /api/microstructure/alerts` | Spoofing and wash-trade alerts |
| `GET /api/market/{ticker}/whale-flow` | Hourly whale flow rollups |
| `POST /api/collector/trigger` | Manual collection + scoring run |
| `POST /api/correlations/rebuild` | Clear and re-run correlation matching |
| `POST /api/clusters/refresh` | Re-run cluster scorer |

---

## Tech Stack

* **Core:** Python 3.11+, FastAPI, NumPy, APScheduler
* **Storage:** SQLite (`data/pmwatch.db`) with automatic schema migrations
* **Frontend:** Single-page dashboard (`dashboard.html`) with SVG whale-flow charts

---

## Verification

```powershell
python -m unittest discover tests
```

The test suite covers keyword matching (including regression cases like Fed stress-test false positives), temporal correlation bands, subject gating, feed parsing, scorer hygiene, cluster scoring, and API endpoints.

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
