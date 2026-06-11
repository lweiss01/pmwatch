import time
import json
import logging
from collections import deque
import requests
import db
import config

log = logging.getLogger(__name__)

# In-memory trade cache to avoid database bottlenecks
_trade_cache = {}  # ticker -> deque of normalized trades, maxlen=1000

def push_trades(ticker: str, trades: list):
    """Push new trades to the in-memory cache, keeping it ordered and capped."""
    global _trade_cache
    if ticker not in _trade_cache:
        _trade_cache[ticker] = deque(maxlen=1000)
    
    # Store normalized trades
    for t in trades:
        parsed = _parse_trade(t)
        # Avoid duplicate trades by checking IDs
        if parsed["id"] and any(existing["id"] == parsed["id"] for existing in _trade_cache[ticker]):
            continue
        _trade_cache[ticker].append(parsed)

def get_cached_trades(ticker: str, max_age_seconds: int = 300) -> list:
    """Get trades from in-memory cache within the specified time window."""
    global _trade_cache
    if ticker not in _trade_cache:
        return []
        
    now_ts = int(time.time())
    cutoff = now_ts - max_age_seconds
    return [t for t in _trade_cache[ticker] if t["ts"] >= cutoff]

# Stateful tracking of active bid/ask walls in memory
# Key: (ticker, side, price), Value: {"initial_qty": float, "timestamp": int}
_tracked_walls = {}

def clear_tracked_walls():
    """Helper to clear state for testing."""
    global _tracked_walls
    _tracked_walls = {}

def _normalize_book(book):
    """Normalizes both test book structures and raw Kalshi API responses."""
    if "yes" in book and "no" in book:
        return book
    if "yes_bids" in book and "no_bids" in book:
        return {
            "yes": {float(k): float(v) for k, v in book["yes_bids"].items()},
            "no": {float(k): float(v) for k, v in book["no_bids"].items()}
        }
    
    yes_bids = {}
    no_bids = {}
    if "orderbook_fp" in book:
        ob = book["orderbook_fp"]
        for item in ob.get("yes_dollars", []):
            if len(item) == 2:
                yes_bids[float(item[0])] = float(item[1])
        for item in ob.get("no_dollars", []):
            if len(item) == 2:
                no_bids[float(item[0])] = float(item[1])
    return {"yes": yes_bids, "no": no_bids}

def _get_trade_val(t, field):
    """Access fields defensively on both dictionaries and custom objects."""
    if hasattr(t, field):
        return getattr(t, field)
    if isinstance(t, dict):
        return t.get(field)
    return None

def _parse_trade(t):
    """Normalize trade representations."""
    price_val = _get_trade_val(t, "price") or _get_trade_val(t, "yes_price_dollars")
    count_val = _get_trade_val(t, "count") or _get_trade_val(t, "count_fp") or _get_trade_val(t, "qty") or 0
    side_val = _get_trade_val(t, "side") or _get_trade_val(t, "taker_side") or ""
    ts_val = _get_trade_val(t, "ts") or _get_trade_val(t, "created_ts") or 0
    trade_id = _get_trade_val(t, "id") or _get_trade_val(t, "trade_id")
    
    return {
        "id": trade_id,
        "price": float(price_val) if price_val is not None else 0.0,
        "count": float(count_val),
        "side": str(side_val).lower(),
        "ts": int(ts_val)
    }

