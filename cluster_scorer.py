"""
pmwatch — cluster_scorer.py

Detects multi-anomaly accumulation patterns on the same ticker.

A "cluster" is a series of anomaly events on the same market where each
consecutive event is within CLUSTER_GAP_HOURS of the previous one.
A single isolated anomaly is not a cluster.  Two or more anomalies within
the gap window form one cluster and are scored together.

Key metrics
-----------
anomaly_count       How many distinct anomaly events in this cluster.
directional_consistency
                    Fraction of events that share a dominant direction
                    (NO-side block accumulation).  0.0 = mixed / noise,
                    1.0 = every event pointed the same way.
score_trend         Slope of anomaly_score over time within the cluster.
                    Positive = escalating (more suspicious).
                    Near-zero = flat / random.
peak_score          Highest individual anomaly score in the cluster.
cluster_score       Compound: peak_score × (1 + consistency) × escalation_multiplier
                    × log(anomaly_count + 1)

Usage
-----
    python cluster_scorer.py          # run once
    # or import and call run_cluster_scorer() from scheduler.py
"""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone

import db
import config

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

# Max hours between two anomaly events for them to belong to the same cluster.
CLUSTER_GAP_HOURS = 72

# Minimum number of anomaly events to form a reportable cluster.
MIN_CLUSTER_SIZE = 2

# Threshold above which directional_flag is considered "directional".
DIRECTIONAL_THRESHOLD = 0.05


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slope(xs: list[float], ys: list[float]) -> float:
    """Linear regression slope — positive means escalating scores."""
    n = len(xs)
    if n < 2:
        return 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    return num / den if den != 0 else 0.0


def _dominant_side(event: dict) -> str:
    side = event.get("dominant_side")
    if side in ("yes", "no", "neutral"):
        return side
    flag = event.get("directional_flag") or 0.0
    if flag > 0.05:
        return "no"
    return "neutral"


def _directional_consistency(events: list[dict]) -> float:
    """Fraction of events sharing the same dominant_side (yes/no accumulation)."""
    if not events:
        return 0.0
    sides = [_dominant_side(e) for e in events]
    directional = [s for s in sides if s != "neutral"]
    if not directional:
        return 0.0
    target = max(set(directional), key=directional.count)
    return sum(1 for s in sides if s == target) / len(events)


def _score_trend(events: list[dict]) -> float:
    """Normalised slope of anomaly_score over time.

    Returns a value roughly in [-1, +1]:
      > 0  scores are escalating → more suspicious
      ~ 0  flat / random
      < 0  scores are declining
    """
    if len(events) < 2:
        return 0.0
    xs = [float(e["detected_ts"]) for e in events]
    ys = [float(e["anomaly_score"]) for e in events]
    raw_slope = _slope(xs, ys)
    # Normalise: raw slope is in score-units/second — scale to something readable.
    # A slope of 1 score-point per hour → normalised ~1.0
    return raw_slope * 3600.0


def _cluster_score(peak: float, count: int, consistency: float, trend: float) -> float:
    """Compound cluster suspicion score.

    Components:
      peak_score            — ceiling on individual event severity
      consistency bonus     — consistent direction doubles the score
      escalation multiplier — escalating pattern adds up to 50%
      count factor          — log(count) rewards repeated anomalies
    """
    escalation = max(0.0, min(0.5, trend / 20.0))   # cap escalation bonus at +50%
    return round(
        peak
        * (1.0 + consistency)
        * (1.0 + escalation)
        * math.log(count + 1),
        2,
    )


# ── Gap-based clustering ───────────────────────────────────────────────────────

def _group_into_clusters(
    events: list[dict],
    gap_hours: float = CLUSTER_GAP_HOURS,
) -> list[list[dict]]:
    """Split a ticker's anomaly history into temporal clusters.

    Events must be sorted by detected_ts ascending.
    A new cluster starts whenever the gap between consecutive events
    exceeds gap_hours.
    """
    if not events:
        return []

    gap_seconds = gap_hours * 3600
    clusters: list[list[dict]] = [[events[0]]]

    for evt in events[1:]:
        prev_ts = clusters[-1][-1]["detected_ts"]
        if (evt["detected_ts"] - prev_ts) <= gap_seconds:
            clusters[-1].append(evt)
        else:
            clusters.append([evt])

    return clusters


