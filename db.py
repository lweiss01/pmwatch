import sqlite3
import os
import time

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "pmwatch.db")
BUSY_TIMEOUT_MS = 30_000


def get_conn(timeout: float = 30.0) -> sqlite3.Connection:
    """Open SQLite connection, creating data/ if needed."""
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    return conn


def _close_conn(conn: sqlite3.Connection | None, own_conn: bool) -> None:
    if own_conn and conn is not None:
        conn.close()


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS watched_markets (
            ticker TEXT PRIMARY KEY,
            series_ticker TEXT,
            title TEXT,
            category TEXT,
            risk_group TEXT,
            mnpi_actors TEXT,
            open_time TEXT,
            close_time TEXT,
            volume_fp REAL DEFAULT 0,
            last_price_dollars REAL DEFAULT 0,
            status TEXT,
            last_seen TEXT
        );

        CREATE TABLE IF NOT EXISTS trades (
            trade_id TEXT PRIMARY KEY,
            ticker TEXT,
            count_fp REAL,
            yes_price_dollars REAL,
            no_price_dollars REAL,
            taker_side TEXT,
            is_block_trade INTEGER,
            created_time TEXT,
            created_ts INTEGER
        );

        CREATE TABLE IF NOT EXISTS candlesticks (
            ticker TEXT,
            end_period_ts INTEGER,
            open_dollars REAL,
            close_dollars REAL,
            high_dollars REAL,
            low_dollars REAL,
            volume_fp REAL,
            open_interest_fp REAL,
            PRIMARY KEY (ticker, end_period_ts)
        );

        CREATE TABLE IF NOT EXISTS anomalies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT,
            market_title TEXT,
            series_ticker TEXT,
            risk_group TEXT,
            mnpi_actors TEXT,
            detected_ts INTEGER,
            detected_time TEXT,
            anomaly_score REAL,
            volume_zscore REAL,
            block_trade_ratio REAL,
            directional_flag REAL,
            trigger_type TEXT,
            price_before REAL,
            price_current REAL,
            volume_in_window REAL,
            correlated_event TEXT,
            notes TEXT
        );

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
            has_block_trades INTEGER DEFAULT 0,
            computed_time TEXT,
            computed_ts INTEGER,
            UNIQUE(ticker, first_seen_ts)
        );

        CREATE INDEX IF NOT EXISTS idx_clusters_ticker ON clusters(ticker);
        CREATE INDEX IF NOT EXISTS idx_clusters_score  ON clusters(cluster_score DESC);
        CREATE INDEX IF NOT EXISTS idx_clusters_last   ON clusters(last_seen_ts  DESC);
        CREATE INDEX IF NOT EXISTS idx_clusters_count_last ON clusters(anomaly_count, last_seen_ts DESC);

        CREATE TABLE IF NOT EXISTS cross_market_clusters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mnpi_actors TEXT NOT NULL,
            series_tickers TEXT NOT NULL,
            tickers TEXT NOT NULL,
            window_start_ts INTEGER NOT NULL,
            window_start_time TEXT,
            window_end_ts INTEGER NOT NULL,
            window_end_time TEXT,
            anomaly_count INTEGER NOT NULL,
            peak_score REAL,
            total_score REAL,
            cluster_score REAL,
            computed_time TEXT,
            computed_ts INTEGER,
            UNIQUE(mnpi_actors, window_start_ts)
        );
        CREATE INDEX IF NOT EXISTS idx_cross_market_score
            ON cross_market_clusters(cluster_score DESC);
        CREATE INDEX IF NOT EXISTS idx_cross_market_window
            ON cross_market_clusters(window_end_ts DESC);

        CREATE TABLE IF NOT EXISTS collection_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_time TEXT,
            markets_checked INTEGER,
            trades_collected INTEGER,
            anomalies_flagged INTEGER,
            errors TEXT
        );

        -- News articles database table
        CREATE TABLE IF NOT EXISTS news_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            url TEXT UNIQUE NOT NULL,
            published_time TEXT NOT NULL,
            published_ts INTEGER NOT NULL,
            source TEXT NOT NULL,
            source_type TEXT NOT NULL,
            series_ticker TEXT,
            ingested_ts INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_news_pub_ts ON news_articles(published_ts DESC);
        CREATE INDEX IF NOT EXISTS idx_news_series ON news_articles(series_ticker);

        -- Correlation mapping table
        CREATE TABLE IF NOT EXISTS news_correlations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            anomaly_id INTEGER,
            cluster_first_seen_ts INTEGER,
            ticker TEXT NOT NULL,
            news_id INTEGER NOT NULL,
            lead_time_seconds INTEGER NOT NULL,
            confidence_score REAL NOT NULL,
            notes TEXT,
            FOREIGN KEY(news_id) REFERENCES news_articles(id),
            UNIQUE(ticker, anomaly_id, news_id)
        );

        -- Microstructure alerts table
        CREATE TABLE IF NOT EXISTS microstructure_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            time_str TEXT NOT NULL,
            alert_type TEXT NOT NULL,
            severity_score REAL NOT NULL,
            details TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_micro_ticker ON microstructure_alerts(ticker, timestamp DESC);

        -- Whale hourly rollups table
        CREATE TABLE IF NOT EXISTS whale_hourly_stats (
            ticker TEXT NOT NULL,
            hour_ts INTEGER NOT NULL,
            whale_yes_volume REAL DEFAULT 0,
            whale_no_volume REAL DEFAULT 0,
            net_whale_exposure REAL DEFAULT 0,
            block_trade_count INTEGER DEFAULT 0,
            PRIMARY KEY (ticker, hour_ts)
        );
    """)

    _ensure_watched_markets_columns(conn)
    _ensure_anomaly_columns(conn)
    _ensure_correlation_columns(conn)

    conn.execute("PRAGMA journal_mode=WAL")
    conn.commit()
    conn.close()
    print(f"Database initialized at {DB_PATH}")


def _ensure_watched_markets_columns(conn: sqlite3.Connection) -> None:
    """Add MNPI actor columns to existing databases."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(watched_markets)")}
    if "clearance_tier" not in existing:
        conn.execute(
            "ALTER TABLE watched_markets ADD COLUMN clearance_tier INTEGER DEFAULT 1"
        )
    if "actors_json" not in existing:
        conn.execute("ALTER TABLE watched_markets ADD COLUMN actors_json TEXT")
    if "subject_name" not in existing:
        conn.execute("ALTER TABLE watched_markets ADD COLUMN subject_name TEXT")
    if "rules_primary" not in existing:
        conn.execute("ALTER TABLE watched_markets ADD COLUMN rules_primary TEXT")


