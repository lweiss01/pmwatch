import unittest
import sys
import os
import time

# Add parent directory to path to import api
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
            "notes": "Matched keywords: cabinet"
        }
        db.insert_correlation(correlation)
        
        response = client.get("/api/correlations")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["ticker"], "KXCABOUT-26DEC")
        self.assertEqual(data[0]["confidence_score"], 90.0)

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
