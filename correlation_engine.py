"""
News-to-anomaly correlation: temporal weighting, confidence scoring, and matching.
"""

from __future__ import annotations

import logging
import sqlite3
import time

import db
from keyword_matcher import SERIES_RULES, match_for_correlation
from market_subject import (
    article_mentions_subject,
    fetch_subject_name_from_api,
    resolve_subject_search_terms,
)

log = logging.getLogger(__name__)

SOURCE_WEIGHTS = {
    "disclosure_filing": 2.0,
    "primary_gov": 1.5,
    "mainstream_news": 1.0,
}
DEFAULT_SOURCE_WEIGHT = 1.0

PRE_NEWS_MAX_SECONDS = 48 * 3600
POST_NEWS_MAX_SECONDS = 6 * 3600
TIER1_PRE_NEWS_SECONDS = 2 * 3600
TIER2_PRE_NEWS_SECONDS = 8 * 3600
MIN_CORRELATION_CONFIDENCE = 12.0
MAX_ANOMALY_SCORE_INPUT = 100.0


def get_source_weight(source_type: str) -> float:
    return SOURCE_WEIGHTS.get(source_type, DEFAULT_SOURCE_WEIGHT)


def format_source_weight(source_type: str) -> str:
    weight = get_source_weight(source_type)
    if weight == int(weight):
        return f"{int(weight)}x"
    return f"{weight}x"


def temporal_multiplier(time_diff: int) -> float | None:
    """Return temporal weight for correlation confidence, or None if outside window.

    Pre-news (anomaly before public drop):
      0–2h: 1.8x | 2–8h: 1.3x | 8–48h: linear 1.0→0.3
    Post-news reaction (news before anomaly):
      0–6h: 0.7x | beyond 6h: excluded
    """
    if time_diff > PRE_NEWS_MAX_SECONDS:
        return None
    if time_diff < -POST_NEWS_MAX_SECONDS:
        return None
    if time_diff < 0:
        return 0.7
    if time_diff <= TIER1_PRE_NEWS_SECONDS:
        return 1.8
    if time_diff <= TIER2_PRE_NEWS_SECONDS:
        return 1.3
    span = PRE_NEWS_MAX_SECONDS - TIER2_PRE_NEWS_SECONDS
    elapsed = time_diff - TIER2_PRE_NEWS_SECONDS
    return 1.0 - (elapsed / span) * 0.7


def calculate_correlation_confidence(
    anomaly: dict,
    news_article: dict,
    time_diff: int,
    match_quality: float,
) -> float:
    """Calculate confidence from capped anomaly score, source weight, match quality, and temporal band."""
    temporal = temporal_multiplier(time_diff)
    if temporal is None:
        return 0.0

    score = min(MAX_ANOMALY_SCORE_INPUT, float(anomaly["anomaly_score"]))
    source_type = news_article.get("source_type", "mainstream_news")
    weight = get_source_weight(source_type)

    return round(score * weight * match_quality * temporal, 2)


