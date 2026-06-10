"""
News-to-anomaly correlation: temporal weighting, confidence scoring, and matching.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import config
import db
from expected_events import adjust_temporal_for_expected_event
from keyword_matcher import (
    SERIES_RULES,
    explain_for_correlation,
    series_allowed_for_source,
)
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
MIN_CORRELATION_CONFIDENCE = config.DEFAULT_CORRELATION_THRESHOLDS["min_confidence"]
MAX_ANOMALY_SCORE_INPUT = 100.0
# When top-two article confidences differ by less than this fraction, keep only the best.
CORRELATION_AMBIGUITY_REL_GAP = 0.15


@dataclass
class CorrelationExplanation:
    """Structured record for a single anomaly↔article correlation decision."""

    decision: Literal["accept", "reject"]
    match: dict[str, Any]
    temporal_direction: Literal["pre_news", "reaction", "excluded"]
    temporal_multiplier: float | None = None
    confidence_score: float = 0.0
    score_type: Literal["leakage", "reaction"] = "leakage"
    confidence_components: dict[str, float] = field(default_factory=dict)
    sub_scores: dict[str, float] = field(default_factory=dict)
    expected_event: dict[str, Any] | None = None
    subject_terms: list[str] | None = None
    subject_gate_passed: bool | None = None
    reject_reason: str | None = None
    final_rationale: str = ""
    competing_candidates: int = 0
    ambiguous_runner_up_news_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def get_source_weight(source_type: str) -> float:
    return SOURCE_WEIGHTS.get(source_type, DEFAULT_SOURCE_WEIGHT)


def format_source_weight(source_type: str) -> str:
    weight = get_source_weight(source_type)
    if weight == int(weight):
        return f"{int(weight)}x"
    return f"{weight}x"


def temporal_direction(time_diff: int) -> Literal["pre_news", "reaction", "excluded"]:
    if temporal_multiplier(time_diff) is None:
        return "excluded"
    return "pre_news" if time_diff >= 0 else "reaction"


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


def _confidence_breakdown(
    anomaly: dict,
    news_article: dict,
    time_diff: int,
    match_quality: float,
    series_ticker: str | None = None,
) -> tuple[float, dict[str, float], dict[str, float], str, dict[str, Any] | None]:
    base_temporal = temporal_multiplier(time_diff)
    if base_temporal is None:
        return 0.0, {}, {}, "leakage", None

    score_type: Literal["leakage", "reaction"] = (
        "reaction" if time_diff < 0 else "leakage"
    )
    temporal = base_temporal
    expected_event_meta: dict[str, Any] | None = None

    if series_ticker and time_diff >= 0:
        temporal, event_adjust = adjust_temporal_for_expected_event(
            series_ticker,
            time_diff,
            int(anomaly["detected_ts"]),
            int(news_article["published_ts"]),
            base_temporal,
        )
        if event_adjust:
            expected_event_meta = dict(event_adjust)

    score = min(MAX_ANOMALY_SCORE_INPUT, float(anomaly["anomaly_score"]))
    source_type = news_article.get("source_type", "mainstream_news")
    weight = get_source_weight(source_type)
    confidence = round(score * weight * match_quality * temporal, 2)

    sub_scores = {
        "market_microstructure_score": round(score, 2),
        "correlation_relevance_score": round(match_quality, 3),
        "source_credibility_score": round(weight, 3),
        "leakage_plausibility_score": round(temporal, 3),
    }
    components = {
        "anomaly_score": round(score, 2),
        "source_weight": weight,
        "match_quality": round(match_quality, 3),
        "base_temporal_multiplier": round(base_temporal, 3),
        "temporal_multiplier": round(temporal, 3),
        "score_type": score_type,
    }
    return confidence, components, sub_scores, score_type, expected_event_meta


def calculate_correlation_confidence(
    anomaly: dict,
    news_article: dict,
    time_diff: int,
    match_quality: float,
) -> float:
    """Calculate confidence from capped anomaly score, source weight, match quality, and temporal band."""
    confidence, _, _, _, _ = _confidence_breakdown(
        anomaly, news_article, time_diff, match_quality
    )
    return confidence


def _format_correlation_notes(
    explanation: CorrelationExplanation,
    match_terms: list[str],
    source_type: str,
) -> str:
    direction = explanation.temporal_direction
    temporal = explanation.temporal_multiplier or 0.0
    source_weight = format_source_weight(source_type)
    terms = ", ".join(match_terms)
    subject_note = ""
    if explanation.subject_terms:
        subject_note = f" | Subject: {', '.join(explanation.subject_terms[:2])}"
    event_note = ""
    if explanation.expected_event:
        event_note = (
            f" | Event window: {explanation.expected_event.get('expected_event', '')}"
        )
    score_label = "Leakage" if explanation.score_type == "leakage" else "Reaction"
    subs = explanation.sub_scores
    sub_note = ""
    if subs:
        sub_note = (
            f" | Micro={subs.get('market_microstructure_score', 0):.0f}"
            f" Rel={subs.get('correlation_relevance_score', 0):.2f}"
            f" Src={subs.get('source_credibility_score', 0):.1f}"
            f" Plaus={subs.get('leakage_plausibility_score', 0):.2f}"
        )
    return (
        f"[{score_label}] Matched keywords: {terms} | "
        f"Match quality: {explanation.match.get('quality', 0):.2f} | "
        f"Temporal: {temporal:.2f}x ({direction}) | "
        f"Source weight: {source_weight}{subject_note}{event_note}{sub_note} | "
        f"{explanation.final_rationale}"
    )


def evaluate_correlation_pair(
    anomaly: dict,
    article: dict,
    subject_terms: list[str] | None,
    series_ticker: str,
) -> CorrelationExplanation:
    """Evaluate one anomaly↔article pair and return a structured explanation."""
    time_diff = article["published_ts"] - anomaly["detected_ts"]
    direction = temporal_direction(time_diff)
    temporal = temporal_multiplier(time_diff)
    article_source = article.get("source")

    if temporal is None:
        return CorrelationExplanation(
            decision="reject",
            match={"series": series_ticker},
            temporal_direction="excluded",
            temporal_multiplier=None,
            subject_terms=subject_terms,
            reject_reason="outside_temporal_window",
            final_rationale="Lead time outside correlation window.",
        )

    if not series_allowed_for_source(series_ticker, article_source):
        match_exp = explain_for_correlation(
            series_ticker,
            article["title"],
            article.get("description") or "",
            source=article_source,
        )
        return CorrelationExplanation(
            decision="reject",
            match=match_exp.to_dict(),
            temporal_direction=direction,
            temporal_multiplier=temporal,
            subject_terms=subject_terms,
            reject_reason="source_scope",
            final_rationale=(
                f"Feed {article_source!r} is not scoped to series {series_ticker}."
            ),
        )

    match_exp = explain_for_correlation(
        series_ticker,
        article["title"],
        article.get("description") or "",
        source=article_source,
    )

    if match_exp.decision != "accept":
        return CorrelationExplanation(
            decision="reject",
            match=match_exp.to_dict(),
            temporal_direction=direction,
            temporal_multiplier=temporal,
            subject_terms=subject_terms,
            reject_reason=match_exp.reject_reason or "match_rejected",
            final_rationale=match_exp.rationale,
        )

    min_match_quality = config.get_min_correlation_match_quality()
    if match_exp.quality < min_match_quality:
        return CorrelationExplanation(
            decision="reject",
            match=match_exp.to_dict(),
            temporal_direction=direction,
            temporal_multiplier=temporal,
            subject_terms=subject_terms,
            reject_reason="below_match_quality_floor",
            final_rationale=(
                f"Match quality {match_exp.quality:.3f} below floor "
                f"{min_match_quality}."
            ),
        )

    article_text = article["title"] + " " + (article.get("description") or "")
    subject_gate_passed: bool | None = None
    if subject_terms is not None:
        subject_gate_passed = article_mentions_subject(article_text, subject_terms)
        if not subject_gate_passed:
            return CorrelationExplanation(
                decision="reject",
                match=match_exp.to_dict(),
                temporal_direction=direction,
                temporal_multiplier=temporal,
                subject_terms=subject_terms,
                subject_gate_passed=False,
                reject_reason="subject_gate",
                final_rationale=(
                    f"Article does not mention subject terms: "
                    f"{', '.join(subject_terms[:3])}."
                ),
            )

    confidence, components, sub_scores, score_type, expected_event = _confidence_breakdown(
        anomaly,
        article,
        time_diff,
        match_exp.quality,
        series_ticker=series_ticker,
    )
    effective_temporal = components.get("temporal_multiplier", temporal)
    min_confidence = config.get_min_correlation_confidence()
    if confidence < min_confidence:
        return CorrelationExplanation(
            decision="reject",
            match=match_exp.to_dict(),
            temporal_direction=direction,
            temporal_multiplier=effective_temporal,
            confidence_score=confidence,
            score_type=score_type,
            confidence_components=components,
            sub_scores=sub_scores,
            expected_event=expected_event,
            subject_terms=subject_terms,
            subject_gate_passed=subject_gate_passed,
            reject_reason="below_min_confidence",
            final_rationale=(
                f"Confidence {confidence:.2f} below threshold "
                f"{min_confidence}."
            ),
        )

    rationale = match_exp.rationale
    if expected_event:
        rationale += (
            f" Expected event window ({expected_event.get('expected_event', '')}) "
            f"raised temporal floor to {expected_event.get('temporal_floor_applied', '')}."
        )

    return CorrelationExplanation(
        decision="accept",
        match=match_exp.to_dict(),
        temporal_direction=direction,
        temporal_multiplier=effective_temporal,
        confidence_score=confidence,
        score_type=score_type,
        confidence_components=components,
        sub_scores=sub_scores,
        expected_event=expected_event,
        subject_terms=subject_terms,
        subject_gate_passed=subject_gate_passed,
        final_rationale=rationale,
    )


def _log_rejection(
    anomaly: dict,
    article: dict,
    explanation: CorrelationExplanation,
) -> None:
    log.debug(
        "correlation_reject ticker=%s news_id=%s reason=%s rationale=%s",
        anomaly.get("ticker"),
        article.get("id"),
        explanation.reject_reason,
        explanation.final_rationale,
    )


def _select_correlations_for_anomaly(
    candidates: list[tuple[dict, CorrelationExplanation]],
) -> list[tuple[dict, CorrelationExplanation]]:
    """When confidences cluster, keep only the top article match."""
    if not candidates:
        return []
    if len(candidates) == 1:
        return candidates

    ranked = sorted(
        candidates,
        key=lambda item: item[1].confidence_score,
        reverse=True,
    )
    best_article, best_exp = ranked[0]
    second_article, second_exp = ranked[1]
    rel_gap = 0.0
    if best_exp.confidence_score > 0:
        rel_gap = (
            (best_exp.confidence_score - second_exp.confidence_score)
            / best_exp.confidence_score
        )

    if rel_gap < CORRELATION_AMBIGUITY_REL_GAP:
        best_exp.competing_candidates = len(candidates)
        best_exp.ambiguous_runner_up_news_id = second_article.get("id")
        best_exp.final_rationale += (
            f" Ambiguous cluster ({len(candidates)} candidates); "
            f"kept top confidence {best_exp.confidence_score:.2f}."
        )
        log.debug(
            "Ambiguous correlation cluster for %s: kept news_id=%s (%.2f) over news_id=%s (%.2f)",
            best_article.get("ticker"),
            best_article.get("id"),
            best_exp.confidence_score,
            second_article.get("id"),
            second_exp.confidence_score,
        )
        return [(best_article, best_exp)]

    for _, exp in ranked:
        exp.competing_candidates = len(candidates)
    return ranked


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
        accepted: list[tuple[dict, CorrelationExplanation]] = []

        for article in news_articles:
            explanation = evaluate_correlation_pair(
                anomaly,
                article,
                subject_terms,
                series_ticker,
            )
            if explanation.decision != "accept":
                _log_rejection(anomaly, article, explanation)
                continue
            accepted.append((article, explanation))

        for article, explanation in _select_correlations_for_anomaly(accepted):
            match_data = explanation.match
            matched_terms = (
                match_data.get("matched_anchors", [])
                + match_data.get("matched_signals", [])
                + match_data.get("matched_required", [])
                + match_data.get("matched_context", [])
            )
            time_diff = article["published_ts"] - anomaly["detected_ts"]
            correlation = {
                "anomaly_id": anomaly["id"],
                "cluster_first_seen_ts": anomaly["detected_ts"],
                "ticker": anomaly["ticker"],
                "news_id": article["id"],
                "lead_time_seconds": time_diff,
                "confidence_score": explanation.confidence_score,
                "notes": _format_correlation_notes(
                    explanation,
                    matched_terms,
                    article.get("source_type", "mainstream_news"),
                ),
                "explanation_json": json.dumps(explanation.to_dict()),
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
