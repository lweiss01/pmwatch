import math
import time
import logging
from datetime import datetime, timezone
import db

log = logging.getLogger(__name__)


# --- Thresholds ---
YELLOW_SCORE = 25.0
RED_SCORE = 60.0
MIN_TRADES_FOR_SCORING = 10


def mean(values: list) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def stdev(values: list) -> float:
    if len(values) < 2:
        return 0.0
    m = mean(values)
    variance = sum((x - m) ** 2 for x in values) / (len(values) - 1)
    return math.sqrt(variance)


# --- Deduplication ---
def already_flagged_recently(ticker: str, hours: int = 6) -> bool:
    conn = db.get_conn()
    c = conn.cursor()
    cutoff = int(time.time()) - (hours * 3600)
    c.execute(
        "SELECT anomaly_score FROM anomalies WHERE ticker=? AND detected_ts >= ? ORDER BY detected_ts DESC LIMIT 1",
        (ticker, cutoff)
    )
    row = c.fetchone()
    conn.close()
    return row is not None


# --- Signal 1: Volume Z-Score ---
def volume_zscore(ticker: str, window_minutes: int = 120) -> float:
    all_candles = db.get_candles(ticker, limit_minutes=7 * 24 * 60)

    if len(all_candles) >= 3:
        volumes = [c["volume_fp"] for c in all_candles if c["volume_fp"] > 0]
        if len(volumes) < 3:
            return 0.0
        recent_vol = volumes[-1]
        baseline = volumes[:-1]
        m = mean(baseline)
        s = stdev(baseline)
        if s == 0:
            return 0.0
        return (recent_vol - m) / s

    # Fall back to raw trades
    all_trades = db.get_recent_trades(ticker, minutes=7 * 24 * 60)
    if not all_trades:
        return 0.0

    now_ts = int(time.time())
    cutoff_ts = now_ts - (window_minutes * 60)
    window_sec = window_minutes * 60

    recent_volume = sum(
        t["count_fp"] for t in all_trades
        if t["created_ts"] >= cutoff_ts
    )

    oldest_ts = min(t["created_ts"] for t in all_trades)
    baseline_volumes = []

    for start in range(oldest_ts, cutoff_ts - window_sec, window_sec):
        end = start + window_sec
        vol = sum(
            t["count_fp"] for t in all_trades
            if start <= t["created_ts"] < end
        )
        if vol > 0:
            baseline_volumes.append(vol)

    if len(baseline_volumes) < 2:
        return 0.0

    m = mean(baseline_volumes)
    s = stdev(baseline_volumes)

    if s == 0:
        return 0.0

    return (recent_volume - m) / s


# --- Signal 2: Block Trade Ratio ---
def block_trade_signal(ticker: str, window_minutes: int = 120) -> dict:
    trades = db.get_recent_trades(ticker, minutes=window_minutes)

    if not trades:
        return {"ratio": 0.0, "directional_no": 0.0, "count": 0}

    total_vol = sum(t["count_fp"] for t in trades)
    block_vol = sum(t["count_fp"] for t in trades if t["is_block_trade"])
    block_no_vol = sum(
        t["count_fp"] for t in trades
        if t["is_block_trade"] and t["taker_side"] == "no"
    )

    return {
        "ratio": block_vol / total_vol if total_vol > 0 else 0.0,
        "directional_no": block_no_vol / total_vol if total_vol > 0 else 0.0,
        "count": len(trades)
    }


# --- Signal 3: Price Divergence ---
def price_divergence(ticker: str, window_minutes: int = 360) -> dict:
    trades = db.get_recent_trades(ticker, minutes=window_minutes)

    if len(trades) < 10:
        return {"max_jump": 0.0, "direction": "none", "price_now": 0.0, "price_before": 0.0}

    trades = sorted(trades, key=lambda t: t["created_ts"])
    mid = len(trades) // 2

    before_prices = [t["yes_price_dollars"] for t in trades[:mid] if t["yes_price_dollars"] > 0]
    after_prices = [t["yes_price_dollars"] for t in trades[mid:] if t["yes_price_dollars"] > 0]

    if not before_prices or not after_prices:
        return {"max_jump": 0.0, "direction": "none", "price_now": 0.0, "price_before": 0.0}

    price_before = mean(before_prices)
    price_now = mean(after_prices)
    jump = abs(price_now - price_before)
    direction = "up" if price_now > price_before else "down"

    return {
        "max_jump": jump,
        "direction": direction,
        "price_now": price_now,
        "price_before": price_before
    }


# --- Compound Scorer ---
def score_market(ticker: str, market: dict) -> dict | None:
    trades = db.get_recent_trades(ticker, minutes=7 * 24 * 60)

    if len(trades) < MIN_TRADES_FOR_SCORING:
        return None

    vol_z = volume_zscore(ticker)
    block = block_trade_signal(ticker)
    price = price_divergence(ticker)

    base_score = max(0.0, (vol_z - 1.5) * 15)
    block_modifier = 1.0 + block["ratio"] + block["directional_no"]
    price_bonus = min(30.0, price["max_jump"] * 100)
    raw_score = (base_score * block_modifier) + price_bonus

    if raw_score < YELLOW_SCORE:
        return None

    if already_flagged_recently(ticker, hours=6):
        return None

    trigger = "compound" if base_score > 0 and price_bonus > 0 else \
              "volume_spike" if base_score > 0 else "price_divergence"

    return {
        "ticker": ticker,
        "market_title": market.get("title", ""),
        "series_ticker": market.get("series_ticker", ""),
        "risk_group": market.get("risk_group", ""),
        "mnpi_actors": market.get("mnpi_actors", ""),
        "detected_ts": int(time.time()),
        "detected_time": datetime.now(timezone.utc).isoformat(),
        "anomaly_score": round(raw_score, 2),
        "volume_zscore": round(vol_z, 3),
        "block_trade_ratio": round(block["ratio"], 3),
        "directional_flag": round(block["directional_no"], 3),
        "trigger_type": trigger,
        "price_before": round(price["price_before"], 4),
        "price_current": round(price["price_now"], 4),
        "volume_in_window": block["count"],
        "correlated_event": None,
        "notes": f"vol_z={vol_z:.2f} price_jump={price['max_jump']:.4f} direction={price['direction']}"
    }


# --- Run Scorer Against All Watched Markets ---
def run_scorer():
    log.info("=== Scorer run started ===")
    conn = db.get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM watched_markets")
    markets = [dict(r) for r in c.fetchall()]
    conn.close()

    flagged = 0
    scored = 0

    for market in markets:
        ticker = market["ticker"]
        try:
            result = score_market(ticker, market)
            scored += 1
            if result:
                db.insert_anomaly(result)
                flagged += 1
                log.info(
                    f"FLAGGED {ticker} | score={result['anomaly_score']} "
                    f"| {result['trigger_type']} | {market['risk_group']}"
                )
        except Exception as e:
            log.error(f"Scorer error on {ticker}: {e}")

    log.info(f"=== Scorer complete: {scored} scored, {flagged} flagged ===")
    return flagged


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )
    db.init_db()
    run_scorer()