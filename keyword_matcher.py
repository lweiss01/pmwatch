"""
Keyword matching for news-to-series correlation.

Uses anchor+signal co-occurrence, phrase-guarded required terms, blocklists,
topic-family suppression, candidate scoring, and negation detection.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import config

log = logging.getLogger(__name__)

# Default floors (runtime reads config.get_* helpers).
MIN_INGEST_QUALITY = config.DEFAULT_MATCHER_THRESHOLDS["min_ingest_quality"]
MIN_CORRELATION_MATCH_QUALITY = config.DEFAULT_CORRELATION_THRESHOLDS["min_match_quality"]
# When top-two candidate qualities differ by less than this, keep the best (ambiguous cluster).
AMBIGUITY_QUALITY_GAP = 0.15
# Bump when matcher logic changes materially (quality saturation = 2 in Phase 2).
MATCHER_VERSION = 3

# Short tokens that need word-boundary matching to avoid substring false positives.
SHORT_BOUNDARY_TERMS = frozenset({
    "dni", "fed", "cr", "eo", "bls", "gdp", "cpi", "doj", "doge", "ag", "scotus",
})

NEGATION_WORDS = frozenset({
    "no", "not", "denied", "rejected", "failed", "won't", "wont", "unlikely",
    "without", "never", "refuse", "refused", "denies", "deny", "neither", "nor",
})

# None = evaluate all series; [] = no series (disclosure/metadata feeds).
FEED_TOPIC_SCOPE: dict[str, list[str] | None] = {
    "Federal Reserve Press": ["KXFED"],
    "Congress.gov House Floor": [
        "KXHOUSE", "KXVETOOVERRIDE", "KXGOVSHUT", "KXCR", "KXIMPEACH",
        "KXSCOTUSRESIGN", "KXSCOURT", "KXTARIFFS",
    ],
    "Congress.gov Senate Floor": [
        "KXSENATE", "KXVETOOVERRIDE", "KXGOVSHUT", "KXCR", "KXIMPEACH",
        "KXNEXTAG", "KXNEXTDEF", "KXNEXTSTATE", "KXNEXTODNI",
    ],
    "Congress.gov Presented to President": [
        "KXEOTRUMPTERM", "KXVETOOVERRIDE", "KXCABOUT", "KXTRUMPPARDONS",
    ],
    "TreasuryDirect Offerings": [],
    "Federal Register": None,
    "Federal Register Executive Orders": ["KXEOTRUMPTERM"],
    "White House Briefings": [
        "KXCABOUT", "KXNEXTAG", "KXNEXTDEF", "KXNEXTSTATE", "KXNEXTODNI",
        "KXTRUMPPARDONS", "KXTRUMPPARDONFAMILY", "KXINSURRECTION", "KXMARTIAL",
        "KXEOTRUMPTERM", "KXAMEND25", "KXTRUMPRESIGN", "KXIMPEACH",
        "KXHABEAS", "KXDOED", "KXAGENCYELIM",
    ],
    "SEC EDGAR Form 4": [],
    "House Financial Disclosures": [],
    "Senate eFD Disclosures": [],
    "NYT Politics": None,
    "Politico Playbook": None,
}

# Topic families add defense beyond per-series blocklists (e.g. Fed supervision vs rate policy).
SERIES_TOPIC_FAMILIES: dict[str, str] = {
    "KXFED": "monetary_policy",
}

TOPIC_FAMILY_BLOCKLIST: dict[str, list[str]] = {
    "monetary_policy": [
        "stress test",
        "bank stress",
        "ccar",
        "capital requirements",
        "enforcement action",
        "cease and desist",
        "bank holding",
        "merger approval",
        "payment system",
        "supervision",
        "bank examination",
        "regulation z",
        "discount window",
    ],
}


@dataclass
class SeriesRule:
    anchors: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    required: list[str] = field(default_factory=list)
    context: list[str] = field(default_factory=list)
    blocklist: list[str] = field(default_factory=list)
    require_both: bool = False
    min_signal_hits: int = 1

    def all_match_terms(self) -> list[str]:
        return _sort_terms_longest_first(
            self.anchors + self.signals + self.required + self.context
        )


def _sort_terms_longest_first(terms: list[str]) -> list[str]:
    return sorted(terms, key=lambda t: len(t), reverse=True)


def _normalize_rule(raw: dict[str, Any] | list[str]) -> SeriesRule:
    if isinstance(raw, list):
        return SeriesRule(signals=list(raw), require_both=False)
    return SeriesRule(
        anchors=list(raw.get("anchors", [])),
        signals=list(raw.get("signals", [])),
        required=list(raw.get("required", [])),
        context=list(raw.get("context", [])),
        blocklist=list(raw.get("blocklist", [])),
        require_both=bool(raw.get("require_both", False)),
        min_signal_hits=int(raw.get("min_signal_hits", 1)),
    )


SERIES_RULES: dict[str, SeriesRule] = {
    series: _normalize_rule(raw)
    for series, raw in {
        "KXCABOUT": {
            "anchors": ["cabinet", "department head", "white house"],
            "signals": ["resign", "resignation", "fired", "leaves office", "vacate", "departure"],
            "require_both": True,
        },
        "KXNEXTAG": {
            "anchors": ["attorney general", "justice department"],
            "signals": ["blanche", "gaetz", "nominee", "nomination", "nominate", "nominates", "nominated"],
            "require_both": True,
        },
        "KXNEXTDEF": {
            "anchors": ["secretary of defense", "pentagon", "defense department"],
            "signals": ["secdef", "hegseth", "nominee", "nomination", "nominate"],
            "require_both": True,
        },
        "KXNEXTSTATE": {
            "anchors": ["secretary of state", "state department"],
            "signals": ["secstate", "rubio", "nominee", "nomination", "nominate"],
            "require_both": True,
        },
        "KXNEXTODNI": {
            "anchors": [
                "director of national intelligence",
                "national intelligence",
                "intelligence community",
            ],
            "signals": ["dni", "gabbard", "nominee", "nomination", "nominate"],
            "require_both": True,
        },
        "KXTRUMPPARDONS": {
            "signals": ["pardon", "clemency", "commute sentence", "commuted"],
            "blocklist": ["beg your pardon", "i beg your pardon"],
            "require_both": False,
        },
        "KXTRUMPPARDONFAMILY": {
            "required": ["family pardon", "pardon family", "pardons family members"],
            "require_both": False,
        },
        "KXINSURRECTION": {
            "required": ["insurrection act", "military deployment", "domestic deployment"],
            "require_both": False,
        },
        "KXMARTIAL": {
            "required": ["martial law", "military rule"],
            "require_both": False,
        },
        "KXHABEAS": {
            "required": ["habeas corpus", "detention without trial"],
            "require_both": False,
        },
        "KXWITHDRAW": {
            "required": ["withdraw from treaty", "nato withdrawal", "treaty exit"],
            "require_both": False,
        },
        "KXDOED": {
            "anchors": ["education department", "department of education"],
            "signals": ["doed", "abolish education", "abolish the department"],
            "require_both": False,
        },
        "KXAGENCYELIM": {
            "anchors": ["agency elimination", "eliminate agency", "department of government efficiency"],
            "signals": ["doge"],
            "require_both": False,
        },
        "KXEOTRUMPTERM": {
            "required": ["executive order"],
            "signals": ["eo count", "number of executive orders"],
            "require_both": False,
        },
        "KXAMEND25": {
            "required": ["25th amendment", "twenty-fifth amendment", "25th amendment"],
            "require_both": False,
        },
        "KXTRUMPRESIGN": {
            "required": ["trump resign", "president resignation", "trump resignation"],
            "require_both": False,
        },
        "KXFED": {
            "anchors": ["federal reserve", "fomc", "federal open market committee"],
            "signals": [
                "fed funds rate", "interest rate", "rate cut", "rate hike",
                "rate decision", "basis points", "bps", "target range",
                "monetary policy", "rate hold", "rate pause", "dot plot",
                "policy rate", "tightening", "easing", "accommodation",
                "chair powell", "jerome powell", "fed chair",
            ],
            "require_both": True,
            "blocklist": [
                "stress test", "bank stress", "ccar", "capital requirements",
                "enforcement action", "cease and desist", "bank holding",
                "merger approval", "payment system", "supervision",
                "bank examination", "regulation z", "discount window",
                "colin powell",
            ],
        },
        "KXCPI": {
            "anchors": ["bls", "bureau of labor statistics"],
            "signals": ["cpi", "consumer price index", "inflation", "consumer prices"],
            "require_both": True,
            "blocklist": ["cpi security", "cyber cpi"],
        },
        "KXGDP": {
            "anchors": ["bea", "bureau of economic analysis"],
            "signals": ["gdp", "gross domestic product", "economic growth"],
            "require_both": True,
            "blocklist": ["historical gdp", "revised prior quarter"],
        },
        "KXGREENTERRITORY": {
            "anchors": ["greenland"],
            "signals": ["acquisition", "territory", "purchase", "annex"],
            "require_both": True,
        },
        "KXCANTERRITORY": {
            "anchors": ["canada"],
            "signals": ["annex", "territory", "acquisition", "51st state"],
            "require_both": True,
        },
        "KXCANAL": {
            "required": ["panama canal", "canal control"],
            "require_both": False,
        },
        "KXRECOGROC": {
            "required": ["recognize taiwan", "taiwan recognition", "republic of china"],
            "require_both": False,
        },
        "KXZELENSKYPUTIN": {
            "anchors": ["zelensky", "putin"],
            "signals": ["talks", "peace negotiation", "peace talks", "summit"],
            "require_both": True,
        },
        "KXUSAKIM": {
            "required": ["kim jong-un", "north korea visit", "kim jong un"],
            "require_both": False,
        },
        "KXABRAHAMSA": {
            "required": ["israel-saudi", "abraham accords", "saudi normalization"],
            "require_both": False,
        },
        "KXABRAHAMSY": {
            "required": ["israel-syria", "peace treaty", "syria normalization"],
            "require_both": False,
        },
        "KXHOUSE": {
            "required": ["house control", "house speaker", "house majority"],
            "require_both": False,
        },
        "KXSENATE": {
            "required": ["senate control", "senate majority", "senate leader"],
            "require_both": False,
        },
        "KXIMPEACH": {
            "signals": ["impeachment", "impeach"],
            "blocklist": ["acquitted", "trial ended", "historical impeachment"],
            "require_both": False,
        },
        "KXVETOOVERRIDE": {
            "required": ["veto override", "override veto", "overriding the veto"],
            "blocklist": ["override failed", "failed to override", "failed override"],
            "require_both": False,
        },
        "KXGOVSHUT": {
            "required": [
                "government shutdown", "federal shutdown", "shutdown funding",
                "partial shutdown", "funding lapse", "appropriations lapse",
            ],
            "blocklist": [
                "plant shutdown", "factory shutdown", "shutdown of talks",
                "school shutdown", "weather shutdown", "port shutdown",
                "shutdown order for", "investigation shutdown",
            ],
            "require_both": False,
        },
        "KXCR": {
            "required": ["continuing resolution", "stopgap funding", "cr budget"],
            "require_both": False,
        },
        "KXSCOTUSRESIGN": {
            "anchors": ["supreme court", "scotus", "high court"],
            "signals": [
                "resign", "retirement", "retire", "vacancy", "step down",
                "stepping down", "health reasons", "alito", "thomas", "sotomayor",
            ],
            "require_both": True,
            "blocklist": [
                "ruling", "decision", "opinion", "oral argument", "certiorari",
                "cert granted", "dissent", "affirmed", "reversed", "hearing on the merits",
            ],
        },
        "KXSCOTUSCHANGE": {
            "required": ["court size", "court packing", "expand the court", "pack the court"],
            "require_both": False,
        },
        "KXSCOURT": {
            "anchors": ["supreme court", "scotus", "high court"],
            "signals": ["next justice", "court nominee", "nominee", "nomination"],
            "require_both": True,
            "blocklist": [
                "ruling", "decision", "opinion", "oral argument", "certiorari",
                "resign", "retirement", "retire", "vacancy",
            ],
        },
        "KXTARIFFS": {
            "anchors": ["supreme court", "scotus", "high court"],
            "signals": ["tariff", "tariffs", "tariffs case", "court tariffs"],
            "require_both": True,
        },
        "KXSCOTUSPOWER": {
            "required": ["court composition", "judicial reform", "reform the judiciary"],
            "require_both": False,
        },
    }.items()
}

# Preserve deterministic first-match priority (same order as legacy SERIES_KEYWORDS).
SERIES_MATCH_ORDER: list[str] = list(SERIES_RULES.keys())


@dataclass
class MatchResult:
    series: str
    matched_anchors: list[str]
    matched_signals: list[str]
    matched_required: list[str]
    matched_context: list[str]
    negated: bool
    quality: float
    reject_reason: str | None = None
    blocklist_hits: list[str] = field(default_factory=list)
    topic_family_hits: list[str] = field(default_factory=list)
    quality_components: dict[str, float] = field(default_factory=dict)

    @property
    def all_matched_terms(self) -> list[str]:
        return (
            self.matched_anchors
            + self.matched_signals
            + self.matched_required
            + self.matched_context
        )


@dataclass
class MatchExplanation:
    """Structured accept/reject record for matcher and correlation forensics."""

    series: str
    decision: Literal["accept", "reject"]
    matched_anchors: list[str] = field(default_factory=list)
    matched_signals: list[str] = field(default_factory=list)
    matched_required: list[str] = field(default_factory=list)
    matched_context: list[str] = field(default_factory=list)
    blocklist_hits: list[str] = field(default_factory=list)
    topic_family_hits: list[str] = field(default_factory=list)
    negated_terms: list[str] = field(default_factory=list)
    negation_checked: bool = False
    source_scope_ok: bool | None = None
    quality: float = 0.0
    quality_components: dict[str, float] = field(default_factory=dict)
    reject_reason: str | None = None
    rationale: str = ""
    ambiguous_runner_up: str | None = None
    ambiguous_quality_gap: float | None = None

    def to_result(self) -> MatchResult | None:
        if self.decision != "accept":
            return None
        return MatchResult(
            series=self.series,
            matched_anchors=self.matched_anchors,
            matched_signals=self.matched_signals,
            matched_required=self.matched_required,
            matched_context=self.matched_context,
            negated=bool(self.negated_terms),
            quality=self.quality,
            reject_reason=None,
            blocklist_hits=self.blocklist_hits,
            topic_family_hits=self.topic_family_hits,
            quality_components=dict(self.quality_components),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _needs_boundary(term: str) -> bool:
    term_lower = term.lower()
    return " " not in term_lower and (
        len(term_lower) <= 4 or term_lower in SHORT_BOUNDARY_TERMS
    )


def find_term_positions(term: str, text: str) -> list[int]:
    """Return start indices of non-overlapping term occurrences in lowercased text."""
    term_lower = term.lower()
    text_lower = text.lower()
    positions: list[int] = []

    if not _needs_boundary(term_lower):
        start = 0
        while True:
            idx = text_lower.find(term_lower, start)
            if idx == -1:
                break
            positions.append(idx)
            start = idx + max(1, len(term_lower))
    else:
        pattern = re.compile(r"\b" + re.escape(term_lower) + r"\b")
        positions = [m.start() for m in pattern.finditer(text_lower)]

    return positions


def term_in_text(term: str, text: str) -> bool:
    return bool(find_term_positions(term, text))


def is_negated(text: str, pos: int, term: str) -> bool:
    """True if a negation word appears within 5 tokens before or after the match."""
    before = text[:pos].lower()
    before_tokens = re.findall(r"\b[\w']+\b", before)
    if any(token in NEGATION_WORDS for token in before_tokens[-5:]):
        return True

    match_end = pos + len(term)
    after = text[match_end:].lower()
    after_tokens = re.findall(r"\b[\w']+\b", after)
    return any(token in NEGATION_WORDS for token in after_tokens[:5])


def _active_matches(terms: list[str], text: str) -> list[str]:
    """Return terms that match and are not entirely negated at every occurrence."""
    matched: list[str] = []
    for term in _sort_terms_longest_first(terms):
        positions = find_term_positions(term, text)
        if not positions:
            continue
        if any(not is_negated(text, pos, term) for pos in positions):
            matched.append(term)
    return matched


def _blocklist_hits(terms: list[str], text: str) -> list[str]:
    return [
        term
        for term in _sort_terms_longest_first(terms)
        if term_in_text(term, text)
    ]


def _negated_terms(terms: list[str], text: str) -> list[str]:
    negated: list[str] = []
    for term in _sort_terms_longest_first(terms):
        positions = find_term_positions(term, text)
        if positions and all(is_negated(text, pos, term) for pos in positions):
            negated.append(term)
    return negated


def _compute_quality(
    matched_anchors: list[str],
    matched_signals: list[str],
    matched_required: list[str],
    matched_context: list[str],
    rule: SeriesRule,
) -> tuple[float, dict[str, float]]:
    if rule.required and not rule.anchors and not rule.signals:
        req_score = min(1.0, len(matched_required) / max(1, len(rule.required)))
        components = {"required_score": round(req_score, 3)}
        return round(max(0.35, req_score), 3), components

    if rule.require_both and rule.anchors and rule.signals:
        # Saturation: two strong hits reach full score regardless of list length.
        anchor_score = min(1.0, len(matched_anchors) / 2)
        signal_score = min(1.0, len(matched_signals) / 2)
        components = {
            "anchor_score": round(anchor_score, 3),
            "signal_score": round(signal_score, 3),
            "saturation_divisor": 2,
        }
        return round(0.35 * anchor_score + 0.65 * signal_score, 3), components

    total_terms = rule.anchors + rule.signals + rule.required + rule.context
    matched_count = (
        len(matched_anchors)
        + len(matched_signals)
        + len(matched_required)
        + len(matched_context)
    )
    coverage = matched_count / max(1, len(total_terms))
    components = {"coverage_score": round(min(1.0, coverage), 3)}
    return round(max(0.25, min(1.0, coverage)), 3), components


def _reject_explanation(
    series: str,
    reason: str,
    rationale: str,
    *,
    blocklist_hits: list[str] | None = None,
    topic_family_hits: list[str] | None = None,
    source_scope_ok: bool | None = None,
    matched_anchors: list[str] | None = None,
    matched_signals: list[str] | None = None,
    matched_required: list[str] | None = None,
    matched_context: list[str] | None = None,
    negated_terms: list[str] | None = None,
    negation_checked: bool = False,
    quality: float = 0.0,
    quality_components: dict[str, float] | None = None,
) -> MatchExplanation:
    return MatchExplanation(
        series=series,
        decision="reject",
        matched_anchors=matched_anchors or [],
        matched_signals=matched_signals or [],
        matched_required=matched_required or [],
        matched_context=matched_context or [],
        blocklist_hits=blocklist_hits or [],
        topic_family_hits=topic_family_hits or [],
        negated_terms=negated_terms or [],
        negation_checked=negation_checked,
        source_scope_ok=source_scope_ok,
        quality=quality,
        quality_components=quality_components or {},
        reject_reason=reason,
        rationale=rationale,
    )


def evaluate_series_explain(
    series: str,
    text: str,
    source: str | None = None,
) -> MatchExplanation:
    """Evaluate a single series and return a structured accept/reject explanation."""
    rule = SERIES_RULES.get(series)
    if rule is None:
        return _reject_explanation(
            series,
            "unknown_series",
            f"Series {series} has no matcher rules.",
        )

    text_lower = text.lower()
    scope_ok = series_allowed_for_source(series, source)
    if source is not None and not scope_ok:
        return _reject_explanation(
            series,
            "source_scope",
            f"Feed source {source!r} is not scoped to series {series}.",
            source_scope_ok=False,
        )

    topic_family = SERIES_TOPIC_FAMILIES.get(series)
    family_hits: list[str] = []
    if topic_family:
        family_hits = _blocklist_hits(
            TOPIC_FAMILY_BLOCKLIST.get(topic_family, []),
            text_lower,
        )
        if family_hits:
            return _reject_explanation(
                series,
                "topic_family_blocklist",
                (
                    f"Topic family {topic_family!r} blocklist hit: "
                    f"{', '.join(family_hits[:3])}."
                ),
                topic_family_hits=family_hits,
                source_scope_ok=scope_ok,
            )

    block_hits = _blocklist_hits(rule.blocklist, text_lower)
    if block_hits:
        return _reject_explanation(
            series,
            "series_blocklist",
            f"Series blocklist hit: {', '.join(block_hits[:3])}.",
            blocklist_hits=block_hits,
            topic_family_hits=family_hits,
            source_scope_ok=scope_ok,
        )

    matched_anchors = _active_matches(rule.anchors, text_lower)
    matched_signals = _active_matches(rule.signals, text_lower)
    matched_required = _active_matches(rule.required, text_lower)
    matched_context = _active_matches(rule.context, text_lower)

    all_terms = rule.anchors + rule.signals + rule.required + rule.context
    negated = _negated_terms(all_terms, text_lower)
    negation_checked = bool(all_terms)

    if rule.required and not rule.anchors and not rule.signals:
        if not matched_required:
            return _reject_explanation(
                series,
                "missing_required_phrase",
                "Required phrase not found.",
                source_scope_ok=scope_ok,
                negated_terms=negated,
                negation_checked=negation_checked,
            )
        quality, components = _compute_quality(
            matched_anchors, matched_signals, matched_required, matched_context, rule
        )
        return MatchExplanation(
            series=series,
            decision="accept",
            matched_anchors=matched_anchors,
            matched_signals=matched_signals,
            matched_required=matched_required,
            matched_context=matched_context,
            blocklist_hits=block_hits,
            topic_family_hits=family_hits,
            negated_terms=negated,
            negation_checked=negation_checked,
            source_scope_ok=scope_ok,
            quality=quality,
            quality_components=components,
            rationale=f"Matched required phrases ({', '.join(matched_required)}).",
        )

    if rule.required and rule.context and rule.require_both:
        if not matched_required or not matched_context:
            return _reject_explanation(
                series,
                "missing_required_context_pair",
                "Required phrase and context must co-occur.",
                source_scope_ok=scope_ok,
                matched_anchors=matched_anchors,
                matched_signals=matched_signals,
                matched_required=matched_required,
                matched_context=matched_context,
                negated_terms=negated,
                negation_checked=negation_checked,
            )
    elif rule.require_both and rule.anchors and rule.signals:
        if not matched_anchors:
            return _reject_explanation(
                series,
                "missing_anchor",
                "Anchor term required but not found.",
                source_scope_ok=scope_ok,
                matched_signals=matched_signals,
                negated_terms=negated,
                negation_checked=negation_checked,
            )
        if len(matched_signals) < rule.min_signal_hits:
            return _reject_explanation(
                series,
                "missing_signal",
                (
                    f"Need {rule.min_signal_hits} signal term(s); "
                    f"found {len(matched_signals)}."
                ),
                source_scope_ok=scope_ok,
                matched_anchors=matched_anchors,
                matched_signals=matched_signals,
                negated_terms=negated,
                negation_checked=negation_checked,
            )
    elif rule.require_both and rule.anchors and rule.context:
        if not matched_anchors or not matched_context:
            return _reject_explanation(
                series,
                "missing_anchor_context_pair",
                "Anchor and context must co-occur.",
                source_scope_ok=scope_ok,
                matched_anchors=matched_anchors,
                matched_context=matched_context,
                negated_terms=negated,
                negation_checked=negation_checked,
            )
    else:
        any_match = (
            matched_anchors or matched_signals or matched_required or matched_context
        )
        if not any_match:
            return _reject_explanation(
                series,
                "no_terms_matched",
                "No rule terms matched.",
                source_scope_ok=scope_ok,
                negated_terms=negated,
                negation_checked=negation_checked,
            )

    quality, components = _compute_quality(
        matched_anchors, matched_signals, matched_required, matched_context, rule
    )
    matched_terms = (
        matched_anchors + matched_signals + matched_required + matched_context
    )
    return MatchExplanation(
        series=series,
        decision="accept",
        matched_anchors=matched_anchors,
        matched_signals=matched_signals,
        matched_required=matched_required,
        matched_context=matched_context,
        blocklist_hits=block_hits,
        topic_family_hits=family_hits,
        negated_terms=negated,
        negation_checked=negation_checked,
        source_scope_ok=scope_ok,
        quality=quality,
        quality_components=components,
        rationale=f"Matched terms: {', '.join(matched_terms[:6])}.",
    )


def evaluate_series(series: str, text: str) -> MatchResult | None:
    """Evaluate whether text matches a single series rule."""
    return evaluate_series_explain(series, text).to_result()


def series_allowed_for_source(series: str, source: str | None) -> bool:
    if source is None:
        return True
    allowed = FEED_TOPIC_SCOPE.get(source)
    if allowed is None:
        return True
    return series in allowed


def evaluate_all_candidates(
    title: str,
    description: str,
    source: str | None = None,
) -> list[MatchExplanation]:
    """Return accept explanations for all series that pass rules and quality floor."""
    text = (title + " " + (description or "")).lower()
    candidates: list[MatchExplanation] = []
    for series in SERIES_MATCH_ORDER:
        explanation = evaluate_series_explain(series, text, source=source)
        if (
            explanation.decision == "accept"
            and explanation.quality >= config.get_min_ingest_quality()
        ):
            candidates.append(explanation)
    candidates.sort(
        key=lambda c: (-c.quality, SERIES_MATCH_ORDER.index(c.series)),
    )
    return candidates


def select_best_candidate(
    candidates: list[MatchExplanation],
) -> MatchExplanation | None:
    """Pick the best candidate; when qualities cluster, keep the top match."""
    if not candidates:
        return None
    best = candidates[0]
    if len(candidates) >= 2:
        gap = best.quality - candidates[1].quality
        if gap < AMBIGUITY_QUALITY_GAP:
            best.ambiguous_runner_up = candidates[1].series
            best.ambiguous_quality_gap = round(gap, 3)
            log.debug(
                "Ambiguous ingest match: top=%s (%.3f) runner_up=%s (%.3f) gap=%.3f — keeping top",
                best.series,
                best.quality,
                candidates[1].series,
                candidates[1].quality,
                gap,
            )
    return best


def match_series(title: str, description: str, source: str | None = None) -> str | None:
    """Return the best matching series ticker for article content."""
    candidates = evaluate_all_candidates(title, description, source=source)
    best = select_best_candidate(candidates)
    return best.series if best else None


def match_for_correlation(
    series_ticker: str,
    title: str,
    description: str,
    source: str | None = None,
) -> MatchResult | None:
    """Evaluate whether an article matches a specific anomaly series."""
    text = (title + " " + (description or "")).lower()
    explanation = evaluate_series_explain(series_ticker, text, source=source)
    if explanation.decision != "accept":
        log.debug(
            "Correlation match rejected for %s: %s — %s",
            series_ticker,
            explanation.reject_reason,
            explanation.rationale,
        )
        return None
    min_match_quality = config.get_min_correlation_match_quality()
    if explanation.quality < min_match_quality:
        log.debug(
            "Correlation match below quality floor for %s: %.3f < %.3f",
            series_ticker,
            explanation.quality,
            min_match_quality,
        )
        return None
    return explanation.to_result()


def explain_for_correlation(
    series_ticker: str,
    title: str,
    description: str,
    source: str | None = None,
) -> MatchExplanation:
    """Structured matcher evaluation for correlation forensics."""
    text = (title + " " + (description or "")).lower()
    return evaluate_series_explain(series_ticker, text, source=source)
