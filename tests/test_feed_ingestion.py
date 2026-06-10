import json
import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import correlation_engine
import feed_ingestion
import keyword_matcher

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


class TestFeedIngestion(unittest.TestCase):
    def test_fed_press_scope_excludes_unrelated_series(self):
        """Fed press releases should not be evaluated against unrelated series."""
        title = "Insurrection Act deployment considered for border crisis"
        description = "Military domestic deployment discussed by administration officials."
        self.assertIsNone(
            keyword_matcher.match_series(title, description, source="Federal Reserve Press")
        )

    def test_fed_press_still_matches_kxfed_with_rate_signals(self):
        title = "FOMC statement on monetary policy"
        description = (
            "The Federal Reserve held the fed funds rate target range unchanged "
            "following the latest interest rate decision."
        )
        self.assertEqual(
            keyword_matcher.match_series(title, description, source="Federal Reserve Press"),
            "KXFED",
        )

    def test_treasury_direct_scoped_to_no_series(self):
        title = "Treasury auction announcement"
        description = "New TreasuryDirect securities offering published."
        self.assertIsNone(
            keyword_matcher.match_series(title, description, source="TreasuryDirect Offerings")
        )

    def test_disclosure_source_weight_is_2x(self):
        self.assertEqual(correlation_engine.get_source_weight("disclosure_filing"), 2.0)
        self.assertEqual(correlation_engine.format_source_weight("disclosure_filing"), "2x")

    def test_disclosure_confidence_beats_primary_gov(self):
        anomaly = {"anomaly_score": 50.0}
        article_gov = {"source_type": "primary_gov"}
        article_disc = {"source_type": "disclosure_filing"}
        time_diff = 3600
        quality = 0.5

        conf_gov = correlation_engine.calculate_correlation_confidence(
            anomaly, article_gov, time_diff, quality
        )
        conf_disc = correlation_engine.calculate_correlation_confidence(
            anomaly, article_disc, time_diff, quality
        )
        self.assertAlmostEqual(conf_disc, conf_gov * (2.0 / 1.5))

    def test_parse_house_fd_xml_ptr_only(self):
        xml_path = os.path.join(FIXTURES_DIR, "house_fd_sample.xml")
        with open(xml_path, "rb") as f:
            articles = feed_ingestion.parse_house_fd_xml(
                f.read(), year=2026, source="House Financial Disclosures"
            )
        self.assertEqual(len(articles), 1)
        art = articles[0]
        self.assertEqual(art["source_type"], "disclosure_filing")
        self.assertIn("House PTR", art["title"])
        self.assertIn("20012345", art["url"])
        self.assertIsNone(art["series_ticker"])

    def test_parse_senate_ptr_json(self):
        payload = [
            {
                "firstName": "John",
                "lastName": "Boozman",
                "dateRecieved": "6/1/2026",
                "ticker": "IWM",
                "asset_name": "iShares Russell 2000 ETF",
                "type": "Sale (Partial)",
                "link": "https://efdsearch.senate.gov/search/view/ptr/example/",
            }
        ]
        articles = feed_ingestion.parse_senate_ptr_json(
            payload, source="Senate eFD Disclosures"
        )
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["source_type"], "disclosure_filing")
        self.assertIn("IWM", articles[0]["title"])

    def test_parse_atom_string(self):
        atom_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <title>4 - Example Corp (0001234567) (Reporting)</title>
            <link rel="alternate" href="https://www.sec.gov/example-form4"/>
            <summary>Form 4 filed for Example Corp insider trade.</summary>
            <updated>2026-06-09T18:00:00Z</updated>
          </entry>
        </feed>
        """
        articles = feed_ingestion.parse_atom_string(
            atom_xml, source="SEC EDGAR Form 4", source_type="disclosure_filing"
        )
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["source_type"], "disclosure_filing")
        self.assertIn("Example Corp", articles[0]["title"])
        self.assertEqual(articles[0]["url"], "https://www.sec.gov/example-form4")


if __name__ == "__main__":
    unittest.main()
