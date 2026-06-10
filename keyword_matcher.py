"""
Keyword matching for news-to-series correlation.

Uses anchor+signal co-occurrence, phrase-guarded required terms, blocklists,
word-boundary matching for short tokens, and negation detection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

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

    @property
    def all_matched_terms(self) -> list[str]:
        return (
            self.matched_anchors
            + self.matched_signals
            + self.matched_required
            + self.matched_context
        )


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


def is_negated(text: str, pos: int) -> bool:
    """True if a negation word appears within 5 tokens before the match position."""
    before = text[:pos].lower()
    tokens = re.findall(r"\b[\w']+\b", before)
    window = tokens[-5:]
    return any(token in NEGATION_WORDS for token in window)


def _active_matches(terms: list[str], text: str) -> list[str]:
    """Return terms that match and are not entirely negated at every occurrence."""
    matched: list[str] = []
    for term in _sort_terms_longest_first(terms):
        positions = find_term_positions(term, text)
        if not positions:
            continue
        if any(not is_negated(text, pos) for pos in positions):
            matched.append(term)
    return matched


def _compute_quality(
    matched_anchors: list[str],
    matched_signals: list[str],
    matched_required: list[str],
    matched_context: list[str],
    rule: SeriesRule,
) -> float:
    if rule.required and not rule.anchors and not rule.signals:
        req_score = min(1.0, len(matched_required) / max(1, len(rule.required)))
        return round(max(0.35, req_score), 3)

    if rule.require_both and rule.anchors and rule.signals:
        anchor_score = min(1.0, len(matched_anchors) / max(1, len(rule.anchors)))
        signal_score = min(1.0, len(matched_signals) / max(1, len(rule.signals)))
        return round(0.35 * anchor_score + 0.65 * signal_score, 3)

    total_terms = rule.anchors + rule.signals + rule.required + rule.context
    matched_count = (
        len(matched_anchors)
        + len(matched_signals)
        + len(matched_required)
        + len(matched_context)
    )
    return round(max(0.25, min(1.0, matched_count / max(1, len(total_terms)))), 3)


def evaluate_series(series: str, text: str) -> MatchResult | None:
    """Evaluate whether text matches a single series rule."""
    rule = SERIES_RULES.get(series)
    if rule is None:
        return None

    text_lower = text.lower()

    for blocked in _sort_terms_longest_first(rule.blocklist):
        if term_in_text(blocked, text_lower):
            return None

    matched_anchors = _active_matches(rule.anchors, text_lower)
    matched_signals = _active_matches(rule.signals, text_lower)
    matched_required = _active_matches(rule.required, text_lower)
    matched_context = _active_matches(rule.context, text_lower)

    negated_only = (
        not matched_anchors
        and not matched_signals
        and not matched_required
        and not matched_context
    )

    # Required-only rules (phrase-guarded).
    if rule.required and not rule.anchors and not rule.signals:
        if not matched_required:
            return None
        quality = _compute_quality(
            matched_anchors, matched_signals, matched_required, matched_context, rule
        )
        return MatchResult(
            series=series,
            matched_anchors=matched_anchors,
            matched_signals=matched_signals,
            matched_required=matched_required,
            matched_context=matched_context,
            negated=negated_only,
            quality=quality,
        )

    # Anchor + context co-occurrence (required + context variant).
    if rule.required and rule.context and rule.require_both:
        if not matched_required or not matched_context:
            return None
    elif rule.require_both and rule.anchors and rule.signals:
        if not matched_anchors or len(matched_signals) < rule.min_signal_hits:
            return None
    elif rule.require_both and rule.anchors and rule.context:
        if not matched_anchors or not matched_context:
            return None
    else:
        any_match = (
            matched_anchors or matched_signals or matched_required or matched_context
        )
        if not any_match:
            return None

    quality = _compute_quality(
        matched_anchors, matched_signals, matched_required, matched_context, rule
    )
    return MatchResult(
        series=series,
        matched_anchors=matched_anchors,
        matched_signals=matched_signals,
        matched_required=matched_required,
        matched_context=matched_context,
        negated=False,
        quality=quality,
    )


def _series_allowed_for_source(series: str, source: str | None) -> bool:
    if source is None:
        return True
    allowed = FEED_TOPIC_SCOPE.get(source)
    if allowed is None:
        return True
    return series in allowed


def match_series(title: str, description: str, source: str | None = None) -> str | None:
    """Return the first matching series ticker for article content."""
    text = (title + " " + (description or "")).lower()
    for series in SERIES_MATCH_ORDER:
        if not _series_allowed_for_source(series, source):
            continue
        if evaluate_series(series, text) is not None:
            return series
    return None


def match_for_correlation(
    series_ticker: str,
    title: str,
    description: str,
) -> MatchResult | None:
    """Evaluate whether an article matches a specific anomaly series."""
    text = (title + " " + (description or "")).lower()
    return evaluate_series(series_ticker, text)
