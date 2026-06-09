# pmwatch

**Prediction market anomaly monitor for detecting potential insider trading on politically-sensitive Kalshi markets.**

Built in response to the George Santos/Kalshi investigation (June 2026) and the broader question: 
*what can be detected from public API data alone?*

![pmwatch dashboard](https://raw.githubusercontent.com/lweiss01/pmwatch/main/screenshot.png)

---

## What It Does

pmwatch continuously monitors Kalshi prediction markets where participants may have material 
nonpublic information (MNPI) -- cabinet departures, SCOTUS resignations, AG nominations, 
geopolitical actions, and economic data releases -- and flags anomalous trading patterns 
before public announcements.

It is a **tipping-point detector**, not an identity resolver. It finds markets where something 
looks wrong. Kalshi and the CFTC use their internal data (account IDs, KYC records) to do 
the actual investigation. pmwatch surfaces the candidates.

---

## How Santos Got Caught (And What We Can See)

On February 23, 2026, George Santos publicly posted he would attend the State of the Union.
He had already placed bets on Kalshi that he would *not* attend. When he didn't show up,
the odds cratered and he profited tens of thousands of dollars.

Kalshi caught him using internal account data. pmwatch detects the *market-level pattern*:

- Large position established before a public statement that moves prices significantly
- Volume Z-score spike against historical baseline
- Directional price movement inconsistent with public information

**The Santos detection didn't require user identity -- it required noticing that someone 
already knew something the market didn't.**

---

## Watchlist

pmwatch monitors 33 high-MNPI-risk series across five categories:

| Category | Series | MNPI Risk Actors |
|---|---|---|
| Executive Actions | KXCABOUT, KXNEXTAG, KXNEXTDEF, KXNEXTSTATE, KXNEXTODNI... | WH chief of staff, personnel office |
| SCOTUS | KXSCOTUSRESIGN, KXSCOURT, KXTARIFFS... | Justices, clerks, Senate judiciary |
| Economic Data | KXFED, KXCPI, KXGDP | FOMC members, BLS/BEA staff |
| Geopolitical | KXGREENTERRITORY, KXCANAL, KXZELENSKYPUTIN... | NSC, State Dept, DoD |
| Congressional | KXIMPEACH, KXHOUSE, KXSENATE, KXGOVSHUT... | Congressional leadership, whips |

---

## Anomaly Scoring

Each market is scored using three signals:

**1. Volume Z-Score**
Compares recent trade volume to a rolling baseline. A spike of 8+ standard deviations 
above normal baseline is a strong anomaly signal.

**2. Block Trade Ratio**
Large privately-negotiated contracts on politically-sensitive markets are a red flag.
Block trades are identified via the `is_block_trade` field in the Kalshi API.

**3. Price Divergence**
Detects sudden directional price movement not explained by gradual drift -- 
the signature of someone positioning ahead of an announcement.

Compound score = `(max(0, vol_z - 1.5) * 15 * block_modifier) + price_bonus`

Thresholds: Yellow >= 25, Red >= 60.

---

## Cluster Analysis

A single anomaly can be noise. A series of anomalies on the same market over hours or days is a pattern.

Sophisticated actors don't establish large positions at once -- they accumulate slowly, in sub-threshold 
increments, staying under the noise floor. The Santos case was sloppy. A more careful actor would look 
like a sequence of moderate anomalies on the same market, each unremarkable individually, building toward 
a larger position before an announcement.

pmwatch detects this via gap-based clustering: anomaly events on the same market within a 72-hour window 
are grouped into a cluster and scored together.

**Cluster scoring factors:**

- **Anomaly count** — how many distinct anomaly events in the window
- **Directional consistency** — are all the block trade anomalies on the same side (YES or NO)? 
  Consistent direction suggests one actor accumulating rather than random noise.
- **Score trend** — are the anomaly scores escalating over time? Increasing urgency suggests 
  growing conviction ahead of an event.
- **Peak score** — the strongest individual signal in the cluster

Cluster score = `peak × (1 + consistency) × (1 + escalation) × log(count)`

The Clusters tab in the dashboard shows all active clusters sorted by cluster score.

---

## Advanced Forensic Features

### 1. Real-Time Political News Ingestion & Confidence Weighting
To detect potential information leakage, `pmwatch` integrates a multi-source news parser that tracks public events and maps them directly to watched prediction market series.
* **Legislative / Statutory Triggers:** Congress.gov feeds tracking 'Bills Presented to the President' and 'On the Floor Today'. Users can supply custom GPO GovInfo query URLs (`govinfo.gov/feeds`) to monitor specific house bills or committee reports.
* **Executive / Regulatory Shocks:** FederalRegister.gov API JSON notice parser filtering by Agency and Topic. Also supports tracking White House OMB (Office of Management and Budget) statements and news releases.
* **Macro / Financial Overlays:** TreasuryDirect Offering/Auction announcements and Federal Reserve Board press releases.
* **Source-Type Weighting:** Official government and regulatory updates (`primary_gov`) receive a **1.5x multiplier** on correlation confidence scores, whereas mainstream media news (`mainstream_news`) receives a **1.0x multiplier**. This highlights anomalies triggered directly by policy actions before they reach media aggregators.

### 2. High-Frequency Microstructure Watcher
In addition to rolling metrics, `pmwatch` tracks depth-of-book data stream updates to detect sub-minute manipulation signatures.
* **WebSocket Delta Feed:** Subscribes to Kalshi's V2 `orderbook_delta` WebSocket channel.
* **In-Memory Deque Buffers:** To prevent database bottlenecks, depth updates are cached in thread-safe `collections.deque(maxlen=1000)` per active market.
* **Spoofing Alert Heuristic:** Identifies bid/ask "walls" ($\ge$ 95th percentile of historical depth, e.g., $\ge 1,000$ contracts). Flags a spoofing cancellation if the wall is removed ($\ge 80\%$ volume drop) within 120 seconds *without* execution fills, immediately followed by large opposite-side trades.
* **Wash Trading Alert Heuristic:** Scans trades inside a rolling 1-minute window to identify opposite-side trades with matching quantities (within 1% tolerance) executed within 30 seconds of each other.

### 3. Whale Behavior Tracker
Large capital pre-positioning is segmented and tracked over time.
* **Dynamic Trade Profiling:** Computes the 99th percentile trade size dynamically per market to identify whale transactions.
* **Exposure Flow:** Roll up YES vs. NO whale volumes hourly into net exposure profiles.
* **Visual SVG Charts:** Renders interactive, pure-SVG net exposure charts overlaying the contract price timeline directly in the dashboard drill-down panel.

### 4. Memory & Performance Optimizations
* **NumPy Vectorization:** Vectorized Z-score, rolling means, and price divergence algorithms inside `scorer.py` using NumPy to ensure low overhead on Python's single-threaded event loop.
* **UI State Preservation:** Decoupled background data loading from page navigation. Background refreshes preserve active card selection states (`selectedId`), list highlighted states, and detail views.
* **Past Countdown Loop Protection:** Prevents infinite loop refreshes when `next_run` is in the past by caching loaded slots and gracefully displaying `pending...` when delays occur.

---

## Current Signals (as of first run, June 3 2026)

| Market | Score | Signal | MNPI Risk |
|---|---|---|---|
| KXNEXTAG-29-TBLA | 167 | compound | WH personnel office |
| KXNEXTODNI-29-RCRA | 156 | compound | WH personnel office |
| KXGREENTERRITORY-29 | 152 | compound | NSC, State Dept |
| KXCPI-26MAY-T0.5 | 105 | compound | BLS staff |

*Note: high scores do not imply insider trading. They flag markets warranting closer examination.*

---
## Case Studies

### KXNEXTAG: Todd Blanche AG Nomination (June 3, 2026)

On June 3, 2026 — pmwatch's first live run — the Kalshi market
`KXNEXTAG-29-TBLA` generated three escalating anomaly scores tied to
trading on who would be President Trump's next Attorney General.

| Market           | Score | Time (ET)      | MNPI Risk           |
|------------------|-------|----------------|---------------------|
| KXNEXTAG-29-TBLA | 118   | 11:35:49 a.m.  | WH personnel office |
| KXNEXTAG-29-TBLA | 167   | 12:04:12 p.m.  | WH personnel office |
| KXNEXTAG-29-TBLA | 377   | 9:27:56 p.m.   | WH personnel office |

Later that day, President Trump said during a White House dinner that he
would nominate Acting Attorney General Todd Blanche to serve as attorney
general on a permanent basis. The ticker suffix `TBLA` corresponds directly
to Blanche's initials.

pmwatch first detected anomalous activity at **11:35:49 a.m. ET** (score 118),
followed by a stronger alert at **12:04:12 p.m. ET** (score 167). Both signals
occurred well before public reporting of the White House dinner remarks and were
associated with upward price movement, elevated volume, and a risk profile linked
to the White House personnel process.

A third, more concentrated burst of unusual activity was detected at
**9:27:56 p.m. ET**, with a materially higher anomaly score of **377**.

The cluster scorer groups all three events into a single cluster with a
cluster score of **785** — 3 anomalies, escalating, over a 10-hour span.

**Source:** AP, ABC News, NBC News, and other major outlets reporting on
President Trump's June 3, 2026 statement that he would nominate Todd Blanche
as attorney general.

> This is pmwatch's first documented detection. The tool identified unusual
> activity in a politically sensitive market in late morning and early afternoon,
> well before the president's Wednesday evening remarks were publicly reported,
> and then flagged a second, sharper anomaly at approximately 9:28 p.m. ET.
> The cluster score of 785 makes this the strongest pattern in pmwatch's history.

---

## Architecture

```
watchmarket_watchlist.json   # 33 monitored series with MNPI risk annotations
collector.py                 # Ingests trades/candlesticks, profiles whale flows & rolls up stats
scorer.py                    # NumPy-vectorized statistical engine (Z-score, block, price jumps)
cluster_scorer.py            # Gap-based clustering of anomalies; directional & trend scoring
news_engine.py               # Feed manager for Congress.gov RSS, GPO GovInfo, Fed, FR, OMB, & Treasury
microstructure_watcher.py    # Stateful L2 spoofing & wash trading detector using local memory buffers
db.py                        # SQLite schema & query helpers (anomalies, clusters, news, alerts, stats)
scheduler.py                 # Multi-cadence engine: news (15m), whale flow (1h), microstructure (15s)
api.py                       # FastAPI backend serving dashboard and JSON endpoints
dashboard.html               # Premium dark terminal analytics UI (SVG whale charts, correlation timelines)
```

---

## API Endpoints

```
GET  /api/anomalies                  # Recent anomaly events, sorted by time
GET  /api/clusters                   # Active clusters sorted by cluster_score
GET  /api/market/{ticker}/clusters          # Cluster history for a specific market
GET  /api/cluster/{ticker}/{first_seen_ts}/events # Anomaly events within a cluster
POST /api/clusters/refresh           # Re-run cluster scorer on demand
GET  /api/markets                    # All watched markets with current price/volume
GET  /api/stats                      # Summary stats and category breakdown
GET  /api/market/{ticker}/trades            # Raw trade history for a market
GET  /api/microstructure/alerts      # Real-time L2 spoofing and wash trading alerts
GET  /api/correlations               # News-to-anomaly event correlation mappings
GET  /api/market/{ticker}/whale-flow # Hourly YES/NO whale volume flow time-series
```

---

## Limitations

| Limitation | Impact |
|---|---|
| No user identity in public data | Cannot name who placed trades |
| No order placement timestamps | Only fill time is available |
| Historical API depth ~3 months | Limited retroactive analysis |
| Kalshi only | Polymarket (offshore) has separate API |

---

## Setup

1. Clone and install dependencies:
```bash
git clone https://github.com/lweiss01/pmwatch.git
cd pmwatch
pip install -r requirements.txt
```

2. Initialize the SQLite database (creates schema for anomalies, clusters, news, and alerts):
```bash
python db.py
```

3. Start the multi-cadence scheduler daemon:
```bash
python scheduler.py
```
*Note: The scheduler handles live news ingestion every 15 minutes, whale statistics hourly, and microstructure scans every 15 seconds.*

4. Launch the FastAPI server hosting the API and analytics terminal:
```bash
python -m uvicorn api:app --port 8000
```
Open [http://127.0.0.1:8000](http://127.0.0.1:8000) in your browser.

Requires Python 3.10+. No API key needed -- all endpoints used are public.

---

## Roadmap

- [x] Anomaly scoring (volume Z-score, block trade ratio, price divergence)
- [x] Gap-based cluster analysis with directional consistency and trend scoring
- [x] Clusters tab in dashboard with pattern interpretation
- [x] Timeline drill-down: individual anomaly events within a cluster
- [x] Live Primary Government Ingestion & Correlation (Federal Register API, Congress.gov RSS, OMB Releases, TreasuryDirect)
- [x] High-frequency Microstructure Watcher (Stateful L2 spoofing & wash trading checks)
- [x] Dynamic Whale Flow Profiling & SVG charts (99th percentile hourly net position rollups)
- [x] Local-first performance & memory optimization (NumPy vectors, WebSocket cache deques, selection preservation)
- [ ] Social media correlation (cross-reference X posts with trade anomalies)
- [ ] Email/webhook alerts on high cluster scores
- [ ] Polymarket support
- [ ] Resolve candidate initials in market tickers to full names

---

## Context

- [NPR: DOJ investigating George Santos for insider trading on Kalshi](https://www.npr.org/2026/06/02/nx-s1-5843371/george-santos-kalshi-insider-trading-investigation)
- [Congress.gov: Prediction Markets and Insider Trading Law](https://www.congress.gov/crs-product/LSB11406)
- [H.R. 7004: Public Integrity in Financial Prediction Markets Act of 2026](https://ritchietorres.house.gov/posts/in-response-to-suspicious-polymarket-trade-preceding-maduro-operation-rep-ritchie-torres-introduces-legislation-to-crack-down-on-insider-trading-on-prediction-markets)
- [Kalshi API Documentation](https://docs.kalshi.com)

---

## Disclaimer

pmwatch is a public transparency tool. It detects market-level anomalies using only
publicly available data. It does not identify individual traders, make accusations,
or constitute legal or financial advice. High anomaly scores indicate unusual market
activity that may warrant further investigation -- nothing more.
