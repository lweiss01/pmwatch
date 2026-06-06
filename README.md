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
general on a permanent basis, according to subsequent reporting that
described the remarks as occurring Wednesday evening. The ticker suffix
`TBLA` corresponds directly to Blanche's initials.

pmwatch first detected anomalous activity in this market at
**11:35:49 a.m. ET** (score 118), followed by a stronger alert at
**12:04:12 p.m. ET** (score 167). Both signals occurred well before
public reporting of the White House dinner remarks and were associated
with upward price movement, elevated volume, and a risk profile linked
to the White House personnel process.

A third, more concentrated burst of unusual activity was detected at
**9:27:56 p.m. ET**, with a materially higher anomaly score of **377**.
This late-evening spike is temporally close to the dinner remarks, but
the exact time of Trump's statement is not clearly documented in public
coverage, so this case study does not claim minute-level alignment.

**Source:** AP, ABC News, NBC News, and other major outlets reporting on
President Trump's June 3, 2026 statement that he would nominate Todd
Blanche as attorney general.

> This is pmwatch's first documented detection. The tool identified
> unusual activity in a politically sensitive market in late morning and
> early afternoon, well before the president's Wednesday evening remarks
> were publicly reported, and then flagged a second, sharper anomaly at
> approximately 9:28 p.m. ET.

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

    watchmarket_watchlist.json   # 33 monitored series with MNPI risk annotations
    collector.py                 # Polls Kalshi API every 60 min, stores trades + candlesticks
    scorer.py                    # Z-score + block trade + price divergence anomaly detection
    cluster_scorer.py            # Gap-based clustering of anomalies; directional + trend scoring
    db.py                        # SQLite schema and query helpers (anomalies + clusters tables)
    scheduler.py                 # APScheduler wrapper: collect → score → cluster, every 60 min
    api.py                       # FastAPI backend serving dashboard and JSON endpoints
    dashboard.html               # Single-file dark-mode monitoring UI with Clusters tab

---

## API Endpoints

    GET  /api/anomalies           # Recent anomaly events, sorted by time
    GET  /api/clusters            # Active clusters sorted by cluster_score
    GET  /api/market/{ticker}/clusters   # Cluster history for a specific market
    POST /api/clusters/refresh    # Re-run cluster scorer on demand
    GET  /api/markets             # All watched markets with current price/volume
    GET  /api/stats               # Summary stats and category breakdown
    GET  /api/market/{ticker}/trades     # Raw trade history for a market

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

    git clone https://github.com/lweiss01/pmwatch.git
    cd pmwatch
    pip install -r requirements.txt
    python db.py                  # initialize database (creates anomalies + clusters tables)
    python scheduler.py           # start collection + scoring loop (runs every 60 min)
    uvicorn api:app --port 8000   # start dashboard API

Requires Python 3.10+. No API key needed -- all endpoints used are public.

---

## Roadmap

- [x] Anomaly scoring (volume Z-score, block trade ratio, price divergence)
- [x] Gap-based cluster analysis with directional consistency and trend scoring
- [x] Clusters tab in dashboard with pattern interpretation
- [ ] Timeline drill-down: individual anomaly events within a cluster
- [ ] Government calendar integration (BLS release schedule, FOMC dates)
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
