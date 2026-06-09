import json
import time
import logging
import requests
from datetime import datetime, timezone
from pathlib import Path
import db

# --- Config ---
BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
WATCHLIST_PATH = Path(__file__).parent / "watchmarket_watchlist.json"
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


# --- Watchlist Loader ---
def load_watchlist() -> list:
    with open(WATCHLIST_PATH, "r", encoding="utf-8-sig") as f:
        raw = json.load(f)

    markets = []
    for category, entries in raw.items():
        for entry in entries:
            markets.append({
                "series": entry["series"],
                "name": entry["name"],
                "risk": entry["risk"],
                "category": category
            })
    return markets


# --- Market Fetcher ---
def fetch_markets_for_series(series_ticker: str) -> list:
    data = api_get("/markets", params={
        "series_ticker": series_ticker,
        "status": "open",
        "limit": 100
    })
    return data.get("markets", [])


# --- Trade Fetcher ---
def fetch_recent_trades(ticker: str) -> list:
    trades = []
    cursor = ""
    pages = 0
    max_pages = 10  # cap at 10000 trades per market per run

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
                dt = datetime.fromisoformat(created_time.replace("Z", "+00:00"))
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

        cursor = data.get("cursor", "")
        pages += 1

        if not cursor:
            break

    return trades

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


# --- Main Collection Run ---
def run_collection():
    run_start = datetime.now(timezone.utc).isoformat()
    log.info(f"=== Collection run started: {run_start} ===")

    watchlist = load_watchlist()
    total_markets = 0
    total_trades = 0
    errors = []

    for entry in watchlist:
        series = entry["series"]
        log.info(f"Fetching markets for series: {series} ({entry['name']})")

        try:
            markets = fetch_markets_for_series(series)

            if not markets:
                log.info(f"  No open markets for {series}")
                continue

            for market in markets:
                ticker = market.get("ticker", "")
                if not ticker:
                    continue

                # Store market in DB
                db.upsert_market({
                    "ticker": ticker,
                    "series_ticker": series,
                    "title": market.get("title", ""),
                    "category": entry["category"],
                    "risk_group": entry["name"],
                    "mnpi_actors": entry["risk"],
                    "open_time": market.get("open_time", ""),
                    "close_time": market.get("close_time", ""),
                    "volume_fp": float(market.get("volume_fp", 0)),
                    "last_price_dollars": float(market.get("last_price_dollars", 0)),
                    "status": market.get("status", ""),
                    "last_seen": run_start
                })

                # Fetch and store trades
                trades = fetch_recent_trades(ticker)
                inserted = db.insert_trades(trades)
                total_trades += inserted

                # Fetch and store candlesticks
                candles = fetch_candlesticks(series, ticker)
                db.insert_candlesticks(candles)

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