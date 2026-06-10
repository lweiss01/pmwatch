"""
Resolve person-specific market subjects from Kalshi tickers and metadata.

Many Kalshi markets share a generic series title (e.g. all KXNEXTAG contracts read
"Who will be Trump's next Attorney General?") while the actual subject lives in
yes_sub_title / rules_primary or the ticker suffix (TCRU = Ted Cruz).
"""

from __future__ import annotations

import logging
import re
import urllib.request
import json

from keyword_matcher import term_in_text

log = logging.getLogger(__name__)

KALSHI_MARKET_URL = "https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}"

# Series where individual contracts usually name a specific person.
PERSON_SCOPED_SERIES = frozenset({
    "KXCABOUT",
    "KXNEXTAG",
    "KXNEXTDEF",
    "KXNEXTSTATE",
    "KXNEXTODNI",
    "KXTRUMPPARDONS",
    "KXTRUMPPARDONFAMILY",
    "KXSCOURT",
})

# Fallback when metadata has not been collected yet (suffix -> display name).
SUBJECT_CODE_NAMES: dict[str, dict[str, str]] = {
    "KXNEXTAG": {
        "TCRU": "Ted Cruz",
        "TBLA": "Todd Blanche",
    },
    "KXTRUMPPARDONS": {
        "GMAX": "Ghislaine Maxwell",
        "TBLA": "Todd Blanche",
        "ETRU": "Eric Trump",
        "PHEG": "Pete Hegseth",
        "MRUB": "Marco Rubio",
        "PBON": "Pam Bondi",
        "EMUS": "Elon Musk",
        "JLOW": "Jho Low",
        "SBES": "Scott Bessent",
    },
}

_RATE_SUFFIX_RE = re.compile(r"^T\d", re.IGNORECASE)
_SUBJECT_CODE_RE = re.compile(r"^[A-Z]{2,8}$")
_TITLE_PARDON_RE = re.compile(
    r"will\s+(.+?)\s+receive\s+(?:a\s+)?presidential\s+pardon",
    re.IGNORECASE,
)
_RULES_PRIMARY_RE = re.compile(
    r"if\s+(.+?)\s+(?:has been|is|becomes)",
    re.IGNORECASE,
)


def extract_subject_code(ticker: str) -> str | None:
    """Return the final ticker segment when it looks like a subject code."""
    parts = ticker.split("-")
    if len(parts) < 3:
        return None
    code = parts[-1].upper()
    if _RATE_SUFFIX_RE.match(code):
        return None
    if _SUBJECT_CODE_RE.match(code):
        return code
    return None


def name_to_search_terms(full_name: str) -> list[str]:
    """Build case-insensitive search terms from a person's display name."""
    cleaned = " ".join(full_name.strip().split())
    if not cleaned:
        return []

    lower = cleaned.lower()
    terms = [lower]
    parts = lower.split()
    if len(parts) >= 2:
        terms.append(parts[-1])
        terms.append(f"{parts[0]} {parts[-1]}")
    return list(dict.fromkeys(terms))


def parse_subject_from_title(title: str) -> str | None:
    """Extract a subject name from person-specific market titles."""
    if not title:
        return None
    match = _TITLE_PARDON_RE.search(title)
    if match:
        return match.group(1).strip()
    return None


def parse_subject_from_rules(rules_primary: str) -> str | None:
    if not rules_primary:
        return None
    match = _RULES_PRIMARY_RE.search(rules_primary)
    if match:
        return match.group(1).strip()
    return None


def resolve_subject_name(
    ticker: str,
    series_ticker: str,
    *,
    market_title: str = "",
    subject_name: str = "",
    rules_primary: str = "",
) -> str | None:
    """Resolve the person this contract is about, if any."""
    if subject_name and subject_name.strip():
        return subject_name.strip()

    parsed_title = parse_subject_from_title(market_title)
    if parsed_title:
        return parsed_title

    parsed_rules = parse_subject_from_rules(rules_primary)
    if parsed_rules:
        return parsed_rules

    code = extract_subject_code(ticker)
    if code and series_ticker in SUBJECT_CODE_NAMES:
        fallback = SUBJECT_CODE_NAMES[series_ticker].get(code)
        if fallback:
            return fallback

    return None


def is_person_scoped_market(
    ticker: str,
    series_ticker: str,
    *,
    market_title: str = "",
    subject_name: str = "",
) -> bool:
    if series_ticker not in PERSON_SCOPED_SERIES:
        return False
    if resolve_subject_name(
        ticker,
        series_ticker,
        market_title=market_title,
        subject_name=subject_name,
    ):
        return True
    return bool(extract_subject_code(ticker) or parse_subject_from_title(market_title))


def resolve_subject_search_terms(
    ticker: str,
    series_ticker: str,
    *,
    market_title: str = "",
    subject_name: str = "",
    rules_primary: str = "",
) -> list[str] | None:
    """Return search terms for person-scoped markets, else None."""
    if not is_person_scoped_market(
        ticker,
        series_ticker,
        market_title=market_title,
        subject_name=subject_name,
    ):
        return None

    resolved = resolve_subject_name(
        ticker,
        series_ticker,
        market_title=market_title,
        subject_name=subject_name,
        rules_primary=rules_primary,
    )
    if not resolved:
        log.debug("Person-scoped market %s missing subject metadata", ticker)
        return []

    return name_to_search_terms(resolved)


def article_mentions_subject(text: str, subject_terms: list[str]) -> bool:
    """True when article text mentions at least one subject search term."""
    if not subject_terms:
        return False
    text_lower = text.lower()
    for term in sorted(subject_terms, key=len, reverse=True):
        if term_in_text(term, text_lower):
            return True
    return False


def fetch_subject_name_from_api(ticker: str) -> str | None:
    """Fetch yes_sub_title from Kalshi for a single market ticker."""
    url = KALSHI_MARKET_URL.format(ticker=ticker)
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "pmwatch/1.0 subject-resolver"},
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        market = payload.get("market", payload)
        return (market.get("yes_sub_title") or "").strip() or None
    except Exception as exc:
        log.warning("Failed to fetch subject for %s: %s", ticker, exc)
        return None
