import unittest
import sys
import os
from unittest.mock import patch, MagicMock

# Add parent directory to path to import news_engine
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import db first to avoid import order errors
import db

try:
    import news_engine
except ImportError:
    news_engine = None  # Expected during Red phase

class TestNewsEngine(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(news_engine, "news_engine module does not exist yet (expected in RED phase)")

    def test_keyword_matching(self):
        """Verify that articles are correctly mapped to series_tickers based on content keywords."""
        # Setup mock article text
        title_ag = "President nominates new Attorney General Blanche"
        desc_ag = "Blanche has been chosen to lead the Justice Department (DOJ)."
        
        series_ag = news_engine.match_series(title_ag, desc_ag)
        self.assertEqual(series_ag, "KXNEXTAG")
        
        # Test SCOTUS resignation
        title_scotus = "Supreme Court Justice retirement announcement expected"
        desc_scotus = "A major vacancy is opening up on the high court (SCOTUS)."
        series_sc = news_engine.match_series(title_scotus, desc_scotus)
        self.assertEqual(series_sc, "KXSCOTUSRESIGN")
        
        # Test CPI
        title_cpi = "Latest CPI index shows rise in consumer prices"
        desc_cpi = "Inflation figures released by the Bureau of Labor Statistics (BLS)."
        series_cpi = news_engine.match_series(title_cpi, desc_cpi)
        self.assertEqual(series_cpi, "KXCPI")
        
        # Test completely unrelated text
        title_unrelated = "Local sports team wins championship"
        desc_unrelated = "Fans celebrate in the streets after historical victory."
        series_none = news_engine.match_series(title_unrelated, desc_unrelated)
        self.assertIsNone(series_none)

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
                <description>The legislative package would reform or abolish federal education oversight (doed).</description>
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
        
        conf_mainstream = news_engine.calculate_correlation_confidence(anomaly, news_mainstream, time_diff=3600, overlap_ratio=0.5)
        conf_gov = news_engine.calculate_correlation_confidence(anomaly, news_gov, time_diff=3600, overlap_ratio=0.5)
        
        # Gov confidence should be exactly 1.5x of mainstream confidence
        self.assertAlmostEqual(conf_gov, conf_mainstream * 1.5)

if __name__ == "__main__":
    unittest.main()
