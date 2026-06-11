import math
import time
import logging
from datetime import datetime, timezone
import db
import config
import numpy as np

log = logging.getLogger(__name__)


# --- Thresholds (defaults; runtime reads config.get_* helpers) ---
YELLOW_SCORE = config.DEFAULT_SCORER_THRESHOLDS["yellow_score"]
RED_SCORE = config.DEFAULT_SCORER_THRESHOLDS["red_score"]
MIN_TRADES_FOR_SCORING = 10
MAX_ANOMALY_SCORE = 100.0
DEDUP_HOURS = config.DEFAULT_SCORER_THRESHOLDS["dedup_hours"]
SCORE_DELTA_THRESHOLD = config.DEFAULT_SCORER_THRESHOLDS["score_delta_threshold"]
CLEARANCE_MULTIPLIER = {1: 1.0, 2: 1.1, 3: 1.25}
BLOCK_WINDOW_MINUTES = 120
PRICE_WINDOW_MINUTES = 360
SCORER_FORMULA_VERSION = 4
QUIET_MARKET_SPIKE_Z = 4.0


def clearance_multiplier(market: dict) -> float:
    tier = int(market.get("clearance_tier") or 1)
    return CLEARANCE_MULTIPLIER.get(tier, 1.0)


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


def filter_trades_by_window(trades: list, window_minutes: int, now_ts: int | None = None) -> list:
    """Return trades with created_ts within the last window_minutes."""
    if not trades:
        return []
    cutoff = (now_ts if now_ts is not None else int(time.time())) - (window_minutes * 60)
    return [t for t in trades if t.get("created_ts", 0) >= cutoff]


def volume_in_window(trades: list, window_minutes: int = BLOCK_WINDOW_MINUTES) -> float:
    """Sum contract volume in the recent window."""
    window_trades = filter_trades_by_window(trades, window_minutes)
    return float(sum(t.get("count_fp", 0) for t in window_trades))


# --- Deduplication ---
def should_suppress_repeat(
    ticker: str,
    new_score: float,
    recent_scores: dict[str, float] | None = None,
    min_delta_pct: float | None = None,
) -> bool:
    """Suppress re-flag only when a recent score exists and the new score isn't materially higher."""
    if min_delta_pct is None:
        min_delta_pct = config.get_score_delta_threshold()
    if recent_scores is not None:
        last_score = recent_scores.get(ticker)
        if last_score is not None and new_score < last_score * (1 + min_delta_pct):
            return True
        return False

    conn = db.get_conn()
    c = conn.cursor()
    cutoff = int(time.time()) - (config.get_dedup_hours() * 3600)
    c.execute(
        """
        SELECT MAX(anomaly_score) AS max_score
        FROM anomalies
        WHERE ticker = ? AND detected_ts >= ?
        """,
        (ticker, cutoff),
    )
    row = c.fetchone()
    conn.close()
    if row is None or row["max_score"] is None:
        return False
    return new_score < float(row["max_score"]) * (1 + min_delta_pct)


