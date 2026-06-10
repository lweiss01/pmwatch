"""
Detect correlated anomaly patterns across multiple series sharing the same MNPI actor group.

When the same risk_group / mnpi_actors string appears on anomalies across 2+ distinct
series_tickers within a short window, it may indicate coordinated multi-leg positioning.
"""

from __future__ import annotations

import logging
import math
import time

import config
import db

log = logging.getLogger(__name__)

CROSS_MARKET_WINDOW_HOURS = 24
MIN_CROSS_MARKET_SERIES = 2


def _group_time_windows(events: list[dict], window_hours: float = CROSS_MARKET_WINDOW_HOURS) -> list[list[dict]]:
    """Group anomalies so each window spans at most window_hours from first to last event."""
    if not events:
        return []

    events_sorted = sorted(events, key=lambda e: e["detected_ts"])
    window_seconds = int(window_hours * 3600)
    groups: list[list[dict]] = [[events_sorted[0]]]

    for event in events_sorted[1:]:
        candidate_span = event["detected_ts"] - groups[-1][0]["detected_ts"]
        if candidate_span <= window_seconds:
            groups[-1].append(event)
        else:
            groups.append([event])

    return groups


def _cross_market_score(events: list[dict], series_count: int) -> float:
    scores = [float(e["anomaly_score"]) for e in events]
    total = sum(scores)
    series_bonus = 1.0 + (0.25 * max(0, series_count - 1))
    return round(total * series_bonus * math.log(series_count + 1), 2)


def build_cross_market_record(mnpi_actors: str, events: list[dict]) -> dict:
    events_sorted = sorted(events, key=lambda e: e["detected_ts"])
    series_tickers = sorted({e["series_ticker"] for e in events_sorted if e.get("series_ticker")})
    tickers = sorted({e["ticker"] for e in events_sorted if e.get("ticker")})
    scores = [float(e["anomaly_score"]) for e in events_sorted]
    first = events_sorted[0]
    last = events_sorted[-1]
    series_count = len(series_tickers)

    return {
        "mnpi_actors": mnpi_actors,
        "series_tickers": ",".join(series_tickers),
        "tickers": ",".join(tickers),
        "window_start_ts": first["detected_ts"],
        "window_start_time": first["detected_time"],
        "window_end_ts": last["detected_ts"],
        "window_end_time": last["detected_time"],
        "anomaly_count": len(events_sorted),
        "peak_score": round(max(scores), 2),
        "total_score": round(sum(scores), 2),
        "cluster_score": _cross_market_score(events_sorted, series_count),
        "computed_time": config.utc_now_iso(),
        "computed_ts": int(time.time()),
    }


def run_cross_market_scorer(lookback_days: int = 7) -> int:
    """Find and persist cross-series anomaly clusters grouped by mnpi_actors."""
    log.info("=== Cross-market scorer started ===")
    conn = db.get_conn()
    c = conn.cursor()
    cutoff_ts = int(time.time()) - (lookback_days * 86400)
    c.execute(
        """
        SELECT id, ticker, market_title, series_ticker, risk_group, mnpi_actors,
               detected_ts, detected_time, anomaly_score
        FROM anomalies
        WHERE detected_ts >= ?
          AND mnpi_actors IS NOT NULL
          AND TRIM(mnpi_actors) != ''
          AND series_ticker IS NOT NULL
          AND TRIM(series_ticker) != ''
        ORDER BY mnpi_actors, detected_ts ASC
        """,
        (cutoff_ts,),
    )
    all_anomalies = [dict(r) for r in c.fetchall()]
    conn.close()

    by_actor: dict[str, list[dict]] = {}
    for event in all_anomalies:
        actor_key = event["mnpi_actors"].strip()
        by_actor.setdefault(actor_key, []).append(event)

    records: list[dict] = []
    for actor_key, events in by_actor.items():
        for window_events in _group_time_windows(events):
            series_tickers = {e["series_ticker"] for e in window_events if e.get("series_ticker")}
            if len(series_tickers) < MIN_CROSS_MARKET_SERIES:
                continue

            record = build_cross_market_record(actor_key, window_events)
            records.append(record)
            log.info(
                "CROSS-MARKET %s | series=%s | count=%d | score=%.1f",
                actor_key[:40],
                record["series_tickers"],
                record["anomaly_count"],
                record["cluster_score"],
            )

    written = db.upsert_cross_market_clusters_bulk(records)
    log.info("=== Cross-market scorer complete: %d clusters written ===", written)
    return written


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    db.init_db()
    run_cross_market_scorer()
