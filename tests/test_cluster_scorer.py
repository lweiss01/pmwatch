import os
import sys
import unittest
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cluster_scorer
import db


class TestClusterDriftPrune(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_db_path = db.DB_PATH
        cls.test_db_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data",
            "test_pmwatch_cluster.db",
        )
        db.DB_PATH = cls.test_db_path

    @classmethod
    def tearDownClass(cls):
        db.DB_PATH = cls.original_db_path
        if os.path.exists(cls.test_db_path):
            try:
                os.remove(cls.test_db_path)
            except OSError:
                pass

    def setUp(self):
        if os.path.exists(self.test_db_path):
            try:
                os.remove(self.test_db_path)
            except OSError:
                pass
        db.init_db()

    def _anomaly(self, ticker: str, detected_ts: int, score: float = 50.0) -> dict:
        return {
            "ticker": ticker,
            "market_title": "Test market",
            "series_ticker": "KXFED",
            "risk_group": "Fed",
            "mnpi_actors": "",
            "detected_ts": detected_ts,
            "detected_time": "2026-06-01T00:00:00Z",
            "anomaly_score": score,
            "volume_zscore": 4.0,
            "block_trade_ratio": 0.1,
            "directional_flag": 0.0,
            "dominant_side": "neutral",
            "trigger_type": "volume_spike",
        }

    def test_first_seen_ts_prune_removes_drift_duplicates(self):
        ticker = "KXFED-CLUSTER"
        lookback_days = 30
        now_ts = 1_800_000_000
        lookback_sec = lookback_days * 86400
        cutoff_ts = now_ts - lookback_sec

        ts_a = cutoff_ts + 3600
        ts_b = ts_a + 48 * 3600
        ts_c = ts_b + 48 * 3600

        for ts in (ts_a, ts_b, ts_c):
            db.insert_anomaly(self._anomaly(ticker, ts))

        with patch("cluster_scorer.time.time", return_value=now_ts):
            cluster_scorer.run_cluster_scorer(lookback_days=lookback_days)

        conn = db.get_conn()
        rows = conn.execute(
            "SELECT first_seen_ts FROM clusters WHERE ticker = ?", (ticker,)
        ).fetchall()
        conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["first_seen_ts"], ts_a)

        later_ts = now_ts + 2 * 86400
        with patch("cluster_scorer.time.time", return_value=later_ts):
            cluster_scorer.run_cluster_scorer(lookback_days=lookback_days)

        conn = db.get_conn()
        rows = conn.execute(
            "SELECT first_seen_ts FROM clusters WHERE ticker = ?", (ticker,)
        ).fetchall()
        conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["first_seen_ts"], ts_b)


if __name__ == "__main__":
    unittest.main()
