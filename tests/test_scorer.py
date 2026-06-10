import os
import sys
import unittest
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import scorer


def _market() -> dict:
    return {
        "title": "Test market",
        "series_ticker": "KXFED",
        "risk_group": "FOMC",
        "mnpi_actors": "Fed governors",
    }


def _minimal_trades(count: int = 12) -> list:
    now = 1_781_000_000
    return [
        {
            "created_ts": now - (i * 300),
            "count_fp": 100.0,
            "yes_price_dollars": 0.45,
            "taker_side": "yes",
            "is_block_trade": 0,
        }
        for i in range(count)
    ]


class TestScorerHygiene(unittest.TestCase):
    @patch.object(scorer, "price_divergence_from_trades")
    @patch.object(scorer, "block_trade_signal_from_trades")
    @patch.object(scorer, "volume_zscore_from_trades")
    def test_score_capped_at_100(self, mock_vol, mock_block, mock_price):
        mock_vol.return_value = 25.0
        mock_block.return_value = {"ratio": 2.0, "directional_no": 1.0, "count": 100}
        mock_price.return_value = {
            "max_jump": 0.5,
            "direction": "up",
            "price_now": 0.55,
            "price_before": 0.45,
        }

        result = scorer.score_market("KXFED-TEST", _market(), _minimal_trades(), None)
        self.assertIsNotNone(result)
        self.assertLessEqual(result["anomaly_score"], 100.0)
        self.assertEqual(result["anomaly_score"], 100.0)

    @patch.object(scorer, "price_divergence_from_trades")
    @patch.object(scorer, "block_trade_signal_from_trades")
    @patch.object(scorer, "volume_zscore_from_trades")
    def test_yellow_threshold_unchanged(self, mock_vol, mock_block, mock_price):
        mock_vol.return_value = 3.0
        mock_block.return_value = {"ratio": 0.1, "directional_no": 0.0, "count": 20}
        mock_price.return_value = {
            "max_jump": 0.05,
            "direction": "up",
            "price_now": 0.50,
            "price_before": 0.45,
        }

        result = scorer.score_market("KXFED-TEST", _market(), _minimal_trades(), None)
        self.assertIsNotNone(result)
        self.assertGreaterEqual(result["anomaly_score"], scorer.YELLOW_SCORE)

    @patch.object(scorer, "price_divergence_from_trades")
    @patch.object(scorer, "block_trade_signal_from_trades")
    @patch.object(scorer, "volume_zscore_from_trades")
    def test_dedup_suppresses_small_increase(self, mock_vol, mock_block, mock_price):
        mock_vol.return_value = 4.0
        mock_block.return_value = {"ratio": 0.2, "directional_no": 0.1, "count": 20}
        mock_price.return_value = {
            "max_jump": 0.08,
            "direction": "up",
            "price_now": 0.52,
            "price_before": 0.45,
        }

        result = scorer.score_market(
            "KXFED-TEST",
            _market(),
            _minimal_trades(),
            recent_scores={"KXFED-TEST": 50.0},
        )
        self.assertIsNone(result)

    @patch.object(scorer, "price_divergence_from_trades")
    @patch.object(scorer, "block_trade_signal_from_trades")
    @patch.object(scorer, "volume_zscore_from_trades")
    def test_dedup_allows_large_increase(self, mock_vol, mock_block, mock_price):
        mock_vol.return_value = 8.0
        mock_block.return_value = {"ratio": 0.5, "directional_no": 0.2, "count": 40}
        mock_price.return_value = {
            "max_jump": 0.15,
            "direction": "up",
            "price_now": 0.60,
            "price_before": 0.45,
        }

        result = scorer.score_market(
            "KXFED-TEST",
            _market(),
            _minimal_trades(),
            recent_scores={"KXFED-TEST": 50.0},
        )
        self.assertIsNotNone(result)
        self.assertGreater(result["anomaly_score"], 50.0 * 1.2)

    def test_should_suppress_repeat_without_prior_score(self):
        self.assertFalse(scorer.should_suppress_repeat("KXFED-TEST", 60.0, {}))

    def test_should_suppress_repeat_with_small_delta(self):
        self.assertTrue(
            scorer.should_suppress_repeat("KXFED-TEST", 55.0, {"KXFED-TEST": 50.0})
        )

    def test_should_suppress_repeat_with_large_delta(self):
        self.assertFalse(
            scorer.should_suppress_repeat("KXFED-TEST", 65.0, {"KXFED-TEST": 50.0})
        )

    @patch.object(scorer, "price_divergence_from_trades")
    @patch.object(scorer, "block_trade_signal_from_trades")
    @patch.object(scorer, "volume_zscore_from_trades")
    def test_clearance_tier_boosts_base_score(self, mock_vol, mock_block, mock_price):
        mock_vol.return_value = 4.0
        mock_block.return_value = {"ratio": 0.0, "directional_no": 0.0, "count": 20}
        mock_price.return_value = {
            "max_jump": 0.0,
            "direction": "none",
            "price_now": 0.45,
            "price_before": 0.45,
        }

        tier1_market = {**_market(), "clearance_tier": 1}
        tier3_market = {**_market(), "clearance_tier": 3}

        tier1 = scorer.score_market("KXFED-T1", tier1_market, _minimal_trades(), None)
        tier3 = scorer.score_market("KXFED-T3", tier3_market, _minimal_trades(), None)

        self.assertIsNotNone(tier1)
        self.assertIsNotNone(tier3)
        self.assertGreater(tier3["anomaly_score"], tier1["anomaly_score"])
        self.assertAlmostEqual(
            tier3["anomaly_score"] / tier1["anomaly_score"],
            scorer.CLEARANCE_MULTIPLIER[3],
            places=2,
        )

    def test_clearance_multiplier_defaults_to_one(self):
        self.assertEqual(scorer.clearance_multiplier({}), 1.0)
        self.assertEqual(scorer.clearance_multiplier({"clearance_tier": 2}), 1.1)

    @patch.object(config, "get_yellow_score", return_value=80.0)
    @patch.object(scorer, "price_divergence_from_trades")
    @patch.object(scorer, "block_trade_signal_from_trades")
    @patch.object(scorer, "volume_zscore_from_trades")
    def test_yellow_threshold_reads_config(
        self, mock_vol, mock_block, mock_price, _mock_yellow
    ):
        mock_vol.return_value = 3.0
        mock_block.return_value = {"ratio": 0.1, "directional_no": 0.0, "count": 20}
        mock_price.return_value = {
            "max_jump": 0.05,
            "direction": "up",
            "price_now": 0.50,
            "price_before": 0.45,
        }

        result = scorer.score_market("KXFED-TEST", _market(), _minimal_trades(), None)
        self.assertIsNone(result)

    @patch.object(scorer, "price_divergence_from_trades")
    @patch.object(scorer, "block_trade_signal_from_trades")
    @patch.object(scorer, "volume_zscore_from_trades")
    def test_notes_include_score_components(self, mock_vol, mock_block, mock_price):
        mock_vol.return_value = 4.0
        mock_block.return_value = {"ratio": 0.2, "directional_no": 0.1, "count": 20}
        mock_price.return_value = {
            "max_jump": 0.08,
            "direction": "up",
            "price_now": 0.52,
            "price_before": 0.45,
        }

        result = scorer.score_market("KXFED-TEST", _market(), _minimal_trades(), None)
        self.assertIsNotNone(result)
        self.assertIn("base=", result["notes"])
        self.assertIn("block_mod=", result["notes"])
        self.assertIn("price_bonus=", result["notes"])
        self.assertIn("clearance=", result["notes"])
        comps = result["score_components"]
        self.assertIn("base_score", comps)
        self.assertIn("block_modifier", comps)
        self.assertIn("normalized_score", comps)
        self.assertEqual(comps["normalized_score"], result["anomaly_score"])


if __name__ == "__main__":
    unittest.main()
