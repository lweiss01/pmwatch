import sqlite3
import logging
import json
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import db
import collector
import scorer
from cluster_scorer import run_cluster_scorer
import config

log = logging.getLogger(__name__)

SCHEDULER_INTERVAL = config.get_scheduler_interval()

app = FastAPI(title="pmwatch", description="Prediction Market Anomaly Monitor")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

HTML_PATH = Path(__file__).parent / "dashboard.html"


@app.get("/", response_class=HTMLResponse)
def root():
    return HTML_PATH.read_text(encoding="utf-8")


@app.get("/api/anomalies")
def get_anomalies(limit: int = 50):
    conn = db.get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT
            id, ticker, market_title, series_ticker, risk_group,
            mnpi_actors, detected_time, anomaly_score, volume_zscore,
            block_trade_ratio, trigger_type, price_before,
            price_current, volume_in_window, notes
        FROM anomalies
        ORDER BY detected_ts DESC
        LIMIT ?
    """, (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return JSONResponse(rows)


@app.get("/api/markets")
def get_markets():
    conn = db.get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT
            ticker, series_ticker, title, risk_group,
            mnpi_actors, volume_fp, last_price_dollars,
            close_time, last_seen
        FROM watched_markets
        ORDER BY volume_fp DESC
        LIMIT 100
    """)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return JSONResponse(rows)


@app.get("/api/stats")
def get_stats():
    conn = db.get_conn()
    c = conn.cursor()

    c.execute("SELECT COUNT(*) FROM watched_markets")
    total_markets = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM trades")
    total_trades = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM anomalies")
    total_anomalies = c.fetchone()[0]

    c.execute("""
        SELECT COUNT(*) FROM anomalies
        WHERE detected_ts >= strftime('%s', 'now') - 86400
    """)
    anomalies_24h = c.fetchone()[0]

    c.execute("""
        SELECT run_time, markets_checked, trades_collected, anomalies_flagged
        FROM collection_log
        ORDER BY id DESC LIMIT 1
    """)
    row = c.fetchone()
    last_run = dict(row) if row else {}

    # Calculate next run time (using config interval)
    next_run = None
    if last_run:
        try:
            last_run_dt = config.parse_iso_datetime(last_run["run_time"])
            next_run_dt = last_run_dt.timestamp() + (config.get_scheduler_interval() * 60)
            next_run = config.timestamp_to_iso(next_run_dt)
        except Exception:
            pass

    c.execute("""
        SELECT risk_group, COUNT(*) as count, MAX(anomaly_score) as max_score
        FROM anomalies
        GROUP BY risk_group
        ORDER BY max_score DESC
    """)
    by_category = [dict(r) for r in c.fetchall()]

    conn.close()

    return JSONResponse({
        "total_markets": total_markets,
        "total_trades": total_trades,
        "total_anomalies": total_anomalies,
        "anomalies_24h": anomalies_24h,
        "by_category": by_category,
        "last_run": last_run.get("run_time") if last_run else None,
        "next_run": next_run,
        "scheduler_interval_minutes": config.get_scheduler_interval()
    })


@app.get("/api/market/{ticker}/trades")
def get_market_trades(ticker: str, limit: int = 200):
    conn = db.get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT trade_id, ticker, count_fp, yes_price_dollars,
               taker_side, is_block_trade, created_time, created_ts
        FROM trades
        WHERE ticker = ?
        ORDER BY created_ts DESC
        LIMIT ?
    """, (ticker, limit))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return JSONResponse(rows)


@app.get("/api/clusters")
def get_clusters(min_count: int = 2, limit: int = 50, active_days: int = 30):
    """Return anomaly clusters sorted by cluster_score.

    A cluster is 2+ anomaly events on the same market within a 72-hour window.
    Higher cluster_score = more suspicious pattern.
    """
    rows = db.get_clusters(min_count=min_count, limit=limit, active_days=active_days)
    return JSONResponse(rows)


@app.get("/api/market/{ticker}/clusters")
def get_ticker_clusters(ticker: str):
    """Return the full cluster history for a specific market ticker."""
    rows = db.get_ticker_cluster_history(ticker)
    return JSONResponse(rows)


@app.post("/api/clusters/refresh")
def refresh_clusters(lookback_days: int = 30):
    """Re-run the cluster scorer against the anomaly history.

    Call this after a new scorer run to update cluster records.
    Returns the number of clusters written.
    """
    count = run_cluster_scorer(lookback_days=lookback_days)
    return JSONResponse({"clusters_written": count, "lookback_days": lookback_days})


@app.post("/api/collector/trigger")
def trigger_collection():
    """Manually trigger a collection + scoring run.
    
    Runs asynchronously in the background. Returns immediately
    with the run status. Check /api/stats for last_run update.
    """
    import threading

    def run_background():
        try:
            log.info("Manual collection triggered via API (fast=True)")
            collector.run_collection(fast=True)
            scorer.run_scorer()
            run_cluster_scorer()
        except Exception as e:
            log.error(f"Manual collection failed: {e}")

    thread = threading.Thread(target=run_background, daemon=True)
    thread.start()

    return JSONResponse({
        "status": "started",
        "message": "Manual collection run triggered. Updates will appear in a few seconds!",
        "triggered_at": config.utc_now_iso()
    })


@app.get("/api/cluster/{ticker}/{first_seen_ts}/events")
def get_cluster_events(ticker: str, first_seen_ts: int):
    """Return all anomaly events within a specific cluster.
    
    Path params:
        ticker         Market ticker (e.g. KXNEXTAG-29-TBLA)
        first_seen_ts  Cluster start timestamp (from clusters table)
    
    Returns list of anomaly events with full signal breakdown,
    ordered chronologically.
    """
    rows = db.get_cluster_events(ticker, first_seen_ts)
    return JSONResponse(rows)


@app.get("/api/microstructure/alerts")
def get_microstructure_alerts(limit: int = 50):
    """Return recent microstructure alerts (spoofing and wash trading)."""
    rows = db.get_microstructure_alerts(limit=limit)
    # Deserialize details JSON for the response
    for row in rows:
        if "details" in row and isinstance(row["details"], str):
            try:
                row["details"] = json.loads(row["details"])
            except Exception:
                pass
    return JSONResponse(rows)


@app.get("/api/correlations")
def get_correlations(limit: int = 50):
    """Return recent news-to-anomaly event correlations."""
    rows = db.get_correlations(limit=limit)
    return JSONResponse(rows)


@app.get("/api/market/{ticker}/whale-flow")
def get_whale_flow(ticker: str, limit: int = 100):
    """Return hourly whale trade rollups for charting."""
    rows = db.get_whale_flow(ticker, limit=limit)
    return JSONResponse(rows)