# ── Main scorer ───────────────────────────────────────────────────────────────

def compute_cluster_record(ticker: str, events: list[dict]) -> dict:
    """Build a cluster record from a list of anomaly events."""
    events_sorted = sorted(events, key=lambda e: e["detected_ts"])
    scores = [e["anomaly_score"] for e in events_sorted]
    peak = max(scores)
    count = len(events_sorted)
    consistency = _directional_consistency(events_sorted)
    trend = _score_trend(events_sorted)
    cscore = _cluster_score(peak, count, consistency, trend)
    has_block = any((e.get("block_trade_ratio") or 0.0) > 0.0 for e in events_sorted)

    first = events_sorted[0]
    last = events_sorted[-1]

    # Cluster identity is (ticker, first_seen_ts) scoped to the lookback window;
    # stale rows are removed by first_seen_ts prune in run_cluster_scorer.

    return {
        "ticker": ticker,
        "series_ticker": first.get("series_ticker", ""),
        "market_title": first.get("market_title", ""),
        "risk_group": first.get("risk_group", ""),
        "mnpi_actors": first.get("mnpi_actors", ""),
        "first_seen_ts": first["detected_ts"],
        "first_seen_time": first["detected_time"],
        "last_seen_ts": last["detected_ts"],
        "last_seen_time": last["detected_time"],
        "anomaly_count": count,
        "peak_score": round(peak, 2),
        "total_score": round(sum(scores), 2),
        "directional_consistency": round(consistency, 3),
        "score_trend": round(trend, 4),
        "cluster_score": cscore,
        "trigger_types": ",".join(sorted({e.get("trigger_type", "") for e in events_sorted})),
        "has_block_trades": 1 if has_block else 0,
        "computed_time": config.utc_now_iso(),
        "computed_ts": int(time.time()),
    }


def run_cluster_scorer(lookback_days: int = 30) -> int:
    """Find and persist all clusters from the anomaly history.

    Args:
        lookback_days: How far back to look for anomaly history.

    Returns:
        Number of clusters written (upserted).
    """
    log.info("=== Cluster scorer started ===")
    conn = db.get_conn()
    c = conn.cursor()

    cutoff_ts = int(time.time()) - (lookback_days * 86400)
    c.execute(
        """
        SELECT id, ticker, market_title, series_ticker, risk_group, mnpi_actors,
               detected_ts, detected_time, anomaly_score, volume_zscore,
               block_trade_ratio, directional_flag, dominant_side, trigger_type
        FROM anomalies
        WHERE detected_ts >= ?
        ORDER BY ticker, detected_ts ASC
        """,
        (cutoff_ts,),
    )
    all_anomalies = [dict(r) for r in c.fetchall()]
    conn.close()

    # Group by ticker
    by_ticker: dict[str, list[dict]] = {}
    for evt in all_anomalies:
        by_ticker.setdefault(evt["ticker"], []).append(evt)

    records = []
    for ticker, events in by_ticker.items():
        raw_clusters = _group_into_clusters(events)
        for cluster_events in raw_clusters:
            if len(cluster_events) < MIN_CLUSTER_SIZE:
                continue
            record = compute_cluster_record(ticker, cluster_events)
            records.append(record)
            log.info(
                "CLUSTER %s | count=%d | cluster_score=%.1f | "
                "consistency=%.2f | trend=%+.2f | risk=%s",
                ticker,
                record["anomaly_count"],
                record["cluster_score"],
                record["directional_consistency"],
                record["score_trend"],
                record["risk_group"],
            )

    # Bulk upsert all clusters in one transaction
    written = db.upsert_clusters_bulk(records)

    # Drop cluster rows whose first_seen_ts predates lookback (stale identity cleanup).
    stale_cutoff = int(time.time()) - (lookback_days * 86400)
    conn = db.get_conn()
    conn.execute("DELETE FROM clusters WHERE first_seen_ts < ?", (stale_cutoff,))
    conn.commit()
    conn.close()

    log.info("=== Cluster scorer complete: %d clusters written ===", written)
    return written


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    db.init_db()
    run_cluster_scorer()
