"""
pmwatch — db.py additions
Paste these into db.py:

  1. Add the clusters table to the CREATE TABLE block inside init_db()
  2. Add the three functions below to the bottom of db.py
"""

# ── 1. Add this CREATE TABLE block inside init_db() (after the anomalies table) ──

CLUSTERS_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS clusters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT NOT NULL,
        series_ticker TEXT,
        market_title TEXT,
        risk_group TEXT,
        mnpi_actors TEXT,
        first_seen_ts INTEGER,
        first_seen_time TEXT,
        last_seen_ts INTEGER,
        last_seen_time TEXT,
        anomaly_count INTEGER NOT NULL,
        peak_score REAL,
        total_score REAL,
        directional_consistency REAL,
        score_trend REAL,
        cluster_score REAL,
        trigger_types TEXT,
        computed_time TEXT,
        computed_ts INTEGER,
        UNIQUE(ticker, first_seen_ts)
    );

    CREATE INDEX IF NOT EXISTS idx_clusters_ticker ON clusters(ticker);
    CREATE INDEX IF NOT EXISTS idx_clusters_score  ON clusters(cluster_score DESC);
    CREATE INDEX IF NOT EXISTS idx_clusters_last   ON clusters(last_seen_ts  DESC);
"""

# ── 2. Add these three functions to the bottom of db.py ──────────────────────


def upsert_cluster(cluster: dict):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO clusters (
            ticker, series_ticker, market_title, risk_group, mnpi_actors,
            first_seen_ts, first_seen_time, last_seen_ts, last_seen_time,
            anomaly_count, peak_score, total_score, directional_consistency,
            score_trend, cluster_score, trigger_types,
            computed_time, computed_ts
        ) VALUES (
            :ticker, :series_ticker, :market_title, :risk_group, :mnpi_actors,
            :first_seen_ts, :first_seen_time, :last_seen_ts, :last_seen_time,
            :anomaly_count, :peak_score, :total_score, :directional_consistency,
            :score_trend, :cluster_score, :trigger_types,
            :computed_time, :computed_ts
        )
        ON CONFLICT(ticker, first_seen_ts) DO UPDATE SET
            last_seen_ts          = excluded.last_seen_ts,
            last_seen_time        = excluded.last_seen_time,
            anomaly_count         = excluded.anomaly_count,
            peak_score            = excluded.peak_score,
            total_score           = excluded.total_score,
            directional_consistency = excluded.directional_consistency,
            score_trend           = excluded.score_trend,
            cluster_score         = excluded.cluster_score,
            trigger_types         = excluded.trigger_types,
            computed_time         = excluded.computed_time,
            computed_ts           = excluded.computed_ts
    """, cluster)
    conn.commit()
    conn.close()


def get_clusters(min_count: int = 2, limit: int = 50, active_days: int = 30) -> list:
    """Return clusters sorted by cluster_score, optionally filtered to recent activity."""
    import time as _time
    conn = get_conn()
    c = conn.cursor()
    cutoff = int(_time.time()) - (active_days * 86400)
    c.execute("""
        SELECT
            ticker, series_ticker, market_title, risk_group, mnpi_actors,
            first_seen_time, last_seen_time, anomaly_count,
            peak_score, total_score, directional_consistency,
            score_trend, cluster_score, trigger_types
        FROM clusters
        WHERE anomaly_count >= ?
          AND last_seen_ts >= ?
        ORDER BY cluster_score DESC
        LIMIT ?
    """, (min_count, cutoff, limit))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_ticker_cluster_history(ticker: str) -> list:
    """Return all clusters for a specific ticker, newest first."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT
            first_seen_time, last_seen_time, anomaly_count,
            peak_score, directional_consistency, score_trend,
            cluster_score, trigger_types
        FROM clusters
        WHERE ticker = ?
        ORDER BY last_seen_ts DESC
    """, (ticker,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows
