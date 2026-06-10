import time
import logging
import requests
from datetime import datetime, timezone
from pathlib import Path
import db
import config
from watchlist_loader import load_watchlist

# --- Config ---
BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
LOG_PATH = Path(__file__).parent / "logs" / "collector.log"
RATE_LIMIT_DELAY = 0.4  # seconds between requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)


# --- API Helpers ---
def api_get(path: str, params: dict = None, retries: int = 3) -> dict:
    url = f"{BASE_URL}{path}"
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=10)
            if r.status_code == 429:
                wait = 5 * (attempt + 1)
                log.warning(f"Rate limited on {path}, waiting {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            time.sleep(RATE_LIMIT_DELAY)
            return r.json()
        except requests.RequestException as e:
            log.error(f"Request failed ({attempt+1}/{retries}): {path} -- {e}")
            time.sleep(0.5)
    return {}


# --- Market Fetcher ---
def fetch_markets_for_series(series_ticker: str) -> list:
    data = api_get("/markets", params={
        "series_ticker": series_ticker,
        "status": "open",
        "limit": 100
    })
    return data.get("markets", [])


# --- Trade Fetcher ---
def fetch_recent_trades(ticker: str, max_pages: int = 10) -> list:
    trades = []
    cursor = ""
    pages = 0

    while pages < max_pages:
        params = {"ticker": ticker, "limit": 1000}
        if cursor:
            params["cursor"] = cursor

        data = api_get("/markets/trades", params=params)
        raw = data.get("trades", [])

        if not raw:
            break

        for t in raw:
            created_time = t.get("created_time", "")
            try:
                dt = config.parse_iso_datetime(created_time)
                created_ts = int(dt.timestamp())
            except Exception:
                created_ts = 0

            trades.append({
                "trade_id": t.get("trade_id", ""),
                "ticker": t.get("ticker", ticker),
                "count_fp": float(t.get("count_fp", 0)),
                "yes_price_dollars": float(t.get("yes_price_dollars", 0)),
                "no_price_dollars": float(t.get("no_price_dollars", 0)),
                "taker_side": t.get("taker_side", ""),
                "is_block_trade": 1 if t.get("is_block_trade", False) else 0,
                "created_time": created_time,
                "created_ts": created_ts
            })
        
        pages += 1
        cursor = data.get("cursor", "")
        if not cursor or len(raw) < 1000:
            break

    return trades

# --- Whale Stats Rollup ---
def rollup_whale_stats_for_market(ticker: str):
    """Calculate whale trade statistics and save hourly rollups to the database."""
    conn = db.get_conn()
    c = conn.cursor()
    
    # 1. Fetch all trades to calculate 99th percentile
    c.execute("SELECT count_fp FROM trades WHERE ticker = ? ORDER BY count_fp ASC", (ticker,))
    rows = c.fetchall()
    if not rows:
        conn.close()
        return
        
    sizes = [r[0] for r in rows]
    idx = int(len(sizes) * 0.99)
    # Threshold is 99th percentile, with a floor of 1000 contracts
    threshold = max(1000.0, sizes[idx])
    
    # 2. Fetch trades in the last 7 days to roll up hourly stats
    cutoff_ts = int(time.time()) - (7 * 86400)
    c.execute("""
        SELECT created_ts, count_fp, taker_side, is_block_trade 
        FROM trades 
        WHERE ticker = ? AND created_ts >= ?
    """, (ticker, cutoff_ts))
    recent_trades = c.fetchall()
    conn.close()
    
    if not recent_trades:
        return
        
    # Group by hour timestamp
    hourly_groups = {}
    for created_ts, count_fp, taker_side, is_block_trade in recent_trades:
        hour_ts = (created_ts // 3600) * 3600
        if hour_ts not in hourly_groups:
            hourly_groups[hour_ts] = []
        hourly_groups[hour_ts].append((count_fp, taker_side, is_block_trade))
        
    # Aggregate and insert
    for hour_ts, trades_in_hour in hourly_groups.items():
        yes_vol = 0.0
        no_vol = 0.0
        blocks = 0
        
        for count_fp, taker_side, is_block_trade in trades_in_hour:
            is_whale = (count_fp >= threshold) or (is_block_trade == 1)
            if is_block_trade == 1:
                blocks += 1
            if is_whale:
                if taker_side == "yes":
                    yes_vol += count_fp
                elif taker_side == "no":
                    no_vol += count_fp
                    
        stats = {
            "ticker": ticker,
            "hour_ts": hour_ts,
            "whale_yes_volume": yes_vol,
            "whale_no_volume": no_vol,
            "net_whale_exposure": yes_vol - no_vol,
            "block_trade_count": blocks
        }
        db.insert_whale_stats(stats)


# --- Candlestick Fetcher ---
def fetch_candlesticks(series_ticker: str, market_ticker: str) -> list:
    # Try series-based URL first, fall back to event-based
    paths = [
        f"/series/{series_ticker}/markets/{market_ticker}/candlesticks",
        f"/markets/{market_ticker}/candlesticks"  # simpler fallback
    ]
    
    for path in paths:
        url = f"{BASE_URL}{path}"
        try:
            r = requests.get(url, params={"period_interval": 60}, timeout=10)
            if r.status_code == 400:
                log.debug(f"Candlesticks 400 for {market_ticker} on {path}")
                continue  # try next path
            if r.status_code == 404:
                log.debug(f"Candlesticks 404 for {market_ticker} on {path}")
                continue
            if r.status_code == 429:
                log.warning(f"Candlesticks rate limited for {market_ticker}, waiting 5s")
                time.sleep(5)
                continue
            r.raise_for_status()
            time.sleep(RATE_LIMIT_DELAY)
            raw = r.json().get("candlesticks", [])
            if not raw:
                log.debug(f"Candlesticks empty response for {market_ticker} on {path}")
                continue
            candles = []
            for c in raw:
                price = c.get("price", {})
                candles.append({
                    "ticker": market_ticker,
                    "end_period_ts": c.get("end_period_ts", 0),
                    "open_dollars": float(price.get("open_dollars", 0)),
                    "close_dollars": float(price.get("close_dollars", 0)),
                    "high_dollars": float(price.get("high_dollars", 0)),
                    "low_dollars": float(price.get("low_dollars", 0)),
                    "volume_fp": float(c.get("volume_fp", 0)),
                    "open_interest_fp": float(c.get("open_interest_fp", 0))
                })
            log.debug(f"Candlesticks OK for {market_ticker}: {len(candles)} bars via {path}")
            return candles
        except requests.RequestException as e:
            log.debug(f"Candlesticks request error for {market_ticker} on {path}: {e}")
            continue
    
    log.debug(f"Candlesticks unavailable for {market_ticker} (all paths exhausted)")
    return []  # silently skip if both paths fail


def run_collection(fast: bool = False):
    run_start = config.utc_now_iso()
    log.info(f"=== Collection run started (fast={fast}): {run_start} ===")

    watchlist = load_watchlist()
    total_markets = 0
    total_trades = 0
    errors = []

    if fast:
        # Fast mode: Get already known open/active markets from DB to avoid fetching watchlist series
        conn = db.get_conn()
        c = conn.cursor()
        c.execute("SELECT ticker, series_ticker, category, risk_group, mnpi_actors FROM watched_markets WHERE status = 'active'")
        active_markets = [dict(row) for row in c.fetchall()]
        conn.close()

        # Group cached markets by series
        active_markets_map = {}
        for m in active_markets:
            series = m["series_ticker"] if "series_ticker" in m else m["ticker"].split("-")[0]
            if series not in active_markets_map:
                active_markets_map[series] = []
            active_markets_map[series].append(m)
    else:
        active_markets_map = None

    for entry in watchlist:
        series = entry["series"]
        
        # If fast mode, only process if we have known open markets for this series
        if fast and (active_markets_map is None or series not in active_markets_map):
            continue

        log.info(f"Fetching markets for series: {series} ({entry['name']})")

        try:
            if fast and active_markets_map is not None:
                # Use cached markets from DB
                markets = active_markets_map[series]
            else:
                markets = fetch_markets_for_series(series)

            if not markets:
                log.info(f"  No open markets for {series}")
                continue

            for market in markets:
                ticker = market.get("ticker", "")
                if not ticker:
                    continue

                if not fast:
                    # Store market in DB
                    db.upsert_market({
                        "ticker": ticker,
                        "series_ticker": series,
                        "title": market.get("title", ""),
                        "category": entry["category"],
                        "risk_group": entry["name"],
                        "mnpi_actors": entry["mnpi_actors"],
                        "clearance_tier": entry["clearance_tier"],
                        "actors_json": entry["actors_json"],
                        "subject_name": market.get("yes_sub_title", ""),
                        "rules_primary": market.get("rules_primary", ""),
                        "open_time": market.get("open_time", ""),
                        "close_time": market.get("close_time", ""),
                        "volume_fp": float(market.get("volume_fp", 0)),
                        "last_price_dollars": float(market.get("last_price_dollars", 0)),
                        "status": market.get("status", ""),
                        "last_seen": run_start
                    })

                # Fetch and store trades (only 1 page in fast mode)
                trades = fetch_recent_trades(ticker, max_pages=1 if fast else 10)
                inserted = db.insert_trades(trades)
                total_trades += inserted
                
                # Push trades to microstructure watcher in-memory cache
                try:
                    import microstructure_watcher
                    microstructure_watcher.push_trades(ticker, trades)
                except Exception as ex:
                    log.error(f"Failed to push trades to cache: {ex}")

                # Calculate and rollup whale stats
                try:
                    rollup_whale_stats_for_market(ticker)
                except Exception as ex:
                    log.error(f"Failed to rollup whale stats for {ticker}: {ex}")

                # Fetch and store candlesticks (skip in fast mode)
                if not fast:
                    candles = fetch_candlesticks(series, ticker)
                    db.insert_candlesticks(candles)
                else:
                    candles = []

                log.info(f"  {ticker}: {len(trades)} trades, {len(candles)} candles, {inserted} new")
                total_markets += 1

        except Exception as e:
            msg = f"Error processing {series}: {e}"
            log.error(msg)
            errors.append(msg)

    # Log the run
    db.log_collection_run({
        "run_time": run_start,
        "markets_checked": total_markets,
        "trades_collected": total_trades,
        "anomalies_flagged": 0,
        "errors": "; ".join(errors) if errors else None
    })

    log.info(f"=== Run complete: {total_markets} markets, {total_trades} trades ===")


if __name__ == "__main__":
    db.init_db()
    run_collection()