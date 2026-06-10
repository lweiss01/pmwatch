import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import event_calendar_refresh

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


class TestEventCalendarRefresh(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(
            os.path.join(FIXTURES_DIR, "fomc_calendar_sample.html"),
            encoding="utf-8",
        ) as f:
            cls.fomc_html = f.read()
        with open(
            os.path.join(FIXTURES_DIR, "cpi_schedule_sample.html"),
            encoding="utf-8",
        ) as f:
            cls.cpi_html = f.read()

    def test_parse_fomc_dates_from_fixture(self):
        dates = event_calendar_refresh.parse_fomc_dates(
            self.fomc_html,
            min_year=2026,
            max_year=2027,
        )
        self.assertIn("2026-01-28", dates)
        self.assertIn("2026-03-18", dates)
        self.assertIn("2026-12-09", dates)
        self.assertIn("2027-03-17", dates)
        self.assertEqual(len([d for d in dates if d.startswith("2026-")]), 8)

    def test_parse_cpi_dates_from_fixture(self):
        dates = event_calendar_refresh.parse_cpi_dates(
            self.cpi_html,
            min_year=2026,
            max_year=2026,
        )
        self.assertEqual(
            dates,
            ["2026-01-14", "2026-02-11", "2026-03-11", "2026-04-10"],
        )

    def test_merge_preserves_windows_and_floors(self):
        cfg = {
            "scheduled_events": {
                "enabled": True,
                "events": [
                    {
                        "series": ["KXFED"],
                        "label": "FOMC rate decision",
                        "dates": ["2026-01-01"],
                        "window_hours_before": 48,
                        "window_hours_after": 6,
                        "temporal_floor": 0.85,
                    },
                    {
                        "series": ["KXCPI"],
                        "label": "CPI release",
                        "dates": ["2026-01-01"],
                        "window_hours_before": 24,
                        "window_hours_after": 6,
                        "temporal_floor": 0.8,
                    },
                ],
            }
        }
        merged = event_calendar_refresh.merge_calendar_into_config(
            cfg,
            fomc_dates=["2026-03-18", "2026-06-17"],
            cpi_dates=["2026-02-11", "2026-03-11"],
        )
        fomc = merged["scheduled_events"]["events"][0]
        cpi = merged["scheduled_events"]["events"][1]
        self.assertEqual(fomc["dates"], ["2026-03-18", "2026-06-17"])
        self.assertEqual(fomc["window_hours_before"], 48)
        self.assertEqual(fomc["temporal_floor"], 0.85)
        self.assertEqual(cpi["dates"], ["2026-02-11", "2026-03-11"])

    def test_refresh_keeps_existing_dates_when_all_sources_fail(self):
        base_cfg = {
            "scheduler_interval_minutes": 30,
            "scheduled_events": {
                "enabled": True,
                "refresh": {"enabled": True},
                "events": [
                    {
                        "series": ["KXFED"],
                        "label": "FOMC rate decision",
                        "dates": ["2026-06-18"],
                        "window_hours_before": 48,
                        "window_hours_after": 6,
                        "temporal_floor": 0.85,
                    }
                ],
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, "config.json")
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(base_cfg, f)
            original_path = config.CONFIG_PATH
            original_backup = config.CONFIG_BACKUP_PATH
            config.CONFIG_PATH = cfg_path
            config.CONFIG_BACKUP_PATH = cfg_path + ".bak"
            try:
                with patch.object(event_calendar_refresh, "_http_get", side_effect=OSError("offline")):
                    result = event_calendar_refresh.refresh_event_calendar(dry_run=False)
                self.assertEqual(result.status, "failed")
                self.assertFalse(result.updated)
                with open(cfg_path, encoding="utf-8") as f:
                    saved = json.load(f)
                self.assertEqual(
                    saved["scheduled_events"]["events"][0]["dates"],
                    ["2026-06-18"],
                )
            finally:
                config.CONFIG_PATH = original_path
                config.CONFIG_BACKUP_PATH = original_backup

    def test_refresh_partial_update_when_cpi_fails(self):
        base_cfg = {
            "scheduler_interval_minutes": 30,
            "scheduled_events": {
                "enabled": True,
                "refresh": {"enabled": True},
                "events": [
                    {
                        "series": ["KXFED"],
                        "label": "FOMC rate decision",
                        "dates": ["2026-01-01"],
                        "window_hours_before": 48,
                        "window_hours_after": 6,
                        "temporal_floor": 0.85,
                    },
                    {
                        "series": ["KXCPI"],
                        "label": "CPI release",
                        "dates": ["2026-01-14"],
                        "window_hours_before": 24,
                        "window_hours_after": 6,
                        "temporal_floor": 0.8,
                    },
                ],
            },
        }

        def fake_http_get(url: str) -> str:
            if "federalreserve.gov" in url:
                return self.fomc_html
            raise OSError("cpi blocked")

        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, "config.json")
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(base_cfg, f)
            original_path = config.CONFIG_PATH
            original_backup = config.CONFIG_BACKUP_PATH
            config.CONFIG_PATH = cfg_path
            config.CONFIG_BACKUP_PATH = cfg_path + ".bak"
            try:
                with patch.object(event_calendar_refresh, "_http_get", side_effect=fake_http_get):
                    result = event_calendar_refresh.refresh_event_calendar(dry_run=False)
                self.assertEqual(result.status, "partial")
                self.assertTrue(result.updated)
                with open(cfg_path, encoding="utf-8") as f:
                    saved = json.load(f)
                fomc_dates = saved["scheduled_events"]["events"][0]["dates"]
                cpi_dates = saved["scheduled_events"]["events"][1]["dates"]
                self.assertNotEqual(fomc_dates, ["2026-01-01"])
                self.assertEqual(cpi_dates, ["2026-01-14"])
                self.assertEqual(
                    saved["scheduled_events"]["refresh"]["last_status"],
                    "partial",
                )
            finally:
                config.CONFIG_PATH = original_path
                config.CONFIG_BACKUP_PATH = original_backup


if __name__ == "__main__":
    unittest.main()
