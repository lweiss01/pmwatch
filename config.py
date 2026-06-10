"""
Shared configuration and datetime utilities for pmwatch.
Eliminates duplication across api.py, scheduler.py, collector.py, scorer.py, cluster_scorer.py.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from typing import Any

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
CONFIG_BACKUP_PATH = CONFIG_PATH + ".bak"

DEFAULT_CORRELATION_THRESHOLDS = {
    "min_confidence": 12.0,
    "min_match_quality": 0.35,
}
DEFAULT_MATCHER_THRESHOLDS = {
    "min_ingest_quality": 0.35,
}
DEFAULT_SCORER_THRESHOLDS = {
    "yellow_score": 25.0,
    "red_score": 60.0,
    "dedup_hours": 2,
    "score_delta_threshold": 0.20,
}

SETTINGS_PATCH_ALLOWLIST = {
    "scheduler_interval_minutes",
    "scheduled_events",
    "correlation",
    "matcher",
    "scorer",
}


def _threshold_section(name: str, defaults: dict[str, Any]) -> dict[str, Any]:
    """Return config threshold section merged over built-in defaults."""
    cfg = load_config()
    section = cfg.get(name, {})
    if not isinstance(section, dict):
        return dict(defaults)
    merged = dict(defaults)
    for key, value in section.items():
        if key in defaults and value is not None:
            merged[key] = value
    return merged


def get_correlation_thresholds() -> dict[str, Any]:
    return _threshold_section("correlation", DEFAULT_CORRELATION_THRESHOLDS)


def get_min_correlation_confidence() -> float:
    return float(get_correlation_thresholds()["min_confidence"])


def get_min_correlation_match_quality() -> float:
    return float(get_correlation_thresholds()["min_match_quality"])


def get_matcher_thresholds() -> dict[str, Any]:
    return _threshold_section("matcher", DEFAULT_MATCHER_THRESHOLDS)


def get_min_ingest_quality() -> float:
    return float(get_matcher_thresholds()["min_ingest_quality"])


def get_scorer_thresholds() -> dict[str, Any]:
    return _threshold_section("scorer", DEFAULT_SCORER_THRESHOLDS)


def get_yellow_score() -> float:
    return float(get_scorer_thresholds()["yellow_score"])


def get_red_score() -> float:
    return float(get_scorer_thresholds()["red_score"])


def get_dedup_hours() -> int:
    return int(get_scorer_thresholds()["dedup_hours"])


def get_score_delta_threshold() -> float:
    return float(get_scorer_thresholds()["score_delta_threshold"])


def load_config() -> dict:
    """Load config.json with fallback."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"scheduler_interval_minutes": 30}


def save_config(cfg: dict) -> None:
    """Atomically write config.json with a backup copy."""
    if os.path.exists(CONFIG_PATH):
        shutil.copy2(CONFIG_PATH, CONFIG_BACKUP_PATH)
    temp_path = CONFIG_PATH + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    os.replace(temp_path, CONFIG_PATH)


def validate_settings_patch(patch: dict[str, Any]) -> list[str]:
    """Return validation errors for a partial settings update."""
    errors: list[str] = []
    unknown = set(patch) - SETTINGS_PATCH_ALLOWLIST
    if unknown:
        errors.append(f"Unsupported settings keys: {', '.join(sorted(unknown))}")

    if "scheduler_interval_minutes" in patch:
        interval = patch["scheduler_interval_minutes"]
        if not isinstance(interval, int) or interval < 5 or interval > 1440:
            errors.append("scheduler_interval_minutes must be an integer between 5 and 1440")

    scheduled = patch.get("scheduled_events")
    if scheduled is not None:
        if not isinstance(scheduled, dict):
            errors.append("scheduled_events must be an object")
        else:
            if "enabled" in scheduled and not isinstance(scheduled["enabled"], bool):
                errors.append("scheduled_events.enabled must be a boolean")
            events = scheduled.get("events")
            if events is not None:
                if not isinstance(events, list):
                    errors.append("scheduled_events.events must be a list")
                else:
                    for idx, event in enumerate(events):
                        if not isinstance(event, dict):
                            errors.append(f"scheduled_events.events[{idx}] must be an object")
                            continue
                        floor = event.get("temporal_floor")
                        if floor is not None and (
                            not isinstance(floor, (int, float))
                            or floor < 0.3
                            or floor > 1.0
                        ):
                            errors.append(
                                f"scheduled_events.events[{idx}].temporal_floor "
                                "must be between 0.3 and 1.0"
                            )
                        for window_key in ("window_hours_before", "window_hours_after"):
                            value = event.get(window_key)
                            if value is not None and (
                                not isinstance(value, int) or value < 1 or value > 168
                            ):
                                errors.append(
                                    f"scheduled_events.events[{idx}].{window_key} "
                                    "must be an integer between 1 and 168"
                                )

    correlation = patch.get("correlation")
    if correlation is not None:
        if not isinstance(correlation, dict):
            errors.append("correlation must be an object")
        else:
            min_conf = correlation.get("min_confidence")
            if min_conf is not None and (
                not isinstance(min_conf, (int, float)) or min_conf < 1 or min_conf > 100
            ):
                errors.append("correlation.min_confidence must be between 1 and 100")
            min_match = correlation.get("min_match_quality")
            if min_match is not None and (
                not isinstance(min_match, (int, float)) or min_match < 0.1 or min_match > 1.0
            ):
                errors.append("correlation.min_match_quality must be between 0.1 and 1.0")

    matcher = patch.get("matcher")
    if matcher is not None:
        if not isinstance(matcher, dict):
            errors.append("matcher must be an object")
        else:
            ingest_quality = matcher.get("min_ingest_quality")
            if ingest_quality is not None and (
                not isinstance(ingest_quality, (int, float))
                or ingest_quality < 0.1
                or ingest_quality > 1.0
            ):
                errors.append("matcher.min_ingest_quality must be between 0.1 and 1.0")

    scorer = patch.get("scorer")
    if scorer is not None:
        if not isinstance(scorer, dict):
            errors.append("scorer must be an object")
        else:
            yellow = scorer.get("yellow_score")
            if yellow is not None and (
                not isinstance(yellow, (int, float)) or yellow < 1 or yellow > 99
            ):
                errors.append("scorer.yellow_score must be between 1 and 99")
            red = scorer.get("red_score")
            if red is not None and (
                not isinstance(red, (int, float)) or red < 2 or red > 100
            ):
                errors.append("scorer.red_score must be between 2 and 100")
            dedup = scorer.get("dedup_hours")
            if dedup is not None and (
                not isinstance(dedup, int) or dedup < 1 or dedup > 48
            ):
                errors.append("scorer.dedup_hours must be an integer between 1 and 48")
            delta = scorer.get("score_delta_threshold")
            if delta is not None and (
                not isinstance(delta, (int, float)) or delta < 0.05 or delta > 1.0
            ):
                errors.append("scorer.score_delta_threshold must be between 0.05 and 1.0")

    if not errors:
        base = load_config()
        merged_scorer = {
            **DEFAULT_SCORER_THRESHOLDS,
            **base.get("scorer", {}),
            **(patch.get("scorer") or {}),
        }
        if merged_scorer["yellow_score"] >= merged_scorer["red_score"]:
            errors.append("scorer.yellow_score must be less than scorer.red_score")

    return errors


