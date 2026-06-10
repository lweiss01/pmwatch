import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import watchlist_loader


class TestWatchlistLoader(unittest.TestCase):
    def test_legacy_entry_uses_series_defaults(self):
        entry = watchlist_loader.normalize_watchlist_entry(
            {
                "risk": "FOMC members, Treasury staff",
                "series": "KXFED",
                "name": "Fed Funds Rate",
            },
            "economic_data",
        )
        self.assertEqual(entry["clearance_tier"], 3)
        self.assertEqual(len(entry["actors"]), 3)
        self.assertEqual(entry["mnpi_actors"], "FOMC members, Treasury staff")

    def test_v2_entry_preserves_explicit_actors(self):
        entry = watchlist_loader.normalize_watchlist_entry(
            {
                "risk": "Justices themselves, clerks",
                "series": "KXSCOTUSRESIGN",
                "name": "SCOTUS Resignations",
                "actors": [
                    {"role": "Supreme Court justice", "clearance_tier": 3},
                    {"role": "Supreme Court clerk", "clearance_tier": 2},
                ],
            },
            "scotus",
        )
        self.assertEqual(entry["clearance_tier"], 3)
        actors = json.loads(entry["actors_json"])
        self.assertEqual(actors[0]["role"], "Supreme Court justice")

    def test_series_category_map_maps_kxfed_to_economic_data(self):
        watchlist_loader.series_category_map.cache_clear()
        mapping = watchlist_loader.series_category_map()
        self.assertEqual(mapping.get("KXFED"), "economic_data")
        self.assertEqual(mapping.get("KXNEXTAG"), "executive_actions")
        self.assertEqual(watchlist_loader.category_for_series("KXFED"), "economic_data")
        self.assertEqual(watchlist_loader.category_for_series("UNKNOWN"), "")

    def test_unknown_series_falls_back_to_risk_split(self):
        entry = watchlist_loader.normalize_watchlist_entry(
            {
                "risk": "Alpha desk, Beta desk",
                "series": "KXUNKNOWN",
                "name": "Unknown Market",
            },
            "test",
        )
        self.assertEqual(entry["clearance_tier"], 1)
        self.assertEqual(len(entry["actors"]), 2)
        self.assertEqual(entry["actors"][0]["clearance_tier"], 1)

    def test_load_watchlist_from_temp_file(self):
        payload = {
            "test_category": [
                {
                    "risk": "WH counsel",
                    "series": "KXTRUMPPARDONS",
                    "name": "Trump Pardons",
                }
            ]
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(payload, handle)
            temp_path = Path(handle.name)

        try:
            markets = watchlist_loader.load_watchlist(temp_path)
            self.assertEqual(len(markets), 1)
            self.assertEqual(markets[0]["series"], "KXTRUMPPARDONS")
            self.assertEqual(markets[0]["clearance_tier"], 3)
        finally:
            temp_path.unlink(missing_ok=True)

    def test_max_clearance_tier_empty_defaults_to_one(self):
        self.assertEqual(watchlist_loader.max_clearance_tier([]), 1)


if __name__ == "__main__":
    unittest.main()
