"""
Scheduled macro/policy event windows for correlation temporal adjustments.

When an anomaly and news article fall inside a known release window (e.g. FOMC,
CPI), long pre-news lead times in the 8–48h decay band receive a temporal floor
instead of heavy decay — recurring events are expected positioning windows.
"""

from __future__ import annotations

from datetime import datetime, timezone

import config

# Tier 3 pre-news band starts after 8 hours (must match correlation_engine).
TIER2_PRE_NEWS_SECONDS = 8 * 3600


def scheduled_events_enabled() -> bool:
    cfg = config.load_config().get("scheduled_events", {})
    return bool(cfg.get("enabled", False))


def get_scheduled_events() -> list[dict]:
    cfg = config.load_config().get("scheduled_events", {})
    if not cfg.get("enabled", False):
        return []
    events = cfg.get("events", [])
    return events if isinstance(events, list) else []


def _parse_event_ts(date_str: str) -> int:
    """Parse YYYY-MM-DD to 18:00 UTC (approximate US afternoon release)."""
    dt = datetime.strptime(date_str.strip(), "%Y-%m-%d").replace(
        hour=18, minute=0, second=0, tzinfo=timezone.utc
    )
    return int(dt.timestamp())


def find_active_event(
    series_ticker: str,
    anomaly_ts: int,
    article_ts: int,
) -> dict | None:
    """Return the scheduled event config if either timestamp is in its window."""
    for event in get_scheduled_events():
        series_list = event.get("series", [])
        if series_ticker not in series_list:
            continue

        hours_before = int(event.get("window_hours_before", 48))
        hours_after = int(event.get("window_hours_after", 6))
        for date_str in event.get("dates", []):
            try:
                event_ts = _parse_event_ts(date_str)
            except ValueError:
                continue
            window_start = event_ts - (hours_before * 3600)
            window_end = event_ts + (hours_after * 3600)
            if window_start <= article_ts <= window_end:
                return {**event, "event_ts": event_ts, "event_date": date_str}
            if window_start <= anomaly_ts <= window_end:
                return {**event, "event_ts": event_ts, "event_date": date_str}
    return None


def adjust_temporal_for_expected_event(
    series_ticker: str,
    time_diff: int,
    anomaly_ts: int,
    article_ts: int,
    base_temporal: float,
) -> tuple[float, dict[str, float | str]]:
    """Apply temporal floor for long pre-news leads inside scheduled event windows."""
    meta: dict[str, float | str] = {}
    if not scheduled_events_enabled() or time_diff < 0:
        return base_temporal, meta
    if time_diff < TIER2_PRE_NEWS_SECONDS:
        return base_temporal, meta

    event = find_active_event(series_ticker, anomaly_ts, article_ts)
    if not event:
        return base_temporal, meta

    floor = float(event.get("temporal_floor", 0.85))
    if base_temporal >= floor:
        return base_temporal, meta

    meta = {
        "expected_event": str(event.get("label", "scheduled event")),
        "event_date": str(event.get("event_date", "")),
        "temporal_floor_applied": floor,
        "base_temporal": round(base_temporal, 3),
    }
    return floor, meta
