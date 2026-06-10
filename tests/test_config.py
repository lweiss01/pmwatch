import json
import os
import sys
import tempfile
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config


class TestConfigSettings(unittest.TestCase):
    def test_validate_settings_patch_rejects_unknown_keys(self):
        errors = config.validate_settings_patch({"unknown_key": 1})
        self.assertTrue(any("Unsupported settings keys" in err for err in errors))

    def test_validate_scheduler_interval_bounds(self):
        errors = config.validate_settings_patch({"scheduler_interval_minutes": 2})
        self.assertTrue(errors)
        errors = config.validate_settings_patch({"scheduler_interval_minutes": 30})
        self.assertEqual(errors, [])

    def test_merge_settings_patch_updates_event_by_label(self):
        base = {
            "scheduler_interval_minutes": 30,
            "scheduled_events": {
                "enabled": True,
                "events": [
                    {
                        "label": "FOMC rate decision",
                        "series": ["KXFED"],
                        "temporal_floor": 0.85,
                        "window_hours_before": 48,
                        "window_hours_after": 6,
                    }
                ],
            },
        }
        merged = config.merge_settings_patch(base, {
            "scheduled_events": {
                "enabled": False,
                "events": [
                    {
                        "label": "FOMC rate decision",
                        "temporal_floor": 0.9,
                        "window_hours_before": 36,
                    }
                ],
            }
        })
        self.assertFalse(merged["scheduled_events"]["enabled"])
        event = merged["scheduled_events"]["events"][0]
        self.assertEqual(event["temporal_floor"], 0.9)
        self.assertEqual(event["window_hours_before"], 36)
        self.assertEqual(event["window_hours_after"], 6)

    def test_save_config_writes_backup_and_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, "config.json")
            backup_path = cfg_path + ".bak"
            original_path = config.CONFIG_PATH
            original_backup = config.CONFIG_BACKUP_PATH
            config.CONFIG_PATH = cfg_path
            config.CONFIG_BACKUP_PATH = backup_path
            try:
                initial = {"scheduler_interval_minutes": 30, "scheduled_events": {"enabled": True}}
                config.save_config(initial)
                config.save_config({"scheduler_interval_minutes": 45, "scheduled_events": {"enabled": False}})
                self.assertTrue(os.path.exists(backup_path))
                loaded = config.load_config()
                self.assertEqual(loaded["scheduler_interval_minutes"], 45)
                self.assertFalse(loaded["scheduled_events"]["enabled"])
            finally:
                config.CONFIG_PATH = original_path
                config.CONFIG_BACKUP_PATH = original_backup

    def test_get_public_settings_includes_thresholds(self):
        snapshot = config.get_public_settings({
            "scheduler_interval_minutes": 30,
            "correlation": {"min_confidence": 15.0},
            "matcher": {"min_ingest_quality": 0.4},
            "scorer": {"yellow_score": 30.0},
            "scheduled_events": {
                "enabled": True,
                "events": [
                    {
                        "label": "FOMC rate decision",
                        "series": ["KXFED"],
                        "dates": ["2026-12-10", "2027-01-01"],
                        "window_hours_before": 48,
                        "window_hours_after": 6,
                        "temporal_floor": 0.85,
                    }
                ],
                "refresh": {"last_status": "ok"},
            },
        })
        self.assertEqual(snapshot["scheduler_interval_minutes"], 30)
        self.assertEqual(snapshot["scheduled_events"]["events"][0]["next_date"], "2026-12-10")
        self.assertEqual(snapshot["correlation"]["min_confidence"], 15.0)
        self.assertEqual(snapshot["correlation"]["min_match_quality"], 0.35)
        self.assertEqual(snapshot["matcher"]["min_ingest_quality"], 0.4)
        self.assertEqual(snapshot["scorer"]["yellow_score"], 30.0)
        self.assertEqual(snapshot["threshold_defaults"]["correlation"]["min_confidence"], 12.0)

    def test_validate_threshold_patch_bounds(self):
        confidence_errors = config.validate_settings_patch({
            "correlation": {"min_confidence": 0.5},
        })
        self.assertTrue(any("min_confidence" in err for err in confidence_errors))

        ingest_errors = config.validate_settings_patch({
            "matcher": {"min_ingest_quality": 2.0},
        })
        self.assertTrue(any("min_ingest_quality" in err for err in ingest_errors))

        scorer_errors = config.validate_settings_patch({
            "scorer": {"yellow_score": 70, "red_score": 60},
        })
        self.assertTrue(any("yellow_score must be less than" in err for err in scorer_errors))

    def test_merge_threshold_patch(self):
        base = {
            "scheduler_interval_minutes": 30,
            "correlation": {"min_confidence": 12.0, "min_match_quality": 0.35},
            "matcher": {"min_ingest_quality": 0.35},
            "scorer": {
                "yellow_score": 25.0,
                "red_score": 60.0,
                "dedup_hours": 2,
                "score_delta_threshold": 0.2,
            },
        }
        merged = config.merge_settings_patch(base, {
            "correlation": {"min_confidence": 18.0},
            "scorer": {"dedup_hours": 4},
        })
        self.assertEqual(merged["correlation"]["min_confidence"], 18.0)
        self.assertEqual(merged["correlation"]["min_match_quality"], 0.35)
        self.assertEqual(merged["scorer"]["dedup_hours"], 4)
        self.assertEqual(merged["scorer"]["yellow_score"], 25.0)


if __name__ == "__main__":
    unittest.main()