def _ensure_anomaly_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(anomalies)")}
    if "subject_name" not in existing:
        conn.execute("ALTER TABLE anomalies ADD COLUMN subject_name TEXT")
    if "score_components_json" not in existing:
        conn.execute("ALTER TABLE anomalies ADD COLUMN score_components_json TEXT")


def _attach_score_components(rows: list) -> list:
    import json as _json

    for row in rows:
        raw = row.get("score_components_json")
        if raw:
            try:
                row["score_components"] = _json.loads(raw)
            except (_json.JSONDecodeError, TypeError):
                row["score_components"] = None
        else:
            row["score_components"] = None
    return rows


def _ensure_correlation_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(news_correlations)")}
    if "explanation_json" not in existing:
        conn.execute("ALTER TABLE news_correlations ADD COLUMN explanation_json TEXT")


def upsert_market(market: dict, conn: sqlite3.Connection | None = None):
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO watched_markets
            (ticker, series_ticker, title, category, risk_group, mnpi_actors,
             clearance_tier, actors_json, subject_name, rules_primary,
             open_time, close_time, volume_fp, last_price_dollars, status, last_seen)
        VALUES
            (:ticker, :series_ticker, :title, :category, :risk_group, :mnpi_actors,
             :clearance_tier, :actors_json, :subject_name, :rules_primary,
             :open_time, :close_time, :volume_fp, :last_price_dollars, :status, :last_seen)
        ON CONFLICT(ticker) DO UPDATE SET
            title = excluded.title,
            mnpi_actors = excluded.mnpi_actors,
            clearance_tier = excluded.clearance_tier,
            actors_json = excluded.actors_json,
            subject_name = excluded.subject_name,
            rules_primary = excluded.rules_primary,
            volume_fp = excluded.volume_fp,
            last_price_dollars = excluded.last_price_dollars,
            status = excluded.status,
            last_seen = excluded.last_seen
    """, {
        "clearance_tier": market.get("clearance_tier", 1),
        "actors_json": market.get("actors_json"),
        "subject_name": market.get("subject_name"),
        "rules_primary": market.get("rules_primary"),
        **market,
    })
    if own_conn:
        conn.commit()
    _close_conn(conn, own_conn)


def insert_trades(trades: list, conn: sqlite3.Connection | None = None) -> int:
    if not trades:
        return 0
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    c = conn.cursor()
    inserted = 0
    for t in trades:
        try:
            c.execute("""
                INSERT OR IGNORE INTO trades
                    (trade_id, ticker, count_fp, yes_price_dollars, no_price_dollars,
                     taker_side, is_block_trade, created_time, created_ts)
                VALUES
                    (:trade_id, :ticker, :count_fp, :yes_price_dollars, :no_price_dollars,
                     :taker_side, :is_block_trade, :created_time, :created_ts)
            """, t)
            inserted += c.rowcount
        except sqlite3.Error:
            pass
    if own_conn:
        conn.commit()
    _close_conn(conn, own_conn)
    return inserted


def insert_candlesticks(candles: list, conn: sqlite3.Connection | None = None) -> int:
    if not candles:
        return 0
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    c = conn.cursor()
    inserted = 0
    for candle in candles:
        try:
            c.execute("""
                INSERT OR REPLACE INTO candlesticks
                    (ticker, end_period_ts, open_dollars, close_dollars,
                     high_dollars, low_dollars, volume_fp, open_interest_fp)
                VALUES
                    (:ticker, :end_period_ts, :open_dollars, :close_dollars,
                     :high_dollars, :low_dollars, :volume_fp, :open_interest_fp)
            """, candle)
            inserted += c.rowcount
        except sqlite3.Error:
            pass
    if own_conn:
        conn.commit()
    _close_conn(conn, own_conn)
    return inserted


def insert_anomaly(anomaly: dict):
    import json as _json

    conn = get_conn()
    c = conn.cursor()
    score_components_json = anomaly.get("score_components_json")
    if score_components_json is None and anomaly.get("score_components") is not None:
        score_components_json = _json.dumps(anomaly["score_components"])
    c.execute("""
        INSERT INTO anomalies
            (ticker, market_title, series_ticker, risk_group, mnpi_actors,
             subject_name,
             detected_ts, detected_time, anomaly_score, volume_zscore,
             block_trade_ratio, directional_flag, trigger_type,
             price_before, price_current, volume_in_window,
             correlated_event, notes, score_components_json)
        VALUES
            (:ticker, :market_title, :series_ticker, :risk_group, :mnpi_actors,
             :subject_name,
             :detected_ts, :detected_time, :anomaly_score, :volume_zscore,
             :block_trade_ratio, :directional_flag, :trigger_type,
             :price_before, :price_current, :volume_in_window,
             :correlated_event, :notes, :score_components_json)
    """, {
        "subject_name": anomaly.get("subject_name"),
        "score_components_json": score_components_json,
        **anomaly,
    })
    conn.commit()
    conn.close()


def get_recent_trades(ticker: str, minutes: int = 120) -> list:
    conn = get_conn()
    c = conn.cursor()
    cutoff_ts = int(time.time()) - (minutes * 60)
    c.execute("""
        SELECT * FROM trades
        WHERE ticker = ? AND created_ts >= ?
        ORDER BY created_ts ASC
    """, (ticker, cutoff_ts))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_candles(ticker: str, limit_minutes: int = 4320) -> list:
    conn = get_conn()
    c = conn.cursor()
    cutoff_ts = int(time.time()) - (limit_minutes * 60)
    c.execute("""
        SELECT * FROM candlesticks
        WHERE ticker = ? AND end_period_ts >= ?
        ORDER BY end_period_ts ASC
    """, (ticker, cutoff_ts))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def log_collection_run(run: dict, conn: sqlite3.Connection | None = None):
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO collection_log
            (run_time, markets_checked, trades_collected, anomalies_flagged, errors)
        VALUES
            (:run_time, :markets_checked, :trades_collected, :anomalies_flagged, :errors)
    """, run)
    if own_conn:
        conn.commit()
    _close_conn(conn, own_conn)


