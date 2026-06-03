import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "pmwatch.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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

        CREATE TABLE IF NOT EXISTS collection_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_time TEXT,
            markets_checked INTEGER,
            trades_collected INTEGER,
            anomalies_flagged INTEGER,
            errors TEXT
        );
    """)

    conn.commit()
    conn.close()
    print(f"Database initialized at {DB_PATH}")


def upsert_market(market: dict):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO watched_markets
            (ticker, series_ticker, title, category, risk_group, mnpi_actors,
             open_time, close_time, volume_fp, last_price_dollars, status, last_seen)
        VALUES
            (:ticker, :series_ticker, :title, :category, :risk_group, :mnpi_actors,
             :open_time, :close_time, :volume_fp, :last_price_dollars, :status, :last_seen)
        ON CONFLICT(ticker) DO UPDATE SET
            volume_fp = excluded.volume_fp,
            last_price_dollars = excluded.last_price_dollars,
            status = excluded.status,
            last_seen = excluded.last_seen
    """, market)
    conn.commit()
    conn.close()


def insert_trades(trades: list):
    if not trades:
        return 0
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
    conn.commit()
    conn.close()
    return inserted


def insert_candlesticks(candles: list):
    if not candles:
        return 0
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
    conn.commit()
    conn.close()
    return inserted


def insert_anomaly(anomaly: dict):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO anomalies
            (ticker, market_title, series_ticker, risk_group, mnpi_actors,
             detected_ts, detected_time, anomaly_score, volume_zscore,
             block_trade_ratio, directional_flag, trigger_type,
             price_before, price_current, volume_in_window,
             correlated_event, notes)
        VALUES
            (:ticker, :market_title, :series_ticker, :risk_group, :mnpi_actors,
             :detected_ts, :detected_time, :anomaly_score, :volume_zscore,
             :block_trade_ratio, :directional_flag, :trigger_type,
             :price_before, :price_current, :volume_in_window,
             :correlated_event, :notes)
    """, anomaly)
    conn.commit()
    conn.close()


def get_recent_trades(ticker: str, minutes: int = 120) -> list:
    conn = get_conn()
    c = conn.cursor()
    import time
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
    import time
    cutoff_ts = int(time.time()) - (limit_minutes * 60)
    c.execute("""
        SELECT * FROM candlesticks
        WHERE ticker = ? AND end_period_ts >= ?
        ORDER BY end_period_ts ASC
    """, (ticker, cutoff_ts))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def log_collection_run(run: dict):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO collection_log
            (run_time, markets_checked, trades_collected, anomalies_flagged, errors)
        VALUES
            (:run_time, :markets_checked, :trades_collected, :anomalies_flagged, :errors)
    """, run)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()