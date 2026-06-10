import json
import os
import sys
import time
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import correlation_engine
import db

FIXTURES_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "fixtures",
    "news_articles.json",
)


class TestCorrelationTemporalModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_db_path = db.DB_PATH
        cls.test_db_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data",
            "test_pmwatch_correlation.db",
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
        db.DB_PATH = self.test_db_path
        if os.path.exists(self.test_db_path):
            try:
                os.remove(self.test_db_path)
            except OSError:
                pass
        db.init_db()

    def _base_anomaly(self, **overrides):
        base = {
            "id": 1,
            "ticker": "KXFED-26DEC-T4.5",
            "series_ticker": "KXFED",
            "anomaly_score": 50.0,
            "detected_ts": 1781000000,
        }
        base.update(overrides)
        return base

    def _base_article(self, **overrides):
        base = {
            "source_type": "mainstream_news",
            "published_ts": 1781003600,
        }
        base.update(overrides)
        return base

    def test_temporal_multiplier_tiers(self):
        self.assertEqual(correlation_engine.temporal_multiplier(3600), 1.8)
        self.assertEqual(correlation_engine.temporal_multiplier(5 * 3600), 1.3)
        self.assertAlmostEqual(correlation_engine.temporal_multiplier(48 * 3600), 0.3, places=2)
        self.assertEqual(correlation_engine.temporal_multiplier(-3600), 0.7)
        self.assertIsNone(correlation_engine.temporal_multiplier(49 * 3600))
        self.assertIsNone(correlation_engine.temporal_multiplier(-7 * 3600))

    def test_temporal_tier_1_beats_tier_3(self):
        anomaly = self._base_anomaly()
        article = self._base_article()
        quality = 0.5

        conf_1h = correlation_engine.calculate_correlation_confidence(
            anomaly, article, time_diff=3600, match_quality=quality
        )
        conf_20h = correlation_engine.calculate_correlation_confidence(
            anomaly, article, time_diff=20 * 3600, match_quality=quality
        )
        self.assertGreater(conf_1h, conf_20h)

    def test_anomaly_score_capped_in_confidence(self):
        anomaly = self._base_anomaly(anomaly_score=299.0)
        article = self._base_article()
        quality = 0.5
        time_diff = 3600

        confidence = correlation_engine.calculate_correlation_confidence(
            anomaly, article, time_diff=time_diff, match_quality=quality
        )
        expected = round(100.0 * 1.0 * quality * 1.8, 2)
        self.assertEqual(confidence, expected)

    def test_post_news_reaction_lower_than_pre_news(self):
        anomaly = self._base_anomaly()
        article = self._base_article()
        quality = 0.6

        pre_news = correlation_engine.calculate_correlation_confidence(
            anomaly, article, time_diff=3600, match_quality=quality
        )
        reaction = correlation_engine.calculate_correlation_confidence(
            anomaly, article, time_diff=-3600, match_quality=quality
        )
        self.assertGreater(pre_news, reaction)

    def test_source_weighted_correlation_still_holds(self):
        anomaly = self._base_anomaly()
        mainstream = self._base_article(source_type="mainstream_news")
        gov = self._base_article(source_type="primary_gov")

        conf_mainstream = correlation_engine.calculate_correlation_confidence(
            anomaly, mainstream, time_diff=3600, match_quality=0.5
        )
        conf_gov = correlation_engine.calculate_correlation_confidence(
            anomaly, gov, time_diff=3600, match_quality=0.5
        )
        self.assertAlmostEqual(conf_gov, conf_mainstream * 1.5)

    def test_disclosure_filing_weight_is_2x(self):
        anomaly = self._base_anomaly()
        gov = self._base_article(source_type="primary_gov")
        disclosure = self._base_article(source_type="disclosure_filing")

        conf_gov = correlation_engine.calculate_correlation_confidence(
            anomaly, gov, time_diff=3600, match_quality=0.5
        )
        conf_disc = correlation_engine.calculate_correlation_confidence(
            anomaly, disclosure, time_diff=3600, match_quality=0.5
        )
        self.assertAlmostEqual(conf_disc / conf_gov, 2.0 / 1.5, places=2)

    def test_stress_test_integration_no_correlation(self):
        article = self.fixtures["fed_stress_test_false_positive"]
        detected_ts = int(time.time()) - (15 * 3600 + 18 * 60)
        published_ts = detected_ts + (15 * 3600 + 18 * 60)

        db.insert_anomaly({
            "ticker": "KXFED-26JUN-T3.50",
            "market_title": "Fed funds upper bound market",
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
            "notes": "test",
        })
        db.insert_news_articles([{
            "title": article["title"],
            "description": article["description"],
            "url": "https://example.gov/fed-stress-test-2",
            "published_time": "2026-06-09T16:00:00Z",
            "published_ts": published_ts,
            "source": "Federal Reserve Press",
            "source_type": "primary_gov",
            "series_ticker": None,
            "ingested_ts": int(time.time()),
        }])

        correlation_engine.correlate_all_recent_anomalies(lookback_days=7)
        self.assertEqual(len(db.get_correlations(limit=10)), 0)

    def test_weak_match_below_min_confidence_not_inserted(self):
        detected_ts = int(time.time()) - 3600
        published_ts = detected_ts + 3600

        db.insert_anomaly({
            "ticker": "KXFED-26DEC-T4.5",
            "market_title": "Fed funds market",
            "series_ticker": "KXFED",
            "risk_group": "FOMC",
            "mnpi_actors": "Fed governors",
            "detected_ts": detected_ts,
            "detected_time": "2026-06-09T12:00:00Z",
            "anomaly_score": 5.0,
            "volume_zscore": 1.0,
            "block_trade_ratio": 0.0,
            "directional_flag": 0.0,
            "trigger_type": "volume_spike",
            "price_before": 0.40,
            "price_current": 0.41,
            "volume_in_window": 50,
            "correlated_event": None,
            "notes": "low score anomaly",
        })
        article = self.fixtures["fed_rate_decision_true_positive"]
        db.insert_news_articles([{
            "title": article["title"],
            "description": article["description"],
            "url": "https://example.gov/fed-rate-weak",
            "published_time": "2026-06-09T13:00:00Z",
            "published_ts": published_ts,
            "source": "Federal Reserve Press",
            "source_type": "mainstream_news",
            "series_ticker": "KXFED",
            "ingested_ts": int(time.time()),
        }])

        correlation_engine.correlate_all_recent_anomalies(lookback_days=7)
        self.assertEqual(len(db.get_correlations(limit=10)), 0)

    def test_rebuild_correlations_replaces_stale_rows(self):
        detected_ts = int(time.time()) - 3600
        published_ts = detected_ts + 3600

        db.insert_anomaly({
            "ticker": "KXFED-26DEC-T4.5",
            "market_title": "Fed funds market",
            "series_ticker": "KXFED",
            "risk_group": "FOMC",
            "mnpi_actors": "Fed governors",
            "detected_ts": detected_ts,
            "detected_time": "2026-06-09T12:00:00Z",
            "anomaly_score": 299.0,
            "volume_zscore": 21.0,
            "block_trade_ratio": 0.5,
            "directional_flag": 0.3,
            "trigger_type": "compound",
            "price_before": 0.45,
            "price_current": 0.52,
            "volume_in_window": 1200,
            "correlated_event": None,
            "notes": "test",
        })
        article = self.fixtures["fed_stress_test_false_positive"]
        db.insert_news_articles([{
            "title": article["title"],
            "description": article["description"],
            "url": "https://example.gov/fed-stress-test-rebuild",
            "published_time": "2026-06-09T13:00:00Z",
            "published_ts": published_ts,
            "source": "Federal Reserve Press",
            "source_type": "primary_gov",
            "series_ticker": None,
            "ingested_ts": int(time.time()),
        }])
        db.insert_correlation({
            "anomaly_id": 1,
            "cluster_first_seen_ts": detected_ts,
            "ticker": "KXFED-26DEC-T4.5",
            "news_id": 1,
            "lead_time_seconds": published_ts - detected_ts,
            "confidence_score": 299.0,
            "notes": "stale false positive",
        })

        result = correlation_engine.rebuild_correlations(lookback_days=7, cap_scores=True)

        self.assertEqual(result["removed"], 1)
        self.assertEqual(result["capped_anomalies"], 1)
        self.assertEqual(result["inserted"], 0)
        self.assertEqual(len(db.get_correlations(limit=10)), 0)

        conn = db.get_conn()
        row = conn.execute(
            "SELECT anomaly_score FROM anomalies WHERE ticker = ?",
            ("KXFED-26DEC-T4.5",),
        ).fetchone()
        conn.close()
        self.assertEqual(row["anomaly_score"], 100.0)

    def test_person_scoped_market_rejects_wrong_candidate_article(self):
        detected_ts = int(time.time()) - (16 * 3600)
        published_ts = detected_ts + (16 * 3600)

        db.upsert_market({
            "ticker": "KXNEXTAG-29-TCRU",
            "series_ticker": "KXNEXTAG",
            "title": "Who will be Trump's next Attorney General?",
            "category": "executive_actions",
            "risk_group": "Next AG",
            "mnpi_actors": "WH personnel office",
            "clearance_tier": 3,
            "actors_json": "[]",
            "subject_name": "Ted Cruz",
            "rules_primary": "If the first new person to be Attorney General is Ted Cruz before Jan 20, 2029, then the market resolves to Yes.",
            "open_time": "",
            "close_time": "",
            "volume_fp": 0.0,
            "last_price_dollars": 0.01,
            "status": "active",
            "last_seen": "2026-06-10T00:00:00Z",
        })
        db.insert_anomaly({
            "ticker": "KXNEXTAG-29-TCRU",
            "market_title": "Who will be Trump's next Attorney General?",
            "series_ticker": "KXNEXTAG",
            "risk_group": "Next AG",
            "mnpi_actors": "WH personnel office",
            "subject_name": "Ted Cruz",
            "detected_ts": detected_ts,
            "detected_time": "2026-06-09T12:00:00Z",
            "anomaly_score": 55.0,
            "volume_zscore": 4.0,
            "block_trade_ratio": 0.2,
            "directional_flag": 0.1,
            "trigger_type": "compound",
            "price_before": 0.01,
            "price_current": 0.02,
            "volume_in_window": 500,
            "correlated_event": None,
            "notes": "test",
        })
        db.insert_news_articles([{
            "title": "Blanche Was Once Seen as Tempering Trump’s Tactics. Now He’s All In.",
            "description": "Todd Blanche is discussed as a possible Attorney General pick.",
            "url": "https://example.com/blanche-ag",
            "published_time": "2026-06-10T04:00:00Z",
            "published_ts": published_ts,
            "source": "NYT Politics",
            "source_type": "mainstream_news",
            "series_ticker": "KXNEXTAG",
            "ingested_ts": int(time.time()),
        }])

        correlation_engine.rebuild_correlations(lookback_days=7, cap_scores=False)
        self.assertEqual(len(db.get_correlations(limit=10)), 0)

    def test_person_scoped_pardon_rejects_other_recipient(self):
        detected_ts = int(time.time()) - (6 * 3600)
        published_ts = detected_ts + (6 * 3600)

        db.upsert_market({
            "ticker": "KXTRUMPPARDONS-29JAN21-GMAX",
            "series_ticker": "KXTRUMPPARDONS",
            "title": "Will Ghislaine Maxwell receive a presidential pardon before Jan 21, 2029?",
            "category": "executive_actions",
            "risk_group": "Trump Pardons",
            "mnpi_actors": "WH counsel",
            "clearance_tier": 3,
            "actors_json": "[]",
            "subject_name": "Ghislaine Maxwell",
            "rules_primary": "If Ghislaine Maxwell has been given a presidential pardon, commutation, or reprieve during Trump's second term and before Jan 21, 2029, then the market resolves to Yes.",
            "open_time": "",
            "close_time": "",
            "volume_fp": 0.0,
            "last_price_dollars": 0.40,
            "status": "active",
            "last_seen": "2026-06-10T00:00:00Z",
        })
        db.insert_anomaly({
            "ticker": "KXTRUMPPARDONS-29JAN21-GMAX",
            "market_title": "Will Ghislaine Maxwell receive a presidential pardon before Jan 21, 2029?",
            "series_ticker": "KXTRUMPPARDONS",
            "risk_group": "Trump Pardons",
            "mnpi_actors": "WH counsel",
            "subject_name": "Ghislaine Maxwell",
            "detected_ts": detected_ts,
            "detected_time": "2026-06-10T06:00:00Z",
            "anomaly_score": 65.0,
            "volume_zscore": 5.0,
            "block_trade_ratio": 0.3,
            "directional_flag": 0.2,
            "trigger_type": "compound",
            "price_before": 0.35,
            "price_current": 0.40,
            "volume_in_window": 800,
            "correlated_event": None,
            "notes": "test",
        })
        db.insert_news_articles([{
            "title": "Granting Pardon to Stephen E. Buyer",
            "description": "White House briefing on a presidential pardon for Stephen E. Buyer.",
            "url": "https://example.com/buyer-pardon",
            "published_time": "2026-06-10T12:00:00Z",
            "published_ts": published_ts,
            "source": "White House Briefings",
            "source_type": "primary_gov",
            "series_ticker": "KXTRUMPPARDONS",
            "ingested_ts": int(time.time()),
        }])

        correlation_engine.rebuild_correlations(lookback_days=7, cap_scores=False)
        self.assertEqual(len(db.get_correlations(limit=10)), 0)


if __name__ == "__main__":
    unittest.main()