def already_flagged_recently(
    ticker: str,
    hours: int | None = None,
    recent_anomalies: set = None,
) -> bool:
    """Legacy check: ticker flagged within window. Prefer should_suppress_repeat for score-delta logic."""
    if hours is None:
        hours = config.get_dedup_hours()
    if recent_anomalies is not None:
        return ticker in recent_anomalies

    conn = db.get_conn()
    c = conn.cursor()
    cutoff = int(time.time()) - (hours * 3600)
    c.execute(
        "SELECT anomaly_score FROM anomalies WHERE ticker=? AND detected_ts >= ? ORDER BY detected_ts DESC LIMIT 1",
        (ticker, cutoff),
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


def volume_zscore_from_trades(
    trades: list,
    window_minutes: int = 120,
    now_ts: int | None = None,
) -> float:
    """Robust volume z-score: median/MAD with mean/std and quiet-market fallbacks."""
    if not trades:
        return 0.0

    now_ts = now_ts if now_ts is not None else int(time.time())
    timestamps = np.array([t["created_ts"] for t in trades])
    counts = np.array([t["count_fp"] for t in trades])

    cutoff_ts = now_ts - (window_minutes * 60)
    window_sec = window_minutes * 60

    recent_mask = timestamps >= cutoff_ts
    recent_volume = float(counts[recent_mask].sum())

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
    # Include zero-volume bins in baseline (thin overnight markets).
    baseline_volumes = bin_volumes.astype(float)

    if len(baseline_volumes) < 2:
        return 0.0

    median = float(np.median(baseline_volumes))
    mad = float(np.median(np.abs(baseline_volumes - median)))

    if mad > 0:
        return float(0.6745 * (recent_volume - median) / mad)

    mean = float(baseline_volumes.mean())
    std = float(baseline_volumes.std(ddof=1))

    if std > 0:
        return float((recent_volume - mean) / std)

    min_vol = config.get_quiet_market_min_volume()
    if recent_volume >= min_vol:
        return config.get_quiet_market_spike_z()

    return 0.0


# --- Signal 2: Block Trade Ratio ---
def block_trade_signal(ticker: str, window_minutes: int = 120) -> dict:
    """Legacy function kept for backward compatibility. Use block_trade_signal_from_trades instead."""
    trades = db.get_recent_trades(ticker, minutes=window_minutes)
    return block_trade_signal_from_trades(trades)


def block_trade_signal_from_trades(trades: list) -> dict:
    """Compute block trade signal from pre-fetched trades using numpy."""
    empty = {
        "ratio": 0.0,
        "directional_no": 0.0,
        "directional_yes": 0.0,
        "directional_imbalance": 0.0,
        "dominant_side": "neutral",
        "count": 0,
    }
    if not trades:
        return empty

    counts = np.array([t["count_fp"] for t in trades])
    is_blocks = np.array([bool(t["is_block_trade"]) for t in trades])
    taker_sides = np.array([t["taker_side"] for t in trades])

    total_vol = counts.sum()
    if total_vol == 0:
        return empty

    block_vol = counts[is_blocks].sum()
    block_no_vol = counts[is_blocks & (taker_sides == "no")].sum()
    block_yes_vol = counts[is_blocks & (taker_sides == "yes")].sum()
    imbalance = float(abs(block_yes_vol - block_no_vol) / total_vol)

    if block_yes_vol > block_no_vol * 1.1:
        dominant_side = "yes"
    elif block_no_vol > block_yes_vol * 1.1:
        dominant_side = "no"
    else:
        dominant_side = "neutral"

    return {
        "ratio": float(block_vol / total_vol),
        "directional_no": float(block_no_vol / total_vol),
        "directional_yes": float(block_yes_vol / total_vol),
        "directional_imbalance": imbalance,
        "dominant_side": dominant_side,
        "count": len(trades),
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


def compute_anomaly_window_start_ts(
    trades_7d: list,
    now_ts: int,
    window_minutes: int = BLOCK_WINDOW_MINUTES,
) -> int:
    """Earliest trade timestamp in the recent volume window (trade-anchored timing)."""
    window_trades = filter_trades_by_window(trades_7d, window_minutes, now_ts)
    if not window_trades:
        return now_ts
    return min(t["created_ts"] for t in window_trades)


def oi_zscore_from_candles(
    candles: list,
    window_minutes: int = 120,
    now_ts: int | None = None,
) -> float:
    """Open-interest delta z-score from pre-fetched candlesticks."""
    if len(candles) < 3:
        return 0.0

    now_ts = now_ts if now_ts is not None else int(time.time())
    cutoff_ts = now_ts - (window_minutes * 60)
    recent = [c for c in candles if c["end_period_ts"] >= cutoff_ts]
    baseline = [c for c in candles if c["end_period_ts"] < cutoff_ts]
    if not recent or len(baseline) < 2:
        return 0.0

    recent_oi = float(recent[-1].get("open_interest_fp") or 0)
    baseline_oi = [float(c.get("open_interest_fp") or 0) for c in baseline]
    if not baseline_oi:
        return 0.0

    deltas = np.diff(np.array(baseline_oi, dtype=float))
    if len(deltas) < 1:
        return 0.0

    recent_delta = recent_oi - float(baseline_oi[-1])
    median = float(np.median(deltas))
    mad = float(np.median(np.abs(deltas - median)))
    if mad > 0:
        return float(0.6745 * (recent_delta - median) / mad)
    std = float(deltas.std(ddof=1)) if len(deltas) > 1 else 0.0
    if std > 0:
        return float((recent_delta - deltas.mean()) / std)
    return 0.0 if recent_delta <= 0 else 2.0


def oi_zscore(ticker: str, window_minutes: int = 120) -> float:
    """Legacy wrapper: fetch candles and delegate to oi_zscore_from_candles."""
    candles = db.get_candles(ticker, limit_minutes=7 * 24 * 60)
    return oi_zscore_from_candles(candles, window_minutes=window_minutes)


def adaptive_yellow_threshold(ticker: str) -> float | None:
    """Per-market adaptive yellow threshold from score_history at current formula version."""
    if not config.get_adaptive_threshold_enabled():
        return None

    conn = db.get_conn()
    cutoff = int(time.time()) - (config.get_adaptive_history_days() * 86400)
    rows = conn.execute(
        """
        SELECT anomaly_score FROM score_history
        WHERE ticker = ? AND formula_version = ? AND run_ts >= ?
          AND anomaly_score IS NOT NULL
        ORDER BY run_ts DESC
        LIMIT 500
        """,
        (ticker, SCORER_FORMULA_VERSION, cutoff),
    ).fetchall()
    conn.close()

    if len(rows) < config.get_adaptive_min_samples():
        return None

    scores = sorted(float(r["anomaly_score"]) for r in rows)
    idx = int(len(scores) * config.get_adaptive_percentile() / 100.0)
    idx = min(max(idx, 0), len(scores) - 1)
    return scores[idx]


# --- Compound Scorer ---
def evaluate_market(
    ticker: str,
    market: dict,
    trades_7d: list,
    recent_scores: dict[str, float] | None = None,
    run_ts: int | None = None,
    candles_7d: list | None = None,
) -> dict:
    """Score a market and return a record suitable for score_history (always)."""
    run_ts = run_ts if run_ts is not None else int(time.time())
    base_record = {
        "run_ts": run_ts,
        "ticker": ticker,
        "series_ticker": market.get("series_ticker"),
        "formula_version": SCORER_FORMULA_VERSION,
        "flagged": False,
        "anomaly_score": None,
        "score_components": None,
        "reject_reason": None,
        "anomaly_payload": None,
    }

    if len(trades_7d) < MIN_TRADES_FOR_SCORING:
        base_record["reject_reason"] = "min_trades"
        return base_record

    now_ts = run_ts
    trades_block = filter_trades_by_window(trades_7d, BLOCK_WINDOW_MINUTES, now_ts)
    trades_price = filter_trades_by_window(trades_7d, PRICE_WINDOW_MINUTES, now_ts)

    vol_z_raw = volume_zscore_from_trades(
        trades_7d, window_minutes=BLOCK_WINDOW_MINUTES, now_ts=now_ts
    )
    vol_z = min(vol_z_raw, config.get_vol_z_cap())
    block = block_trade_signal_from_trades(trades_block)
    price = price_divergence_from_trades(trades_price)
    oi_z = oi_zscore_from_candles(
        candles_7d or [], window_minutes=BLOCK_WINDOW_MINUTES, now_ts=now_ts
    )
    oi_bonus = min(
        config.get_oi_max_bonus(),
        max(0.0, oi_z) * config.get_oi_z_weight(),
    )
    anomaly_window_start_ts = compute_anomaly_window_start_ts(
        trades_7d, now_ts, BLOCK_WINDOW_MINUTES
    )

    clearance_mult = clearance_multiplier(market)
    base_score = max(0.0, (vol_z - 1.5) * 15) * clearance_mult
    block_modifier = 1.0 + block["ratio"] + block["directional_imbalance"]
    price_bonus = min(30.0, price["max_jump"] * 100)
    raw_score = (base_score * block_modifier) + price_bonus + oi_bonus
    normalized_score = min(MAX_ANOMALY_SCORE, raw_score)

    static_yellow = config.get_yellow_score()
    adaptive_yellow = adaptive_yellow_threshold(ticker)
    effective_yellow = static_yellow
    if adaptive_yellow is not None:
        effective_yellow = max(static_yellow, adaptive_yellow)

    trigger = "compound" if base_score > 0 and price_bonus > 0 else \
              "volume_spike" if base_score > 0 else "price_divergence"

    score_components = {
        "formula_version": SCORER_FORMULA_VERSION,
        "volume_zscore_raw": round(vol_z_raw, 3),
        "volume_zscore": round(vol_z, 3),
        "oi_zscore": round(oi_z, 3),
        "oi_bonus": round(oi_bonus, 2),
        "base_score": round(base_score, 2),
        "block_modifier": round(block_modifier, 3),
        "price_bonus": round(price_bonus, 2),
        "clearance_multiplier": round(clearance_mult, 2),
        "raw_score": round(raw_score, 2),
        "normalized_score": round(normalized_score, 2),
        "trigger_type": trigger,
        "block_window_minutes": BLOCK_WINDOW_MINUTES,
        "price_window_minutes": PRICE_WINDOW_MINUTES,
        "dominant_side": block["dominant_side"],
        "directional_imbalance": round(block["directional_imbalance"], 3),
        "anomaly_window_start_ts": anomaly_window_start_ts,
        "static_yellow_threshold": static_yellow,
        "adaptive_yellow_threshold": adaptive_yellow,
        "effective_yellow_threshold": effective_yellow,
    }

    base_record["anomaly_score"] = round(normalized_score, 2)
    base_record["score_components"] = score_components

    if normalized_score < effective_yellow:
        base_record["reject_reason"] = "below_yellow"
        return base_record

    if should_suppress_repeat(ticker, normalized_score, recent_scores):
        base_record["reject_reason"] = "dedup_suppressed"
        return base_record

    base_record["flagged"] = True
    base_record["reject_reason"] = None
    base_record["anomaly_payload"] = {
        "ticker": ticker,
        "market_title": market.get("title", ""),
        "series_ticker": market.get("series_ticker", ""),
        "risk_group": market.get("risk_group", ""),
        "mnpi_actors": market.get("mnpi_actors", ""),
        "subject_name": market.get("subject_name", ""),
        "detected_ts": run_ts,
        "detected_time": config.timestamp_to_iso(run_ts),
        "anomaly_window_start_ts": anomaly_window_start_ts,
        "anomaly_score": round(normalized_score, 2),
        "volume_zscore": round(vol_z, 3),
        "block_trade_ratio": round(block["ratio"], 3),
        "directional_flag": round(block["directional_no"], 3),
        "directional_imbalance": round(block["directional_imbalance"], 3),
        "dominant_side": block["dominant_side"],
        "trigger_type": trigger,
        "price_before": round(price["price_before"], 4),
        "price_current": round(price["price_now"], 4),
        "volume_in_window": round(volume_in_window(trades_7d, BLOCK_WINDOW_MINUTES), 2),
        "correlated_event": None,
        "score_components": score_components,
        "notes": (
            f"vol_z={vol_z:.2f} price_jump={price['max_jump']:.4f} "
            f"direction={price['direction']} | "
            f"base={base_score:.1f} block_mod={block_modifier:.2f} "
            f"price_bonus={price_bonus:.1f} clearance={clearance_mult:.2f}"
        ),
    }
    return base_record


def score_market(
    ticker: str,
    market: dict,
    trades_7d: list,
    recent_scores: dict[str, float] | None = None,
    candles_7d: list | None = None,
) -> dict | None:
    evaluation = evaluate_market(
        ticker, market, trades_7d, recent_scores, candles_7d=candles_7d
    )
    return evaluation.get("anomaly_payload")


# --- Run Scorer Against All Watched Markets ---
def run_scorer() -> int:
    log.info("=== Scorer run started ===")
    conn = db.get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM watched_markets")
    markets = [dict(r) for r in c.fetchall()]
    
    # Pre-fetch recent anomaly scores for score-delta deduplication
    cutoff_ts = int(time.time()) - (config.get_dedup_hours() * 3600)
    c.execute(
        """
        SELECT ticker, MAX(anomaly_score) AS max_score
        FROM anomalies
        WHERE detected_ts >= ?
        GROUP BY ticker
        """,
        (cutoff_ts,),
    )
    recent_scores = {row["ticker"]: float(row["max_score"]) for row in c.fetchall()}
    conn.close()

    flagged = 0
    scored = 0
    run_ts = int(time.time())
    score_records: list[dict] = []

    for market in markets:
        ticker = market["ticker"]
        try:
            trades_7d = db.get_recent_trades(ticker, minutes=7 * 24 * 60)
            candles_7d = db.get_candles(ticker, limit_minutes=7 * 24 * 60)
            evaluation = evaluate_market(
                ticker,
                market,
                trades_7d,
                recent_scores,
                run_ts=run_ts,
                candles_7d=candles_7d,
            )
            score_records.append(evaluation)
            scored += 1
            result = evaluation.get("anomaly_payload")
            if result:
                db.insert_anomaly(result)
                recent_scores[ticker] = max(
                    recent_scores.get(ticker, 0.0),
                    result["anomaly_score"],
                )
                flagged += 1
                log.info(
                    f"FLAGGED {ticker} | score={result['anomaly_score']} "
                    f"| {result['trigger_type']} | {market['risk_group']}"
                )
        except Exception as e:
            log.error(f"Scorer error on {ticker}: {e}")

    if score_records:
        db.insert_score_history_bulk(score_records)

    log.info(f"=== Scorer complete: {scored} scored, {flagged} flagged ===")
    return flagged


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )
    db.init_db()
    run_scorer()