def upsert_cluster(cluster: dict):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO clusters (
            ticker, series_ticker, market_title, risk_group, mnpi_actors,
            first_seen_ts, first_seen_time, last_seen_ts, last_seen_time,
            anomaly_count, peak_score, total_score, directional_consistency,
            score_trend, cluster_score, trigger_types, has_block_trades,
            computed_time, computed_ts
        ) VALUES (
            :ticker, :series_ticker, :market_title, :risk_group, :mnpi_actors,
            :first_seen_ts, :first_seen_time, :last_seen_ts, :last_seen_time,
            :anomaly_count, :peak_score, :total_score, :directional_consistency,
            :score_trend, :cluster_score, :trigger_types, :has_block_trades,
            :computed_time, :computed_ts
        )
        ON CONFLICT(ticker, first_seen_ts) DO UPDATE SET
            last_seen_ts            = excluded.last_seen_ts,
            last_seen_time          = excluded.last_seen_time,
            anomaly_count           = excluded.anomaly_count,
            peak_score              = excluded.peak_score,
            total_score             = excluded.total_score,
            directional_consistency = excluded.directional_consistency,
            score_trend             = excluded.score_trend,
            cluster_score           = excluded.cluster_score,
            trigger_types           = excluded.trigger_types,
            has_block_trades        = excluded.has_block_trades,
            computed_time           = excluded.computed_time,
            computed_ts             = excluded.computed_ts
    """, cluster)
    conn.commit()
    conn.close()


def upsert_clusters_bulk(clusters: list):
    """Bulk upsert multiple clusters in a single transaction."""
    if not clusters:
        return 0
    conn = get_conn()
    c = conn.cursor()
    written = 0
    try:
        for cluster in clusters:
            c.execute("""
                INSERT INTO clusters (
                    ticker, series_ticker, market_title, risk_group, mnpi_actors,
                    first_seen_ts, first_seen_time, last_seen_ts, last_seen_time,
                    anomaly_count, peak_score, total_score, directional_consistency,
                    score_trend, cluster_score, trigger_types, has_block_trades,
                    computed_time, computed_ts
                ) VALUES (
                    :ticker, :series_ticker, :market_title, :risk_group, :mnpi_actors,
                    :first_seen_ts, :first_seen_time, :last_seen_ts, :last_seen_time,
                    :anomaly_count, :peak_score, :total_score, :directional_consistency,
                    :score_trend, :cluster_score, :trigger_types, :has_block_trades,
                    :computed_time, :computed_ts
                )
                ON CONFLICT(ticker, first_seen_ts) DO UPDATE SET
                    last_seen_ts            = excluded.last_seen_ts,
                    last_seen_time          = excluded.last_seen_time,
                    anomaly_count           = excluded.anomaly_count,
                    peak_score              = excluded.peak_score,
                    total_score             = excluded.total_score,
                    directional_consistency = excluded.directional_consistency,
                    score_trend             = excluded.score_trend,
                    cluster_score           = excluded.cluster_score,
                    trigger_types           = excluded.trigger_types,
                    has_block_trades        = excluded.has_block_trades,
                    computed_time           = excluded.computed_time,
                    computed_ts             = excluded.computed_ts
            """, cluster)
            written += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return written


def get_clusters(min_count: int = 2, limit: int = 50, active_days: int = 30) -> list:
    conn = get_conn()
    c = conn.cursor()
    cutoff = int(time.time()) - (active_days * 86400)
    c.execute("""
        SELECT
            ticker, series_ticker, market_title, risk_group, mnpi_actors,
            first_seen_ts, first_seen_time, last_seen_ts, last_seen_time,
            anomaly_count,
            peak_score, total_score, directional_consistency,
            score_trend, cluster_score, trigger_types, has_block_trades
        FROM clusters
        WHERE anomaly_count >= ?
          AND last_seen_ts >= ?
        ORDER BY cluster_score DESC
        LIMIT ?
    """, (min_count, cutoff, limit))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def upsert_cross_market_clusters_bulk(clusters: list) -> int:
    """Bulk upsert cross-market cluster records."""
    if not clusters:
        return 0
    conn = get_conn()
    c = conn.cursor()
    written = 0
    try:
        for cluster in clusters:
            c.execute("""
                INSERT INTO cross_market_clusters (
                    mnpi_actors, series_tickers, tickers,
                    window_start_ts, window_start_time,
                    window_end_ts, window_end_time,
                    anomaly_count, peak_score, total_score, cluster_score,
                    computed_time, computed_ts
                ) VALUES (
                    :mnpi_actors, :series_tickers, :tickers,
                    :window_start_ts, :window_start_time,
                    :window_end_ts, :window_end_time,
                    :anomaly_count, :peak_score, :total_score, :cluster_score,
                    :computed_time, :computed_ts
                )
                ON CONFLICT(mnpi_actors, window_start_ts) DO UPDATE SET
                    series_tickers = excluded.series_tickers,
                    tickers = excluded.tickers,
                    window_end_ts = excluded.window_end_ts,
                    window_end_time = excluded.window_end_time,
                    anomaly_count = excluded.anomaly_count,
                    peak_score = excluded.peak_score,
                    total_score = excluded.total_score,
                    cluster_score = excluded.cluster_score,
                    computed_time = excluded.computed_time,
                    computed_ts = excluded.computed_ts
            """, cluster)
            written += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return written


def get_cross_market_clusters(limit: int = 50, active_days: int = 30) -> list:
    conn = get_conn()
    c = conn.cursor()
    cutoff = int(time.time()) - (active_days * 86400)
    c.execute("""
        SELECT
            mnpi_actors, series_tickers, tickers,
            window_start_ts, window_start_time,
            window_end_ts, window_end_time,
            anomaly_count, peak_score, total_score, cluster_score
        FROM cross_market_clusters
        WHERE window_end_ts >= ?
        ORDER BY cluster_score DESC, window_end_ts DESC
        LIMIT ?
    """, (cutoff, limit))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_ticker_cluster_history(ticker: str) -> list:
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT
            first_seen_time, last_seen_time, anomaly_count,
            peak_score, directional_consistency, score_trend,
            cluster_score, trigger_types, has_block_trades
        FROM clusters
        WHERE ticker = ?
        ORDER BY last_seen_ts DESC
    """, (ticker,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_cluster_events(ticker: str, first_seen_ts: int) -> list:
    """Return all anomaly events belonging to a specific cluster.
    
    A cluster is defined by ticker + first_seen_ts (from clusters table).
    We find all anomalies for that ticker within the cluster's time window.
    """
    conn = get_conn()
    c = conn.cursor()
    
    # First get the cluster's time bounds
    c.execute("""
        SELECT first_seen_ts, last_seen_ts
        FROM clusters
        WHERE ticker = ? AND first_seen_ts = ?
    """, (ticker, first_seen_ts))
    cluster = c.fetchone()
    
    if not cluster:
        conn.close()
        return []
    
    first_ts = cluster["first_seen_ts"]
    last_ts = cluster["last_seen_ts"]
    
    # Get all anomalies for this ticker within the cluster window
    c.execute("""
        SELECT
            id, ticker, market_title, series_ticker, risk_group, mnpi_actors,
            detected_ts, detected_time, anomaly_score, volume_zscore,
            block_trade_ratio, directional_flag, trigger_type,
            price_before, price_current, volume_in_window, notes,
            score_components_json
        FROM anomalies
        WHERE ticker = ?
          AND detected_ts >= ?
          AND detected_ts <= ?
        ORDER BY detected_ts ASC
    """, (ticker, first_ts, last_ts))
    
    rows = _attach_score_components([dict(r) for r in c.fetchall()])
    conn.close()
    return rows


def insert_news_articles(articles: list) -> int:
    if not articles:
        return 0
    conn = get_conn()
    c = conn.cursor()
    inserted = 0
    for a in articles:
        try:
            c.execute("""
                INSERT OR IGNORE INTO news_articles
                    (title, description, url, published_time, published_ts,
                     source, source_type, series_ticker, ingested_ts)
                VALUES
                    (:title, :description, :url, :published_time, :published_ts,
                     :source, :source_type, :series_ticker, :ingested_ts)
            """, a)
            inserted += c.rowcount
        except sqlite3.Error:
            pass
    conn.commit()
    conn.close()
    return inserted


def get_recent_news_articles(limit: int = 50) -> list:
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM news_articles
        ORDER BY published_ts DESC
        LIMIT ?
    """, (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def insert_microstructure_alert(alert: dict):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO microstructure_alerts
            (ticker, timestamp, time_str, alert_type, severity_score, details)
        VALUES
            (:ticker, :timestamp, :time_str, :alert_type, :severity_score, :details)
    """, alert)
    conn.commit()
    conn.close()


