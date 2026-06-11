#!/usr/bin/env python3
"""
Replay labeled events against stored trades and correlation decisions.

Usage:
    python backtest.py --days 30
    python backtest.py --compare-matcher-versions
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3

import config
import db

log = logging.getLogger(__name__)

FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__), "tests", "fixtures", "labeled_events.json"
)


def _load_labeled_events() -> list[dict]:
    if not os.path.exists(FIXTURE_PATH):
        return _events_from_config()
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("events", data if isinstance(data, list) else [])


def _events_from_config() -> list[dict]:
    events = []
    for event in config.load_config().get("scheduled_events", {}).get("events", []):
        for date_str in event.get("dates", []):
            events.append({
                "label": event.get("label"),
                "series": event.get("series", []),
                "date": date_str,
            })
    return events


def summarize_score_history(days: int) -> dict:
    conn = db.get_conn()
    cutoff = conn.execute(
        "SELECT strftime('%s', 'now') - ? AS ts",
        (days * 86400,),
    ).fetchone()["ts"]
    rows = conn.execute(
        """
        SELECT formula_version, flagged, COUNT(*) AS n
        FROM score_history
        WHERE run_ts >= ?
        GROUP BY formula_version, flagged
        """,
        (cutoff,),
    ).fetchall()
    conn.close()
    return {"score_history_by_version": [dict(r) for r in rows]}


def compare_matcher_versions() -> dict:
    conn = db.get_conn()
    rows = conn.execute(
        """
        SELECT v1.anomaly_id, v1.news_id, v1.decision AS v1_decision,
               v2.decision AS v2_decision, v1.explanation_json AS v1_json,
               v2.explanation_json AS v2_json
        FROM correlation_decisions v1
        JOIN correlation_decisions v2
          ON v1.anomaly_id = v2.anomaly_id AND v1.news_id = v2.news_id
        WHERE v1.matcher_version = 1 AND v2.matcher_version = 2
          AND v1.decision = 'reject' AND v2.decision = 'accept'
        LIMIT 100
        """
    ).fetchall()
    conn.close()
    return {"flipped_reject_to_accept": len(rows), "samples": [dict(r) for r in rows[:5]]}


def main() -> None:
    parser = argparse.ArgumentParser(description="pmwatch backtest / tuning summary")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--compare-matcher-versions", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    db.init_db()

    report = {
        "trade_retention_days": config.get_trade_retention_days(),
        "labeled_events": len(_load_labeled_events()),
        "score_history": summarize_score_history(args.days),
    }
    if args.compare_matcher_versions:
        report["matcher_diff"] = compare_matcher_versions()

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
