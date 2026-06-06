"""
pmwatch — api.py additions
Paste these routes into api.py (after the existing routes).

Also add this import at the top of api.py:
    from cluster_scorer import run_cluster_scorer
"""

# ── Add these routes to api.py ────────────────────────────────────────────────


@app.get("/api/clusters")
def get_clusters(min_count: int = 2, limit: int = 50, active_days: int = 30):
    """Return anomaly clusters sorted by cluster_score.

    A cluster is 2+ anomaly events on the same market within a 72-hour window.
    Higher cluster_score = more suspicious pattern.

    Query params:
        min_count   minimum anomaly events to include (default 2)
        limit       max results (default 50)
        active_days only show clusters with activity in last N days (default 30)
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
