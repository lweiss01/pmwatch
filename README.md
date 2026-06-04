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

On June 3, 2026 — pmwatch's first run — the market `KXNEXTAG-29-TBLA` 
scored **167** (compound signal), the highest score recorded that day.

| Market           | Score | Signal   | MNPI Risk           |
|------------------|-------|----------|---------------------|
| KXNEXTAG-29-TBLA | 167   | compound | WH personnel office |

Later that night, President Trump announced he would nominate Todd Blanche 
as Attorney General. The ticker suffix `TBLA` corresponds directly to 
Blanche's initials.

pmwatch detected anomalous trading volume and price divergence in this 
market before the nomination was publicly announced. No user identity 
data was required — only the market-level pattern was sufficient to flag 
it as high-risk.

**Source:** AP: President Trump says he will nominate Todd Blanche to 
serve as attorney general [https://apnews.com/article/trump-blanche-justice-department-86f44c3c01caf89a1dae9d5b5c468551](https://apnews.com/article/trump-blanche-justice-department-86f44c3c01caf89a1dae9d5b5c468551) (June 3, 2026)

> This is pmwatch's first documented detection. The tool identified 
> unusual activity in a politically-sensitive market approximately 
> 10+ hours before the public announcement.

---

## Architecture

    watchmarket_watchlist.json   # 33 monitored series with MNPI risk annotations
    collector.py                 # Polls Kalshi API every 60 min, stores trades + candlesticks
    scorer.py                    # Z-score + block trade + price divergence anomaly detection
    db.py                        # SQLite schema and query helpers
    scheduler.py                 # APScheduler wrapper, runs collect + score automatically
    api.py                       # FastAPI backend serving dashboard and JSON endpoints
    dashboard.html               # Single-file dark-mode monitoring UI

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
    python scheduler.py
    uvicorn api:app --port 8000

Requires Python 3.10+. No API key needed -- all endpoints used are public.

---

## Roadmap

- [ ] Social media correlation (cross-reference X posts with trade anomalies)
- [ ] Government calendar integration (BLS release schedule, FOMC dates)
- [ ] Polymarket support
- [ ] Email/webhook alerts on high-score flags
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