def get_microstructure_alerts(limit: int = 50) -> list:
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM microstructure_alerts
        ORDER BY timestamp DESC
        LIMIT ?
    """, (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def insert_whale_stats(stats: dict, conn: sqlite3.Connection | None = None):
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO whale_hourly_stats
            (ticker, hour_ts, whale_yes_volume, whale_no_volume, net_whale_exposure, block_trade_count)
        VALUES
            (:ticker, :hour_ts, :whale_yes_volume, :whale_no_volume, :net_whale_exposure, :block_trade_count)
    """, stats)
    if own_conn:
        conn.commit()
    _close_conn(conn, own_conn)


def get_whale_flow(ticker: str, limit: int = 100) -> list:
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM whale_hourly_stats
        WHERE ticker = ?
        ORDER BY hour_ts ASC
        LIMIT ?
    """, (ticker, limit))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_market_subject_metadata(tickers: list[str]) -> dict[str, dict]:
    """Return per-ticker subject metadata from watched_markets."""
    if not tickers:
        return {}
    conn = get_conn()
    c = conn.cursor()
    placeholders = ",".join("?" for _ in tickers)
    c.execute(
        f"""
        SELECT ticker, title, series_ticker, subject_name, rules_primary
        FROM watched_markets
        WHERE ticker IN ({placeholders})
        """,
        tickers,
    )
    rows = {row["ticker"]: dict(row) for row in c.fetchall()}
    conn.close()
    return rows


def update_market_subject_metadata(
    ticker: str,
    *,
    subject_name: str | None = None,
    rules_primary: str | None = None,
) -> None:
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        UPDATE watched_markets
        SET subject_name = COALESCE(?, subject_name),
            rules_primary = COALESCE(?, rules_primary)
        WHERE ticker = ?
        """,
        (subject_name, rules_primary, ticker),
    )
    conn.commit()
    conn.close()


