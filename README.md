# 📡 pmwatch: The Insider Trading Forensics Terminal for Kalshi Prediction Markets

`pmwatch` is a high-signal, local-first quantitative forensics terminal built specifically to track, map, and expose political and macroeconomic insider trading across Kalshi prediction markets.

When policy shifts, regulatory rulings, or economic metrics are drafted behind closed doors, the individuals with access to material non-public information leave an undeniable footprint in alternative asset liquidity pools before press releases ever drop. While traditional retail chart scanners look retroactively at price actions, `pmwatch` actively monitors the dark space where asymmetric capital front-runs public policy announcements.

![pmwatch dashboards](https://raw.githubusercontent.com/lweiss01/pmwatch/main/screenshot.png)

---

## 🔥 Key Visual Telemetry & Signal Drilldowns

The real-time terminal UI drops you into an institutional-grade security operations center:

* **The Insider Leakage Timeline:** Maps real-time volume anomalies directly against authoritative primary federal data streams, isolating the exact minute insider capital pre-positioned itself ahead of public media drops.
* **Whale Flow Exposure Matrix:** Strips away retail noise by running percentile-based clustering to isolate the net positioning shifts of the top 1% of traders.
* **Microstructure Alert Monitor:** A live forensic ticker logging real-time institutional spoofing walls, rapid order cancellations, and wash-trading configurations designed to manipulate order books.

---

## ⚡ Core Architecture: Built for the Alpha

The framework trades standard equity assumptions for raw predictive forensics, running entirely under local-first engineering principles.

### 1. Order Book Manipulation Forensics
Prediction market contracts trade within unique fixed bounds ($0.00 to $1.00), meaning traditional equity technical analysis tools fail completely. `pmwatch` tracks depth-of-book ($L2$) fluctuations via a stateful, thread-safe memory queue:
* **Pre-Cancellation Spoofing Heuristics:** Detects massive liquidity walls designed to panic retail traders, monitoring for patterns where 80%+ of a deep wall is abruptly cancelled within 120 seconds alongside sharp opposite-side execution.
* **Wash Trading Cross-Detection:** Intercepts live trade streams to pair identical execution sizes on opposite sides of the market within a tight 60-second window to flag artificial volume inflation.

### 2. First-Party Information Leakage Mapping
Stop scraping slow, editorialized mainstream media aggregators. `pmwatch` builds a mathematically verifiable information edge by wiring directly into raw government data infrastructure to flag suspected insider moves:
* **The $2\sigma$ Threat Alert:** Tracks rolling contract volume baselines to fire high-priority triggers the second trading volume spikes $\ge 2$ standard deviations above the statistical norm.
* **Authoritative Ingestion Pipes:** Streams live data directly from the Federal Register API (notices, agency drafts, and proposed rules), GPO GovInfo search endpoints (statutes and committee prints), Congress.gov floor activity, and TreasuryDirect auction timelines.
* **1.5x Source Weighting:** Automatically applies a higher confidence metric to raw, unedited government announcements over secondary news aggregation to isolate the true lead time of a leak.

### 3. Dynamic Whale Flow Profiling
Because low-liquidity alternative markets are highly sensitive to hidden capital blocks, `pmwatch` dynamically isolates the top 1% of market participants to map insider accumulation:
* **Contract-Specific Percentiles:** Avoids the trap of hardcoded dollar values. The system calculates the 99th-percentile trade size dynamically per ticker, evaluating a thin legislative contract with the same precision as a massive election line.
* **Hourly Accumulation Rollups:** Tracks clean YES vs. NO directional whale flows to reveal continuous institutional positioning walls.

---

## 🛠️ Performance & Complete Rate-Limit Safety

Designed to operate seamlessly on local hardware, `pmwatch` enforces absolute data privacy and robust protection against API exhaustion:
* **Streaming WebSocket Architecture:** Streams raw public book metrics straight into localized in-memory buffers (`collections.deque(maxlen=200)` per ticker).
* **Zero-REST Polling Footprint:** The 15-second analysis loop runs 100% inside local memory state. It performs precisely one REST warmup call on a cold contract startup, completely eliminating `429 Too Many Requests` limits on volatile trading days.
* **Proactive Eviction:** Garbage collection routines drop historical order book snapshots older than 120 seconds on every tick to maintain a featherweight, static RAM profile.

---

## 🚀 Installation & Quickstart (100% Public Data, No API Keys Needed)

`pmwatch` operates entirely on publicly available data endpoints. You do not need a Kalshi trading account, profile verification, API keys, or authentication credentials to run this terminal.

### Step 1: Download the Application

**Option A: Clone with Git (Recommended)**
Open a PowerShell window, navigate to your projects directory, and run:
```powershell
git clone [https://github.com/lweiss01/pmwatch.git](https://github.com/lweiss01/pmwatch.git)
cd pmwatch
```

**Option B: Download ZIP File**
Go to the repository on GitHub (`https://github.com/lweiss01/pmwatch`), click the green **Code** button, select **Download ZIP**, and extract it to a folder of your choice.

### Step 2: Set Up Environment & Dependencies
Open PowerShell in your project folder. If this is your first time using virtual environments in PowerShell, execute this command to allow running scripts for the current terminal session:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process
```

Now, create your virtual environment, activate it, install requirements, and initialize the database tables:
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python db.py
```
*(This maps the localized SQLite structure under `data/pmwatch.db` for your tracking logs).*

### Step 3: Run the Application (Dual-Window Setup)

To run both the background analyzer and the web dashboard, launch two separate processes in side-by-side PowerShell windows.

**🧵 Window 1: The Background Scheduler Daemon**
Open a new PowerShell window in your `pmwatch` folder and run:
```powershell
.venv\Scripts\Activate.ps1
python scheduler.py
```
*The daemon will spin up. Microstructure checks run every 15 seconds, news ingestion loops every 15 minutes, and whale updates roll up hourly.*

**🖥️ Window 2: The FastAPI Web Server & Dashboard**
Open another separate PowerShell window in your `pmwatch` folder and run:
```powershell
.venv\Scripts\Activate.ps1
python -m uvicorn api:app --port 8080
```

### Step 4: Open the Terminal Dashboard for Insights Galore
Once both windows are running, open your web browser and navigate to:
```text
http://localhost:8080
```
**Insights Galore:** The moment the first collector processing cycle finishes analyzing its batched metrics, the terminal comes completely alive. The UI instantly populates your active $2\sigma$ anomaly feeds, draws interactive SVG whale flow tracking charts, and maps out hidden pre-positioning windows.

*To force immediate execution without waiting for the scheduler intervals, click the glowing **"Manual Run"** button in the top-right header.*

---

## 💻 Tech Stack

* **Core Engine:** Python 3.11+, FastAPI, NumPy, Pandas, Asyncio.
* **Data Storage:** Lightweight, hyper-optimized local SQLite schema managed via `db.py` tracking transaction anomalies and automated data pruning.
* **Front End:** Dark-mode dashboard (`dashboard.html`) utilizing lightweight SVG visualization layers to chart net whale exposure without external tracking scripts.

---

## 🧪 Verification & Test Framework

Built with rigorous Test-Driven Development (TDD) principles to guarantee absolute numerical stability across extreme market turbulence.

Verify the local engine parameters and ensure the setup matches specifications:
```bash
python -m unittest discover tests
```

---

## 📖 The Founding Thesis: Holding Positions of Trust Accountable

Prediction markets are uniquely vulnerable to asymmetric information corruption. Because the legal custodians of public policy data (including legislative aides, economic agency staff, regulatory draft writers, and politicians) possess non-public insights into upcoming announcements, that information inevitably leaves a footprint in the order book when bad actors look to profit from positions of trust.

### The Catalyst: The George Santos Precedent
`pmwatch` was built as a direct response to flagrant exploitation of alternative asset pools by public figures, a reality most notably highlighted by the federal insider trading investigations into former Congressman George Santos. By publicly pumping up the sentiment surrounding an event contract and then using non-public intent to dump tens of thousands of dollars into an opposing asymmetric whale position, bad actors have proven that prediction markets are a playground for manipulation if left unmonitored. 

### Real-World Micro-Case Study: The 9-Hour Edge
During an alpha run, the `pmwatch` engine flagged an extreme structural anomaly. In the dead of night at 12:41 AM, the volume $Z$-score for a high-profile federal nomination contract spiked to a staggering 21.44 $\sigma$ over baseline. This move was driven by a highly concentrated asymmetric whale position shorting the consensus choice by over 13,000 contracts. 

Exactly 9 hours and 13 minutes later, mainstream political media dropped the breaking news bulletin confirming the sudden shift. The wider market scrambled and re-priced instantly, but `pmwatch` had already mapped the information leakage window nearly half a day prior. This sequence proves that political insider trading leaves a clear structural signature before public disclosure.

`pmwatch` turns the chaos of order book stream data into definitive, institutional-grade intelligence, putting the raw mechanics of information transparency completely in your hands.
---

## Disclaimer

pmwatch is a public transparency tool. It detects market-level anomalies using only
publicly available data. It does not identify individual traders, make accusations,
or constitute legal or financial advice. High anomaly scores indicate unusual market
activity that may warrant further investigation -- nothing more.
