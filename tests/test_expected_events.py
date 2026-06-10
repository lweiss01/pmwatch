import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import expected_events


class TestExpectedEvents(unittest.TestCase):
    def test_find_active_event_inside_fomc_window(self):
        anomaly_ts = int(datetime(2026, 6, 17, 0, 0, tzinfo=timezone.utc).timestamp())
        article_ts = anomaly_ts + (20 * 3600)
        event = expected_events.find_active_event("KXFED", anomaly_ts, article_ts)
        self.assertIsNotNone(event)
        self.assertEqual(event.get("label"), "FOMC rate decision")
        self.assertEqual(event.get("event_date"), "2026-06-18")

    def test_find_active_event_outside_window(self):
        anomaly_ts = int(datetime(2026, 2, 1, 0, 0, tzinfo=timezone.utc).timestamp())
        article_ts = anomaly_ts + (20 * 3600)
        event = expected_events.find_active_event("KXFED", anomaly_ts, article_ts)
        self.assertIsNone(event)

    def test_adjust_temporal_applies_floor_for_long_pre_news_lead(self):
        anomaly_ts = int(datetime(2026, 6, 17, 0, 0, tzinfo=timezone.utc).timestamp())
        article_ts = anomaly_ts + (20 * 3600)
        base_temporal = 0.45
        adjusted, meta = expected_events.adjust_temporal_for_expected_event(
            "KXFED",
            time_diff=20 * 3600,
            anomaly_ts=anomaly_ts,
            article_ts=article_ts,
            base_temporal=base_temporal,
        )
        self.assertGreaterEqual(adjusted, 0.85)
        self.assertEqual(meta.get("expected_event"), "FOMC rate decision")
        self.assertEqual(meta.get("temporal_floor_applied"), 0.85)

    def test_adjust_temporal_skips_post_news_reaction(self):
        anomaly_ts = int(datetime(2026, 6, 17, 0, 0, tzinfo=timezone.utc).timestamp())
        article_ts = anomaly_ts - 3600
        adjusted, meta = expected_events.adjust_temporal_for_expected_event(
            "KXFED",
            time_diff=-3600,
            anomaly_ts=anomaly_ts,
            article_ts=article_ts,
            base_temporal=0.7,
        )
        self.assertEqual(adjusted, 0.7)
        self.assertEqual(meta, {})


if __name__ == "__main__":
    unittest.main()
