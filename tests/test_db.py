import unittest
import sqlite3
import os
import sys

# Add parent directory to path to import db
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db

class TestDBForensics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_db_path = db.DB_PATH
        # Use a unique file for testing
        cls.test_db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "test_pmwatch_run.db")
        db.DB_PATH = cls.test_db_path
        cls.clean_db()

    @classmethod
    def tearDownClass(cls):
        db.DB_PATH = cls.original_db_path
        cls.clean_db()

    @classmethod
    def clean_db(cls):
        if os.path.exists(cls.test_db_path):
            try:
                os.remove(cls.test_db_path)
            except OSError:
                pass

    def setUp(self):
        # Clean the DB before each test by deleting and re-init
        self.clean_db()

    def test_init_db_creates_new_tables(self):
        """Verify that the new forensics tables are successfully created in the schema."""
        db.init_db()
        conn = db.get_conn()
        cursor = conn.cursor()
        
        # Check tables exist
        tables_to_check = [
            "news_articles",
            "news_correlations",
            "microstructure_alerts",
            "whale_hourly_stats",
            "cross_market_clusters",
        ]
        for table in tables_to_check:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
            row = cursor.fetchone()
            self.assertIsNotNone(row, f"Table '{table}' was not created!")
            
        conn.close()

    def test_watched_markets_actor_columns(self):
        """Verify MNPI actor columns exist and persist through upsert."""
        db.init_db()
        db.upsert_market({
            "ticker": "KXFED-TEST",
            "series_ticker": "KXFED",
            "title": "Fed test market",
            "category": "economic_data",
            "risk_group": "Fed Funds Rate",
            "mnpi_actors": "FOMC members, Treasury staff",
            "clearance_tier": 3,
            "actors_json": '[{"role":"FOMC member","clearance_tier":3}]',
            "open_time": "",
            "close_time": "",
            "volume_fp": 0.0,
            "last_price_dollars": 0.5,
            "status": "active",
            "last_seen": "2026-06-10T00:00:00Z",
        })

        conn = db.get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT clearance_tier, actors_json FROM watched_markets WHERE ticker = ?",
            ("KXFED-TEST",),
        )
        row = cursor.fetchone()
        conn.close()

        self.assertEqual(row["clearance_tier"], 3)
        self.assertIn("FOMC member", row["actors_json"])

    def test_news_articles_helpers(self):
        """Verify that insert and retrieve helpers for news articles function correctly."""
        db.init_db()
        
        # Test article
        article = {
            "title": "Fed hints at rate cut",
            "description": "FOMC meeting minutes show dovish sentiment.",
            "url": "https://example.gov/fed-cut",
            "published_time": "2026-06-09T12:00:00Z",
            "published_ts": 1781006400,
            "source": "Federal Reserve",
            "source_type": "primary_gov",
            "series_ticker": "KXFED",
            "ingested_ts": 1781006405
        }
        
        # Call insert helper (this should be defined in db.py)
        inserted = db.insert_news_articles([article])
        self.assertEqual(inserted, 1)
        
        # Call get helper (this should be defined in db.py)
        retrieved = db.get_recent_news_articles(limit=10)
        self.assertEqual(len(retrieved), 1)
        self.assertEqual(retrieved[0]["title"], "Fed hints at rate cut")
        self.assertEqual(retrieved[0]["source_type"], "primary_gov")

    def test_microstructure_alerts_helpers(self):
        """Verify that insert and retrieve helpers for microstructure alerts function correctly."""
        db.init_db()
        
        alert = {
            "ticker": "KXFED-26DEC-T4.5",
            "timestamp": 1781006400,
            "time_str": "2026-06-09T12:00:00Z",
            "alert_type": "spoofing",
            "severity_score": 85.5,
            "details": '{"price": 0.45, "canceled_qty": 5000}'
        }
        
        db.insert_microstructure_alert(alert)
        alerts = db.get_microstructure_alerts(limit=5)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["ticker"], "KXFED-26DEC-T4.5")
        self.assertEqual(alerts[0]["alert_type"], "spoofing")

    def test_whale_stats_helpers(self):
        """Verify that insert and retrieve helpers for whale stats function correctly."""
        db.init_db()
        
        stats = {
            "ticker": "KXCPI-26JUN-T0.5",
            "hour_ts": 1781004000,
            "whale_yes_volume": 12000.0,
            "whale_no_volume": 4000.0,
            "net_whale_exposure": 8000.0,
            "block_trade_count": 3
        }
        
        db.insert_whale_stats(stats)
        flow = db.get_whale_flow("KXCPI-26JUN-T0.5", limit=10)
        self.assertEqual(len(flow), 1)
        self.assertEqual(flow[0]["net_whale_exposure"], 8000.0)

    def test_news_correlations_helpers(self):
        """Verify that insert and retrieve helpers for news correlations function correctly."""
        db.init_db()
        
        # Setup pre-requisite article
        article = {
            "title": "Fed hints at rate cut",
            "description": "FOMC meeting minutes show dovish sentiment.",
            "url": "https://example.gov/fed-cut-corr",
            "published_time": "2026-06-09T12:00:00Z",
            "published_ts": 1781006400,
            "source": "Federal Reserve",
            "source_type": "primary_gov",
            "series_ticker": "KXFED",
            "ingested_ts": 1781006405
        }
        db.insert_news_articles([article])
        retrieved = db.get_recent_news_articles(limit=1)
        news_id = retrieved[0]["id"]
        
        correlation = {
            "anomaly_id": 42,
            "cluster_first_seen_ts": 1781000000,
            "ticker": "KXFED-26DEC-T4.5",
            "news_id": news_id,
            "lead_time_seconds": 6400,
            "confidence_score": 75.2,
            "notes": "Matched keywords: fed"
        }
        
        db.insert_correlation(correlation)
        corrs = db.get_correlations(limit=5)
        self.assertEqual(len(corrs), 1)
        self.assertEqual(corrs[0]["ticker"], "KXFED-26DEC-T4.5")
        self.assertEqual(corrs[0]["confidence_score"], 75.2)

if __name__ == "__main__":
    unittest.main()
