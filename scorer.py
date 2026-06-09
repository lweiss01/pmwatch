import math
import time
import logging
from datetime import datetime, timezone
import db
import config
import numpy as np

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
def already_flagged_recently(ticker: str, hours: int = 6, recent_anomalies: set = None) -> bool:
    """Check if ticker was recently flagged. Pass recent_anomalies set to avoid DB query."""
    if recent_anomalies is not None:
        return ticker in recent_anomalies
    
    # Fallback for backward compatibility
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
    """Legacy function kept for backward compatibility. Use volume_zscore_from_trades instead."""
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
    return volume_zscore_from_trades(all_trades, window_minutes)


def volume_zscore_from_trades(trades: list, window_minutes: int = 120) -> float:
    """Compute volume z-score from pre-fetched trades using fast numpy operations."""
    if not trades:
        return 0.0

    timestamps = np.array([t["created_ts"] for t in trades])
    counts = np.array([t["count_fp"] for t in trades])

    now_ts = int(time.time())
    cutoff_ts = now_ts - (window_minutes * 60)
    window_sec = window_minutes * 60

    # 1. Recent volume
    recent_mask = timestamps >= cutoff_ts
    recent_volume = counts[recent_mask].sum()

    # 2. Baseline volumes
    baseline_mask = timestamps < cutoff_ts
    b_ts = timestamps[baseline_mask]
    b_counts = counts[baseline_mask]

    if len(b_ts) == 0:
        return 0.0

    oldest_ts = b_ts.min()
    offsets = b_ts - oldest_ts
    bin_indices = offsets // window_sec
    num_bins = int((cutoff_ts - oldest_ts) // window_sec)

    if num_bins < 2:
        return 0.0

    valid_bins_mask = (bin_indices >= 0) & (bin_indices < num_bins)
    bin_indices = bin_indices[valid_bins_mask]
    b_counts = b_counts[valid_bins_mask]

    if len(bin_indices) == 0:
        return 0.0

    bin_volumes = np.bincount(bin_indices, weights=b_counts, minlength=num_bins)
    baseline_volumes = bin_volumes[bin_volumes > 0]

    if len(baseline_volumes) < 2:
        return 0.0

    m = baseline_volumes.mean()
    s = baseline_volumes.std(ddof=1)

    if s == 0:
        return 0.0

    return float((recent_volume - m) / s)


# --- Signal 2: Block Trade Ratio ---
def block_trade_signal(ticker: str, window_minutes: int = 120) -> dict:
    """Legacy function kept for backward compatibility. Use block_trade_signal_from_trades instead."""
    trades = db.get_recent_trades(ticker, minutes=window_minutes)
    return block_trade_signal_from_trades(trades)


def block_trade_signal_from_trades(trades: list) -> dict:
    """Compute block trade signal from pre-fetched trades using numpy."""
    if not trades:
        return {"ratio": 0.0, "directional_no": 0.0, "count": 0}

    counts = np.array([t["count_fp"] for t in trades])
    is_blocks = np.array([bool(t["is_block_trade"]) for t in trades])
    taker_sides = np.array([t["taker_side"] for t in trades])

    total_vol = counts.sum()
    if total_vol == 0:
        return {"ratio": 0.0, "directional_no": 0.0, "count": 0}

    block_vol = counts[is_blocks].sum()
    block_no_vol = counts[is_blocks & (taker_sides == "no")].sum()

    return {
        "ratio": float(block_vol / total_vol),
        "directional_no": float(block_no_vol / total_vol),
        "count": len(trades)
    }


# --- Signal 3: Price Divergence ---
def price_divergence(ticker: str, window_minutes: int = 360) -> dict:
    """Legacy function kept for backward compatibility. Use price_divergence_from_trades instead."""
    trades = db.get_recent_trades(ticker, minutes=window_minutes)
    return price_divergence_from_trades(trades)


def price_divergence_from_trades(trades: list) -> dict:
    """Compute price divergence from pre-fetched trades using numpy."""
    if len(trades) < 10:
        return {"max_jump": 0.0, "direction": "none", "price_now": 0.0, "price_before": 0.0}

    times = np.array([t["created_ts"] for t in trades])
    prices = np.array([t["yes_price_dollars"] for t in trades])

    # Sort chronological
    sort_idx = np.argsort(times)
    prices = prices[sort_idx]

    mid = len(prices) // 2
    before_prices = prices[:mid]
    after_prices = prices[mid:]

    before_prices = before_prices[before_prices > 0]
    after_prices = after_prices[after_prices > 0]

    if len(before_prices) == 0 or len(after_prices) == 0:
        return {"max_jump": 0.0, "direction": "none", "price_now": 0.0, "price_before": 0.0}

    price_before = float(before_prices.mean())
    price_now = float(after_prices.mean())
    jump = abs(price_now - price_before)
    direction = "up" if price_now > price_before else "down"

    return {
        "max_jump": jump,
        "direction": direction,
        "price_now": price_now,
        "price_before": price_before
    }


# --- Compound Scorer ---
def score_market(ticker: str, market: dict, trades_7d: list, recent_anomalies: set) -> dict | None:
    if len(trades_7d) < MIN_TRADES_FOR_SCORING:
        return None

    vol_z = volume_zscore_from_trades(trades_7d)
    block = block_trade_signal_from_trades(trades_7d)
    price = price_divergence_from_trades(trades_7d)

    base_score = max(0.0, (vol_z - 1.5) * 15)
    block_modifier = 1.0 + block["ratio"] + block["directional_no"]
    price_bonus = min(30.0, price["max_jump"] * 100)
    raw_score = (base_score * block_modifier) + price_bonus

    if raw_score < YELLOW_SCORE:
        return None

    if already_flagged_recently(ticker, hours=6, recent_anomalies=recent_anomalies):
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
        "detected_time": config.utc_now_iso(),
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
def run_scorer() -> int:
    log.info("=== Scorer run started ===")
    conn = db.get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM watched_markets")
    markets = [dict(r) for r in c.fetchall()]
    
    # Pre-fetch recent anomalies for deduplication (avoids N+1 queries)
    cutoff_ts = int(time.time()) - (6 * 3600)
    c.execute(
        "SELECT ticker FROM anomalies WHERE detected_ts >= ?",
        (cutoff_ts,)
    )
    recent_anomalies = {row["ticker"] for row in c.fetchall()}
    conn.close()

    flagged = 0
    scored = 0

    for market in markets:
        ticker = market["ticker"]
        try:
            # Fetch all trades once per market (7 days = 10080 minutes)
            trades_7d = db.get_recent_trades(ticker, minutes=7 * 24 * 60)
            result = score_market(ticker, market, trades_7d, recent_anomalies)
            scored += 1
            if result:
                db.insert_anomaly(result)
                # Add to set so subsequent markets don't trigger duplicate flag
                recent_anomalies.add(ticker)
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