def detect_spoofing(ticker, current_book, previous_book, recent_trades, threshold=1000.0, now_ts=None):
    """
    Stateful L2 spoofing heuristic:
    1. Identify a large size addition (bid wall) to the orderbook at price P.
    2. Check if this wall is canceled (volume drops by >= 80%) within 120 seconds.
    3. Check if matching trades filled < 20% of the canceled volume.
    4. Check if opposite side trades were executed (opposite pressure) during this period.
    """
    if now_ts is None:
        now_ts = int(time.time())
        
    curr = _normalize_book(current_book)
    prev = _normalize_book(previous_book)
    
    parsed_trades = [_parse_trade(t) for t in recent_trades]
    alerts = []
    
    # 1. Look for additions (Bid Wall insertion)
    for side in ["yes", "no"]:
        for price, qty in curr[side].items():
            prev_qty = prev[side].get(price, 0.0)
            delta = qty - prev_qty
            
            if delta >= threshold:
                key = (ticker, side, price)
                # If we are already tracking a wall, update it or keep older timestamp
                if key not in _tracked_walls:
                    _tracked_walls[key] = {
                        "initial_qty": delta,
                        "timestamp": now_ts
                    }
                    
    # 2. Check tracked walls for cancellations
    keys_to_remove = []
    for key, wall in list(_tracked_walls.items()):
        wall_ticker, side, price = key
        if wall_ticker != ticker:
            continue
            
        # Remove old walls
        if now_ts - wall["timestamp"] > 120:
            keys_to_remove.append(key)
            continue
            
        current_qty = curr[side].get(price, 0.0)
        qty_removed = wall["initial_qty"] - current_qty
        
        # If >= 80% of the wall was removed/canceled
        if qty_removed >= wall["initial_qty"] * 0.8:
            # Check if trades filled this wall
            trades_at_price = sum(
                t["count"] for t in parsed_trades 
                if abs(t["price"] - price) < 0.0001 
                and t["side"] == side 
                and t["ts"] >= wall["timestamp"]
            )
            
            # If volume was canceled, not executed
            if trades_at_price < qty_removed * 0.2:
                # Check for opposite side execution pressure
                opposite_side = "no" if side == "yes" else "yes"
                opp_trades = sum(
                    t["count"] for t in parsed_trades 
                    if t["side"] == opposite_side 
                    and t["ts"] >= wall["timestamp"]
                )
                
                if opp_trades > threshold * 0.5:
                    severity = (qty_removed / wall["initial_qty"]) * (opp_trades / threshold)
                    alerts.append({
                        "ticker": ticker,
                        "alert_type": "spoofing",
                        "severity_score": round(min(100.0, severity * 10), 2),
                        "details": {
                            "price": price,
                            "side": side,
                            "canceled_qty": qty_removed,
                            "opposite_trades_qty": opp_trades
                        }
                    })
                    keys_to_remove.append(key)
                    
    for k in keys_to_remove:
        _tracked_walls.pop(k, None)
        
    return alerts

def detect_wash_trading(ticker, recent_trades, window_seconds=60):
    """
    Wash trading heuristic:
    Identifies matched buy/sell orders of identical/similar quantities executed
    on opposite taker sides within window_seconds.
    """
    parsed_trades = [_parse_trade(t) for t in recent_trades]
    alerts = []
    flagged_ids = set()
    
    for i, t1 in enumerate(parsed_trades):
        if t1["id"] in flagged_ids:
            continue
        for t2 in parsed_trades[i+1:]:
            if t2["id"] in flagged_ids:
                continue
                
            time_diff = abs(t1["ts"] - t2["ts"])
            if time_diff > window_seconds:
                continue
                
            # Quantity matches within 1%
            if t1["count"] <= 0:
                continue
            qty_match = abs(t1["count"] - t2["count"]) / t1["count"] < 0.01
            
            # Opposite taker side
            opp_side = t1["side"] != t2["side"]
            
            if qty_match and opp_side and t1["price"] == t2["price"]:
                flagged_ids.add(t1["id"])
                flagged_ids.add(t2["id"])

                severity = (t1["count"] / 100.0) * (60.0 / max(1, time_diff)) * 0.5

                alerts.append({
                    "ticker": ticker,
                    "alert_type": "wash_trading",
                    "severity_score": round(min(100.0, severity), 2),
                    "details": {
                        "trade_1_id": t1["id"],
                        "trade_2_id": t2["id"],
                        "qty": t1["count"],
                        "price_1": t1["price"],
                        "price_2": t2["price"],
                        "time_gap_sec": time_diff,
                        "confidence": "low",
                    }
                })
    return alerts

# Ticker -> deque of (timestamp, normalized_book)
_orderbook_buffers = {}

def push_orderbook(ticker: str, book: dict, timestamp: int = None):
    """Stream orderbook updates into a local thread-safe memory buffer."""
    global _orderbook_buffers
    if timestamp is None:
        timestamp = int(time.time())
        
    if ticker not in _orderbook_buffers:
        _orderbook_buffers[ticker] = deque(maxlen=200)
        
    norm = _normalize_book(book)
    _orderbook_buffers[ticker].append((timestamp, norm))
    
    # Garbage Collection: Evict snapshots older than or equal to 120 seconds
    cutoff = timestamp - 120
    while _orderbook_buffers[ticker] and _orderbook_buffers[ticker][0][0] <= cutoff:
        _orderbook_buffers[ticker].popleft()

def clear_orderbook_buffers():
    """Helper to clear buffer state for tests."""
    global _orderbook_buffers
    _orderbook_buffers = {}

# Cache of the last seen orderbook snapshot for each ticker to calculate deltas
_previous_orderbooks = {}

