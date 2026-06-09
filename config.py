"""
Shared configuration and datetime utilities for pmwatch.
Eliminates duplication across api.py, scheduler.py, collector.py, scorer.py, cluster_scorer.py.
"""

import json
import os
from datetime import datetime, timezone
from functools import lru_cache

def load_config() -> dict:
    """Load config.json with fallback."""
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    try:
        with open(config_path, "r") as f:
            return json.load(f)
    except Exception:
        return {"scheduler_interval_minutes": 30}


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