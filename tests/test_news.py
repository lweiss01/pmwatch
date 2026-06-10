import json
import os
import sys
import time
import unittest

# Add parent directory to path to import news_engine
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db

try:
    import news_engine
except ImportError:
    news_engine = None  # Expected during Red phase

FIXTURES_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "fixtures",
    "news_articles.json",
)


class TestNewsEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_db_path = db.DB_PATH
        cls.test_db_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data",
            "test_pmwatch_news.db",
        )
        with open(FIXTURES_PATH, encoding="utf-8") as f:
            cls.fixtures = json.load(f)

    @classmethod
    def tearDownClass(cls):
        db.DB_PATH = cls.original_db_path
        if os.path.exists(cls.test_db_path):
            try:
                os.remove(cls.test_db_path)
            except OSError:
                pass

    def setUp(self):
        self.assertIsNotNone(news_engine, "news_engine module does not exist yet (expected in RED phase)")
        db.DB_PATH = self.test_db_path
        if os.path.exists(self.test_db_path):
            try:
                os.remove(self.test_db_path)
            except OSError:
                pass
        db.init_db()

    def test_parse_rss_feed(self):
        """Verify that standard RSS feed XML is correctly parsed into articles."""
        mock_xml = """<?xml version="1.0" encoding="UTF-8" ?>
        <rss version="2.0">
        <channel>
            <title>NYT Politics</title>
            <link>https://www.nytimes.com/section/politics</link>
            <description>New York Times Politics RSS Feed</description>
            <item>
                <title>President Signs Major Education Reform Bill</title>
                <link>https://www.nytimes.com/edu-bill</link>
                <description>The legislative package would abolish the Department of Education (doed).</description>
                <pubDate>Tue, 09 Jun 2026 12:00:00 -0400</pubDate>
            </item>
        </channel>
        </rss>
        """
        
        # Parse RSS mock
        articles = news_engine.parse_rss_string(mock_xml, source="NYT Politics", source_type="mainstream_news")
        
        self.assertEqual(len(articles), 1)
        art = articles[0]
        self.assertEqual(art["title"], "President Signs Major Education Reform Bill")
        self.assertEqual(art["source"], "NYT Politics")
        self.assertEqual(art["source_type"], "mainstream_news")
        self.assertEqual(art["series_ticker"], "KXDOED")  # keyword: 'doed'
        self.assertEqual(art["published_time"], "2026-06-09T16:00:00Z") # parsed and normalized to UTC ISO
        self.assertEqual(art["published_ts"], 1781020800)

    def test_parse_federal_register_json(self):
        """Verify that Federal Register API JSON structure is correctly parsed into articles."""
        mock_json = {
            "results": [
                {
                    "title": "Establishment of new regulatory limits",
                    "abstract": "The Bureau of Labor Statistics (BLS) announces new consumer price index metrics.",
                    "html_url": "https://www.federalregister.gov/documents/bls-limits",
                    "publication_date": "2026-06-09",
                    "agencies": [{"name": "Bureau of Labor Statistics"}],
                    "type": "Notice"
                }
            ]
        }
        
        articles = news_engine.parse_fed_register_json(mock_json, source="Federal Register")
        
        self.assertEqual(len(articles), 1)
        art = articles[0]
        self.assertEqual(art["title"], "Establishment of new regulatory limits")
        self.assertEqual(art["source_type"], "primary_gov")
        self.assertEqual(art["series_ticker"], "KXCPI") # keyword: 'bls', 'consumer price index'

    def test_source_weighted_correlation(self):
        """Verify that primary government sources receive 1.5x weight in confidence score calculation."""
        # Anomaly at t = 1781000000 with score = 50.0 on series KXFED
        anomaly = {
            "id": 100,
            "ticker": "KXFED-26DEC-T4.5",
            "series_ticker": "KXFED",
            "anomaly_score": 50.0,
            "detected_ts": 1781000000
        }
        
        # News article at t = 1781003600 (1 hour later, 3600 seconds)
        # Mainstream news
        news_mainstream = {
            "title": "Fed rate decision details",
            "description": "Interest rate updates from FOMC members.",
            "source_type": "mainstream_news",
            "published_ts": 1781003600
        }
        
        # Gov news
        news_gov = {
            "title": "Fed official announcement",
            "description": "Interest rate updates from FOMC members.",
            "source_type": "primary_gov",
            "published_ts": 1781003600
        }
        
        conf_mainstream = news_engine.calculate_correlation_confidence(
            anomaly, news_mainstream, time_diff=3600, match_quality=0.5
        )
        conf_gov = news_engine.calculate_correlation_confidence(
            anomaly, news_gov, time_diff=3600, match_quality=0.5
        )
        
        # Gov confidence should be exactly 1.5x of mainstream confidence
        self.assertAlmostEqual(conf_gov, conf_mainstream * 1.5)

    def test_stress_test_does_not_correlate_to_kxfed(self):
        """Regression: Fed stress test article must not create a KXFED correlation."""
        article = self.fixtures["fed_stress_test_false_positive"]
        detected_ts = int(time.time()) - (15 * 3600 + 18 * 60)
        published_ts = detected_ts + (15 * 3600 + 18 * 60)

        db.insert_anomaly({
            "ticker": "KXFED-26JUN-T3.50",
            "market_title": "Will the upper bound of the federal funds rate be above 3.50%?",
            "series_ticker": "KXFED",
            "risk_group": "FOMC",
            "mnpi_actors": "Fed governors",
            "detected_ts": detected_ts,
            "detected_time": "2026-06-09T00:42:00Z",
            "anomaly_score": 299.0,
            "volume_zscore": 21.44,
            "block_trade_ratio": 0.5,
            "directional_flag": 0.3,
            "trigger_type": "compound",
            "price_before": 0.45,
            "price_current": 0.52,
            "volume_in_window": 1200,
            "correlated_event": None,
            "notes": "test anomaly",
        })

        db.insert_news_articles([{
            "title": article["title"],
            "description": article["description"],
            "url": "https://example.gov/fed-stress-test",
            "published_time": "2026-06-09T16:00:00Z",
            "published_ts": published_ts,
            "source": "Federal Reserve Press",
            "source_type": "primary_gov",
            "series_ticker": None,
            "ingested_ts": int(time.time()),
        }])

        news_engine.correlate_all_recent_anomalies(lookback_days=7)
        correlations = db.get_correlations(limit=10)
        self.assertEqual(len(correlations), 0)

    def test_fed_rate_article_correlates_to_kxfed(self):
        """Valid rate-policy article should create a KXFED correlation."""
        article = self.fixtures["fed_rate_decision_true_positive"]
        detected_ts = int(time.time()) - 3600
        published_ts = detected_ts + 3600

        db.insert_anomaly({
            "ticker": "KXFED-26DEC-T4.5",
            "market_title": "Fed funds rate market",
            "series_ticker": "KXFED",
            "risk_group": "FOMC",
            "mnpi_actors": "Fed governors",
            "detected_ts": detected_ts,
            "detected_time": "2026-06-09T12:00:00Z",
            "anomaly_score": 50.0,
            "volume_zscore": 3.0,
            "block_trade_ratio": 0.2,
            "directional_flag": 0.1,
            "trigger_type": "volume_spike",
            "price_before": 0.40,
            "price_current": 0.42,
            "volume_in_window": 500,
            "correlated_event": None,
            "notes": "test anomaly",
        })

        db.insert_news_articles([{
            "title": article["title"],
            "description": article["description"],
            "url": "https://example.gov/fed-rate-decision",
            "published_time": "2026-06-09T13:00:00Z",
            "published_ts": published_ts,
            "source": "Federal Reserve Press",
            "source_type": "primary_gov",
            "series_ticker": "KXFED",
            "ingested_ts": int(time.time()),
        }])

        news_engine.correlate_all_recent_anomalies(lookback_days=7)
        correlations = db.get_correlations(limit=10)
        self.assertEqual(len(correlations), 1)
        self.assertEqual(correlations[0]["ticker"], "KXFED-26DEC-T4.5")
        self.assertIn("fed funds rate", correlations[0]["notes"].lower())


if __name__ == "__main__":
    unittest.main()
