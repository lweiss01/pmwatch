import unittest
import sys
import os
import time

# Add parent directory to path to import api
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import db

try:
    from fastapi.testclient import TestClient
    import api
    client = TestClient(api.app)
except Exception:
    client = None

class TestAPI(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(client, "FastAPI app or TestClient could not be initialized")
        
        # Configure test DB
        self.original_db_path = db.DB_PATH
        self.test_db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "test_pmwatch_api.db")
        db.DB_PATH = self.test_db_path
        if os.path.exists(self.test_db_path):
            try:
                os.remove(self.test_db_path)
            except OSError:
                pass
        db.init_db()

    def tearDown(self):
        if os.path.exists(self.test_db_path):
            try:
                os.remove(self.test_db_path)
            except OSError:
                pass
        db.DB_PATH = self.original_db_path

    def test_microstructure_alerts_endpoint(self):
        """Verify that GET /api/microstructure/alerts returns inserted alerts."""
        # Seed an alert
        alert = {
            "ticker": "KXCABOUT-26DEC",
            "timestamp": int(time.time()),
            "time_str": "2026-06-09T12:00:00Z",
            "alert_type": "spoofing",
            "severity_score": 85.0,
            "details": '{"price": 0.45}'
        }
        db.insert_microstructure_alert(alert)
        
        response = client.get("/api/microstructure/alerts")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["ticker"], "KXCABOUT-26DEC")
        self.assertEqual(data[0]["alert_type"], "spoofing")

    def test_correlations_endpoint(self):
        """Verify that GET /api/correlations returns matching correlations."""
        # Setup article
        article = {
            "title": "Cabinet changes expected",
            "description": "White house spokesperson confirms resignations.",
            "url": "https://example.gov/resigns",
            "published_time": "2026-06-09T12:00:00Z",
            "published_ts": int(time.time()),
            "source": "Congress.gov",
            "source_type": "primary_gov",
            "series_ticker": "KXCABOUT",
            "ingested_ts": int(time.time())
        }
        db.insert_news_articles([article])
        news_id = db.get_recent_news_articles(limit=1)[0]["id"]
        
        # Seed correlation
        correlation = {
            "anomaly_id": 12,
            "cluster_first_seen_ts": int(time.time()) - 3600,
            "ticker": "KXCABOUT-26DEC",
            "news_id": news_id,
            "lead_time_seconds": 3600,
            "confidence_score": 90.0,
            "notes": "Matched keywords: cabinet",
            "explanation_json": (
                '{"decision":"accept","score_type":"leakage",'
                '"sub_scores":{"market_microstructure_score":70.0}}'
            ),
        }
        db.insert_correlation(correlation)
        
        response = client.get("/api/correlations")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["ticker"], "KXCABOUT-26DEC")
        self.assertEqual(data[0]["confidence_score"], 90.0)
        self.assertIsNotNone(data[0].get("explanation"))
        self.assertEqual(data[0]["explanation"]["score_type"], "leakage")

    def test_settings_get_and_put(self):
        original_path = config.CONFIG_PATH
        original_backup = config.CONFIG_BACKUP_PATH
        cfg_path = os.path.join(
            os.path.dirname(self.test_db_path),
            "test_pmwatch_settings_config.json",
        )
        backup_path = cfg_path + ".bak"
        config.CONFIG_PATH = cfg_path
        config.CONFIG_BACKUP_PATH = backup_path
        try:
            if os.path.exists(cfg_path):
                os.remove(cfg_path)
            config.save_config({
                "scheduler_interval_minutes": 30,
                "scheduled_events": {
                    "enabled": True,
                    "events": [
                        {
                            "label": "FOMC rate decision",
                            "series": ["KXFED"],
                            "dates": ["2026-06-18"],
                            "window_hours_before": 48,
                            "window_hours_after": 6,
                            "temporal_floor": 0.85,
                        }
                    ],
                },
            })

            get_resp = client.get("/api/settings")
            self.assertEqual(get_resp.status_code, 200)
            settings = get_resp.json()
            self.assertEqual(settings["scheduler_interval_minutes"], 30)
            self.assertTrue(settings["scheduled_events"]["enabled"])

            put_resp = client.put("/api/settings", json={
                "scheduler_interval_minutes": 45,
                "scheduled_events": {
                    "enabled": False,
                    "events": [
                        {
                            "label": "FOMC rate decision",
                            "temporal_floor": 0.9,
                            "window_hours_before": 40,
                        }
                    ],
                },
            })
            self.assertEqual(put_resp.status_code, 200)
            body = put_resp.json()
            self.assertEqual(body["status"], "ok")
            self.assertIn("scheduler", body["restart_required"])
            self.assertFalse(body["settings"]["scheduled_events"]["enabled"])
        finally:
            config.CONFIG_PATH = original_path
            config.CONFIG_BACKUP_PATH = original_backup
            if os.path.exists(cfg_path):
                os.remove(cfg_path)
            if os.path.exists(backup_path):
                os.remove(backup_path)

    def test_settings_threshold_put_round_trip(self):
        original_path = config.CONFIG_PATH
        original_backup = config.CONFIG_BACKUP_PATH
        cfg_path = os.path.join(
            os.path.dirname(self.test_db_path),
            "test_pmwatch_threshold_config.json",
        )
        backup_path = cfg_path + ".bak"
        config.CONFIG_PATH = cfg_path
        config.CONFIG_BACKUP_PATH = backup_path
        try:
            config.save_config({"scheduler_interval_minutes": 30})
            put_resp = client.put("/api/settings", json={
                "correlation": {"min_confidence": 20.0, "min_match_quality": 0.5},
                "matcher": {"min_ingest_quality": 0.45},
                "scorer": {
                    "yellow_score": 30.0,
                    "red_score": 65.0,
                    "dedup_hours": 3,
                    "score_delta_threshold": 0.25,
                },
            })
            self.assertEqual(put_resp.status_code, 200)
            body = put_resp.json()
            self.assertEqual(body["settings"]["correlation"]["min_confidence"], 20.0)
            self.assertEqual(body["settings"]["matcher"]["min_ingest_quality"], 0.45)
            self.assertEqual(body["settings"]["scorer"]["dedup_hours"], 3)

            get_resp = client.get("/api/settings")
            settings = get_resp.json()
            self.assertEqual(settings["correlation"]["min_confidence"], 20.0)
            self.assertEqual(config.get_min_correlation_confidence(), 20.0)
        finally:
            config.CONFIG_PATH = original_path
            config.CONFIG_BACKUP_PATH = original_backup
            if os.path.exists(cfg_path):
                os.remove(cfg_path)
            if os.path.exists(backup_path):
                os.remove(backup_path)

    def test_series_categories_endpoint(self):
        response = client.get("/api/series-categories")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get("KXFED"), "economic_data")
        self.assertEqual(data.get("KXNEXTAG"), "executive_actions")

    def test_anomalies_endpoint_includes_category(self):
        db.insert_anomaly({
            "ticker": "KXFED-26DEC-T4.5",
            "market_title": "Fed funds market",
            "series_ticker": "KXFED",
            "risk_group": "Fed Funds Rate",
            "mnpi_actors": "Fed governors",
            "detected_ts": int(time.time()),
            "detected_time": "2026-06-10T12:00:00Z",
            "anomaly_score": 55.0,
            "volume_zscore": 4.0,
            "block_trade_ratio": 0.2,
            "directional_flag": 0.1,
            "trigger_type": "compound",
            "price_before": 0.45,
            "price_current": 0.50,
            "volume_in_window": 500,
            "correlated_event": None,
            "notes": "test",
        })
        response = client.get("/api/anomalies?limit=5")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data[0]["category"], "economic_data")

    def test_anomalies_endpoint_includes_score_components(self):
        db.insert_anomaly({
            "ticker": "KXFED-26DEC-T4.5",
            "market_title": "Fed funds market",
            "series_ticker": "KXFED",
            "risk_group": "FOMC",
            "mnpi_actors": "Fed governors",
            "detected_ts": int(time.time()),
            "detected_time": "2026-06-10T12:00:00Z",
            "anomaly_score": 55.0,
            "volume_zscore": 4.0,
            "block_trade_ratio": 0.2,
            "directional_flag": 0.1,
            "trigger_type": "compound",
            "price_before": 0.45,
            "price_current": 0.50,
            "volume_in_window": 500,
            "correlated_event": None,
            "notes": "test",
            "score_components": {
                "base_score": 30.0,
                "block_modifier": 1.2,
                "price_bonus": 5.0,
                "normalized_score": 55.0,
                "trigger_type": "compound",
            },
        })
        response = client.get("/api/anomalies?limit=5")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data)
        self.assertIsNotNone(data[0].get("score_components"))
        self.assertEqual(data[0]["score_components"]["normalized_score"], 55.0)

    def test_cross_market_clusters_endpoint(self):
        """Verify that GET /api/cross-market-clusters returns persisted clusters."""
        cluster = {
            "mnpi_actors": "Fed governors",
            "series_tickers": "KXFED,KXCPI",
            "tickers": "KXFED-26DEC-T4.5,KXCPI-26JUN-T3.0",
            "window_start_ts": int(time.time()) - 7200,
            "window_start_time": "2026-06-09T10:00:00Z",
            "window_end_ts": int(time.time()) - 3600,
            "window_end_time": "2026-06-09T11:00:00Z",
            "anomaly_count": 2,
            "peak_score": 60.0,
            "total_score": 105.0,
            "cluster_score": 180.5,
            "computed_time": "2026-06-09T12:00:00Z",
            "computed_ts": int(time.time()),
        }
        db.upsert_cross_market_clusters_bulk([cluster])

        response = client.get("/api/cross-market-clusters")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["mnpi_actors"], "Fed governors")
        self.assertIn("KXFED", data[0]["series_tickers"])

    def test_whale_flow_endpoint(self):
        """Verify that GET /api/market/{ticker}/whale-flow returns hourly stats."""
        ticker = "KXCABOUT-26DEC"
        stats = {
            "ticker": ticker,
            "hour_ts": (int(time.time()) // 3600) * 3600,
            "whale_yes_volume": 5000.0,
            "whale_no_volume": 1000.0,
            "net_whale_exposure": 4000.0,
            "block_trade_count": 2
        }
        db.insert_whale_stats(stats)
        
        response = client.get(f"/api/market/{ticker}/whale-flow")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["net_whale_exposure"], 4000.0)

if __name__ == "__main__":
    unittest.main()