def correlate_all_recent_anomalies(lookback_days: int = 7) -> int:
    """Scan recent anomalies and match them to ingested news articles."""
    conn = db.get_conn()
    c = conn.cursor()
    cutoff_ts = int(time.time()) - (lookback_days * 86400)

    c.execute(
        """
        SELECT * FROM anomalies
        WHERE detected_ts >= ?
        ORDER BY detected_ts ASC
        """,
        (cutoff_ts,),
    )
    anomalies = [dict(r) for r in c.fetchall()]

    c.execute(
        """
        SELECT * FROM news_articles
        WHERE published_ts >= ?
        ORDER BY published_ts ASC
        """,
        (cutoff_ts - 86400,),
    )
    news_articles = [dict(r) for r in c.fetchall()]
    conn.close()

    market_meta = db.get_market_subject_metadata(
        sorted({a["ticker"] for a in anomalies if a.get("ticker")})
    )
    subject_terms_cache: dict[str, list[str] | None] = {}

    correlations_inserted = 0
    for anomaly in anomalies:
        series_ticker = anomaly["series_ticker"]
        if not series_ticker:
            continue

        if series_ticker not in SERIES_RULES:
            continue

        ticker = anomaly["ticker"]
        if ticker not in subject_terms_cache:
            meta = market_meta.get(ticker, {})
            subject_terms_cache[ticker] = resolve_subject_search_terms(
                ticker,
                series_ticker,
                market_title=anomaly.get("market_title") or meta.get("title", ""),
                subject_name=anomaly.get("subject_name") or meta.get("subject_name", ""),
                rules_primary=meta.get("rules_primary", ""),
            )
            if subject_terms_cache[ticker] == []:
                api_subject = fetch_subject_name_from_api(ticker)
                if api_subject:
                    db.update_market_subject_metadata(ticker, subject_name=api_subject)
                    subject_terms_cache[ticker] = resolve_subject_search_terms(
                        ticker,
                        series_ticker,
                        market_title=anomaly.get("market_title") or meta.get("title", ""),
                        subject_name=api_subject,
                        rules_primary=meta.get("rules_primary", ""),
                    )

        subject_terms = subject_terms_cache[ticker]

        for article in news_articles:
            time_diff = article["published_ts"] - anomaly["detected_ts"]
            if temporal_multiplier(time_diff) is None:
                continue

            result = match_for_correlation(
                series_ticker,
                article["title"],
                article.get("description") or "",
            )
            if not result or result.negated or result.quality <= 0:
                continue

            article_text = article["title"] + " " + (article.get("description") or "")
            if subject_terms is not None and not article_mentions_subject(
                article_text, subject_terms
            ):
                continue

            confidence = calculate_correlation_confidence(
                anomaly,
                article,
                time_diff,
                result.quality,
            )
            if confidence < MIN_CORRELATION_CONFIDENCE:
                continue

            matched_terms = ", ".join(result.all_matched_terms)
            source_weight = format_source_weight(article["source_type"])
            direction = "pre_news" if time_diff >= 0 else "reaction"
            temporal = temporal_multiplier(time_diff)
            subject_note = ""
            if subject_terms:
                subject_note = f" | Subject: {', '.join(subject_terms[:2])}"
            correlation = {
                "anomaly_id": anomaly["id"],
                "cluster_first_seen_ts": anomaly["detected_ts"],
                "ticker": anomaly["ticker"],
                "news_id": article["id"],
                "lead_time_seconds": time_diff,
                "confidence_score": confidence,
                "notes": (
                    f"Matched keywords: {matched_terms} | "
                    f"Match quality: {result.quality:.2f} | "
                    f"Temporal: {temporal:.2f}x ({direction}) | "
                    f"Source weight: {source_weight}{subject_note}"
                ),
            }
            try:
                db.insert_correlation(correlation)
                correlations_inserted += 1
            except sqlite3.IntegrityError:
                pass
            except Exception as e:
                log.warning(
                    "Failed to insert correlation for %s / news %s: %s",
                    anomaly["ticker"],
                    article["id"],
                    e,
                )

    if correlations_inserted > 0:
        log.info("Created %d news-to-anomaly correlations.", correlations_inserted)

    return correlations_inserted


def rebuild_correlations(
    lookback_days: int = 30,
    cap_scores: bool = True,
) -> dict:
    """Clear stale correlations and re-match anomalies against ingested news.

    Use after upgrading matcher/scorer logic to refresh the dashboard without
    re-fetching feeds or re-collecting trades.
    """
    removed = db.clear_correlations()
    capped = db.cap_anomaly_scores(MAX_ANOMALY_SCORE_INPUT) if cap_scores else 0
    inserted = correlate_all_recent_anomalies(lookback_days=lookback_days)
    summary = {
        "removed": removed,
        "capped_anomalies": capped,
        "inserted": inserted,
        "lookback_days": lookback_days,
    }
    log.info(
        "Correlation rebuild complete: removed=%d capped=%d inserted=%d lookback=%dd",
        removed,
        capped,
        inserted,
        lookback_days,
    )
    return summary


if __name__ == "__main__":
    import argparse
    import logging as _logging

    parser = argparse.ArgumentParser(
        description="Rebuild news-to-anomaly correlations from existing DB data."
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=30,
        help="How far back to scan anomalies and news (default: 30)",
    )
    parser.add_argument(
        "--no-cap-scores",
        action="store_true",
        help="Skip capping historical anomaly_score values at 100",
    )
    args = parser.parse_args()

    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    db.init_db()
    result = rebuild_correlations(
        lookback_days=args.lookback_days,
        cap_scores=not args.no_cap_scores,
    )
    print(result)
