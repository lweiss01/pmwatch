"""
Thin orchestrator for news feed ingestion and anomaly correlation.

Implementation lives in feed_ingestion.py and correlation_engine.py.
"""

from correlation_engine import (
    MIN_CORRELATION_CONFIDENCE,
    SOURCE_WEIGHTS,
    calculate_correlation_confidence,
    correlate_all_recent_anomalies,
    format_source_weight,
    get_source_weight,
    rebuild_correlations,
    temporal_multiplier,
)
from feed_ingestion import (
    DEFAULT_FEEDS,
    fetch_and_ingest_feeds,
    parse_fed_register_json,
    parse_rss_string,
)

__all__ = [
    "DEFAULT_FEEDS",
    "MIN_CORRELATION_CONFIDENCE",
    "SOURCE_WEIGHTS",
    "calculate_correlation_confidence",
    "correlate_all_recent_anomalies",
    "fetch_and_ingest_feeds",
    "rebuild_correlations",
    "format_source_weight",
    "get_source_weight",
    "parse_fed_register_json",
    "parse_rss_string",
    "temporal_multiplier",
]


if __name__ == "__main__":
    import logging

    import db

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    db.init_db()
    fetch_and_ingest_feeds()