def clear_correlations() -> int:
    """Delete all news-to-anomaly correlation rows. Returns rows removed."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM news_correlations")
    count = c.fetchone()[0]
    c.execute("DELETE FROM news_correlations")
    conn.commit()
    conn.close()
    return count


def cap_anomaly_scores(max_score: float = 100.0) -> int:
    """Cap stored anomaly scores at max_score. Returns rows updated."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        UPDATE anomalies
        SET anomaly_score = ?
        WHERE anomaly_score > ?
        """,
        (max_score, max_score),
    )
    updated = c.rowcount
    conn.commit()
    conn.close()
    return updated


def insert_correlation(correlation: dict):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT OR IGNORE INTO news_correlations
            (anomaly_id, cluster_first_seen_ts, ticker, news_id, lead_time_seconds,
             confidence_score, notes, explanation_json)
        VALUES
            (:anomaly_id, :cluster_first_seen_ts, :ticker, :news_id, :lead_time_seconds,
             :confidence_score, :notes, :explanation_json)
    """, {
        "anomaly_id": correlation["anomaly_id"],
        "cluster_first_seen_ts": correlation["cluster_first_seen_ts"],
        "ticker": correlation["ticker"],
        "news_id": correlation["news_id"],
        "lead_time_seconds": correlation["lead_time_seconds"],
        "confidence_score": correlation["confidence_score"],
        "notes": correlation["notes"],
        "explanation_json": correlation.get("explanation_json"),
    })
    conn.commit()
    conn.close()