def merge_settings_patch(base: dict, patch: dict[str, Any]) -> dict:
    """Deep-merge an allowlisted settings patch into a config copy."""
    merged = json.loads(json.dumps(base))
    for key, value in patch.items():
        if key not in SETTINGS_PATCH_ALLOWLIST:
            continue
        if key == "scheduled_events" and isinstance(value, dict):
            current = merged.setdefault("scheduled_events", {})
            for sub_key, sub_value in value.items():
                if sub_key == "events" and isinstance(sub_value, list):
                    current_events = current.get("events", [])
                    by_label = {
                        event.get("label"): event
                        for event in current_events
                        if event.get("label")
                    }
                    for patched in sub_value:
                        if not isinstance(patched, dict):
                            continue
                        label = patched.get("label")
                        if not label:
                            continue
                        if label in by_label:
                            by_label[label].update(patched)
                        else:
                            by_label[label] = patched
                    current["events"] = list(by_label.values())
                else:
                    current[sub_key] = sub_value
        elif key in ("correlation", "matcher", "scorer") and isinstance(value, dict):
            defaults = {
                "correlation": DEFAULT_CORRELATION_THRESHOLDS,
                "matcher": DEFAULT_MATCHER_THRESHOLDS,
                "scorer": DEFAULT_SCORER_THRESHOLDS,
            }[key]
            current = merged.setdefault(key, dict(defaults))
            for sub_key, sub_value in value.items():
                if sub_key in defaults:
                    current[sub_key] = sub_value
        else:
            merged[key] = value
    return merged


def get_public_settings(cfg: dict | None = None) -> dict:
    """Return operator-facing settings snapshot for API/dashboard."""
    data = cfg or load_config()
    scheduled = data.get("scheduled_events", {})
    events = []
    today = datetime.now(timezone.utc).date()
    for event in scheduled.get("events", []):
        dates = sorted(event.get("dates", []))
        upcoming = None
        for date_str in dates:
            try:
                parsed = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            if parsed >= today:
                upcoming = date_str
                break
        events.append({
            "label": event.get("label"),
            "series": event.get("series", []),
            "dates_count": len(dates),
            "next_date": upcoming,
            "window_hours_before": event.get("window_hours_before"),
            "window_hours_after": event.get("window_hours_after"),
            "temporal_floor": event.get("temporal_floor"),
        })
    return {
        "scheduler_interval_minutes": data.get("scheduler_interval_minutes", 30),
        "scheduled_events": {
            "enabled": scheduled.get("enabled", False),
            "events": events,
            "refresh": scheduled.get("refresh", {}),
        },
        "correlation": {
            **DEFAULT_CORRELATION_THRESHOLDS,
            **data.get("correlation", {}),
        },
        "matcher": {
            **DEFAULT_MATCHER_THRESHOLDS,
            **data.get("matcher", {}),
        },
        "scorer": {
            **DEFAULT_SCORER_THRESHOLDS,
            **data.get("scorer", {}),
        },
        "threshold_defaults": {
            "correlation": dict(DEFAULT_CORRELATION_THRESHOLDS),
            "matcher": dict(DEFAULT_MATCHER_THRESHOLDS),
            "scorer": dict(DEFAULT_SCORER_THRESHOLDS),
        },
        "restart_required_for": ["scheduler_interval_minutes"],
    }


def get_scheduler_interval() -> int:
    """Get scheduler interval from config with default fallback."""
    return load_config().get("scheduler_interval_minutes", 60)


# --- Datetime utilities ---

def parse_iso_datetime(iso_str: str) -> datetime:
    """Parse ISO datetime string, handling 'Z' suffix."""
    return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))


def utc_now_iso() -> str:
    """Current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def timestamp_to_iso(ts: float) -> str:
    """Convert Unix timestamp to UTC ISO 8601 string."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()