def run_microstructure_analysis():
    """
    Background runner: processes local order book buffers to calculate alerts.
    Does NOT execute active REST polling against /orderbooks inside the loop on active periods
    to prevent rate-limit 429 exhaustion. Falls back to single REST warming on cold start.
    """
    log.info("Starting microstructure analysis run...")
    
    # 1. Determine active markets (markets with highest recent trade activity or active clusters)
    conn = db.get_conn()
    c = conn.cursor()
    
    # Fetch tickers of markets with recent anomalies/clusters
    c.execute("""
        SELECT DISTINCT ticker FROM anomalies
        WHERE detected_ts >= ?
        LIMIT 10
    """, (int(time.time()) - 86400,))
    active_tickers = [row["ticker"] for row in c.fetchall()]
    
    # Fetch top volume markets in last 24h if active list is small
    if len(active_tickers) < 5:
        c.execute("""
            SELECT ticker, COUNT(*) as cnt FROM trades
            WHERE created_ts >= ?
            GROUP BY ticker
            ORDER BY cnt DESC
            LIMIT 10
        """, (int(time.time()) - 86400,))
        active_tickers.extend([row["ticker"] for row in c.fetchall() if row["ticker"] not in active_tickers])
        
    conn.close()
    
    if not active_tickers:
        log.info("No active tickers found in DB, skipping run.")
        return
        
    log.info(f"Targeting {len(active_tickers)} active tickers for microstructure calculations.")
    
    from collector import api_get
    
    for ticker in active_tickers:
        # Check buffer status for this ticker
        ticker_buffer = _orderbook_buffers.get(ticker)
        
        # Cold start / Rate-limit protection: If we don't have enough snapshots,
        # perform a single REST warm-up poll. Once the buffer is populated, subsequent
        # loops run 100% from memory without executing active REST polling.
        if not ticker_buffer or len(ticker_buffer) < 2:
            log.info(f"Warming up local orderbook buffer for {ticker} via REST poll...")
            book = api_get(f"/markets/{ticker}/orderbook")
            if book and "orderbook_fp" in book:
                push_orderbook(ticker, book)
            else:
                continue
                
        # Get the latest buffer
        ticker_buffer = _orderbook_buffers.get(ticker)
        if not ticker_buffer or len(ticker_buffer) < 2:
            # Wait for next tick to have at least 2 snapshots
            continue
            
        # Extract from buffer (memory only)
        prev_ts, prev_book = ticker_buffer[-2]
        curr_ts, curr_book = ticker_buffer[-1]
            
        # Fetch recent trades using fast in-memory cache with DB warm-up on cold start
        recent_db_trades = get_cached_trades(ticker, max_age_seconds=300)
        if not recent_db_trades:
            conn = db.get_conn()
            c = conn.cursor()
            c.execute("""
                SELECT * FROM trades
                WHERE ticker = ? AND created_ts >= ?
                ORDER BY created_ts DESC
                LIMIT 500
            """, (ticker, int(time.time()) - 300))
            db_trades = [dict(r) for r in c.fetchall()]
            conn.close()
            if db_trades:
                push_trades(ticker, db_trades)
                recent_db_trades = get_cached_trades(ticker, max_age_seconds=300)
        
        # Calculate dynamic threshold: 95th percentile of depth
        yes_depths = list(curr_book.get("yes", {}).values())
        no_depths = list(curr_book.get("no", {}).values())
        all_depths = yes_depths + no_depths
        
        # Default threshold of 1,000 if book is empty/small
        threshold = 1000.0
        if all_depths:
            all_depths.sort()
            idx = int(len(all_depths) * 0.95)
            threshold = max(500.0, all_depths[idx])
            
        # Check spoofing
        spoof_alerts = detect_spoofing(ticker, curr_book, prev_book, recent_db_trades, threshold=threshold, now_ts=curr_ts)
        for alert in spoof_alerts:
            alert["time_str"] = config.utc_now_iso()
            alert["timestamp"] = int(time.time())
            alert["details"] = json.dumps(alert["details"])
            db.insert_microstructure_alert(alert)
            log.warning(f"!!! SPOOFING ALERT on {ticker} | Severity: {alert['severity_score']} !!!")
        
        # Check wash trading
        wash_alerts = detect_wash_trading(ticker, recent_db_trades, window_seconds=60)
        for alert in wash_alerts:
            alert["time_str"] = config.utc_now_iso()
            alert["timestamp"] = int(time.time())
            alert["details"] = json.dumps(alert["details"])
            db.insert_microstructure_alert(alert)
            log.warning(f"!!! WASH TRADING ALERT on {ticker} | Severity: {alert['severity_score']} !!!")
            
    log.info("Microstructure analysis run complete.")