def get_correlations(limit: int = 50) -> list:
    import json as _json

    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT 
            c.*, 
            a.anomaly_score, 
            a.volume_zscore, 
            a.market_title, 
            n.title as news_title, 
            n.url as news_url, 
            n.published_time as news_time, 
            n.source as news_source,
            n.source_type as news_source_type
        FROM news_correlations c
        LEFT JOIN anomalies a ON c.anomaly_id = a.id
        JOIN news_articles n ON c.news_id = n.id
        ORDER BY c.confidence_score DESC, c.id DESC
        LIMIT ?
    """, (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    for row in rows:
        raw = row.get("explanation_json")
        if raw:
            try:
                row["explanation"] = _json.loads(raw)
            except (_json.JSONDecodeError, TypeError):
                row["explanation"] = None
        else:
            row["explanation"] = None
    return rows


def prune_historical_data(order_book_days: int = 14, trade_days: int = 30):
    import time as _time
    now_ts = int(_time.time())
    conn = get_conn()
    c = conn.cursor()
    
    # Prune old trades
    cutoff_trades = now_ts - (trade_days * 86400)
    c.execute("DELETE FROM trades WHERE created_ts < ?", (cutoff_trades,))
    
    # Prune old microstructure alerts
    cutoff_alerts = now_ts - (order_book_days * 86400)
    c.execute("DELETE FROM microstructure_alerts WHERE timestamp < ?", (cutoff_alerts,))
    
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
