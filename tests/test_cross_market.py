import os
import sys
import time
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cross_market_scorer
import db


class TestCrossMarketScorer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_db_path = db.DB_PATH
        cls.test_db_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data",
            "test_pmwatch_cross_market.db",
        )

    @classmethod
    def tearDownClass(cls):
        db.DB_PATH = cls.original_db_path
        if os.path.exists(cls.test_db_path):
            try:
                os.remove(cls.test_db_path)
            except OSError:
                pass

    def setUp(self):
        db.DB_PATH = self.test_db_path
        if os.path.exists(self.test_db_path):
            try:
                os.remove(self.test_db_path)
            except OSError:
                pass
        db.init_db()

    def _insert_anomaly(self, **overrides):
        base = {
            "ticker": "KXFED-26DEC-T4.5",
            "market_title": "Fed funds market",
            "series_ticker": "KXFED",
            "risk_group": "FOMC",
            "mnpi_actors": "Fed governors",
            "detected_ts": int(time.time()) - 3600,
            "detected_time": "2026-06-09T12:00:00Z",
            "anomaly_score": 55.0,
            "volume_zscore": 3.0,
            "block_trade_ratio": 0.2,
            "directional_flag": 0.1,
            "trigger_type": "volume_spike",
            "price_before": 0.40,
            "price_current": 0.42,
            "volume_in_window": 500,
            "correlated_event": None,
            "notes": "test",
        }
        base.update(overrides)
        db.insert_anomaly(base)

    def test_multi_series_same_actor_creates_cluster(self):
        now = int(time.time())
        self._insert_anomaly(
            ticker="KXFED-26DEC-T4.5",
            series_ticker="KXFED",
            detected_ts=now - 7200,
            anomaly_score=60.0,
        )
        self._insert_anomaly(
            ticker="KXCPI-26JUN-T3.0",
            series_ticker="KXCPI",
            detected_ts=now - 3600,
            anomaly_score=45.0,
        )

        written = cross_market_scorer.run_cross_market_scorer(lookback_days=7)
        clusters = db.get_cross_market_clusters(limit=10)

        self.assertEqual(written, 1)
        self.assertEqual(len(clusters), 1)
        self.assertIn("KXFED", clusters[0]["series_tickers"])
        self.assertIn("KXCPI", clusters[0]["series_tickers"])
        self.assertEqual(clusters[0]["anomaly_count"], 2)

    def test_same_series_only_no_cross_market_cluster(self):
        now = int(time.time())
        self._insert_anomaly(
            ticker="KXFED-26DEC-T4.5",
            series_ticker="KXFED",
            detected_ts=now - 7200,
        )
        self._insert_anomaly(
            ticker="KXFED-26JUN-T3.50",
            series_ticker="KXFED",
            detected_ts=now - 3600,
        )

        written = cross_market_scorer.run_cross_market_scorer(lookback_days=7)
        clusters = db.get_cross_market_clusters(limit=10)

        self.assertEqual(written, 0)
        self.assertEqual(len(clusters), 0)

    def test_outside_window_splits_clusters(self):
        now = int(time.time())
        self._insert_anomaly(
            ticker="KXFED-26DEC-T4.5",
            series_ticker="KXFED",
            detected_ts=now - (30 * 3600),
            mnpi_actors="Senate Banking Committee",
        )
        self._insert_anomaly(
            ticker="KXCPI-26JUN-T3.0",
            series_ticker="KXCPI",
            detected_ts=now - 3600,
            mnpi_actors="Senate Banking Committee",
        )

        written = cross_market_scorer.run_cross_market_scorer(lookback_days=7)
        clusters = db.get_cross_market_clusters(limit=10)

        self.assertEqual(written, 0)
        self.assertEqual(len(clusters), 0)

    def test_group_time_windows_respects_24h_span(self):
        base_ts = 1_781_000_000
        events = [
            {"detected_ts": base_ts},
            {"detected_ts": base_ts + 3600},
            {"detected_ts": base_ts + (25 * 3600)},
        ]
        groups = cross_market_scorer._group_time_windows(events, window_hours=24)
        self.assertEqual(len(groups), 2)
        self.assertEqual(len(groups[0]), 2)
        self.assertEqual(len(groups[1]), 1)


if __name__ == "__main__":
    unittest.main()
