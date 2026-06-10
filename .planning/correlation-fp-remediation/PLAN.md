# Correlation False-Positive Remediation Plan

**Project:** pmwatch  
**Created:** 2026-06-10  
**Status:** Milestone 1 — complete (2026-06-10)  
**Scope:** Keyword matching, correlation scoring, scorer hygiene, feeds, cross-market detection, MNPI structure

---

## Problem Statement

The news-to-anomaly correlation pipeline in `news_engine.py` produces false positives because it uses naive substring matching (`if kw.lower() in text`). A documented example:

| Field | Value |
|-------|-------|
| **News** | Federal Reserve Board announces annual bank stress test results (Jun 24) |
| **Anomaly** | KXFED-26JUN-T3.50 (fed funds rate upper bound) |
| **Matched on** | `federal reserve` |
| **Lead time** | 15h 18m |
| **Anomaly score** | 299 (uncapped) |
| **Result** | False correlation — stress tests are unrelated to rate policy |

Three issues compound:

1. **No topic specificity** — broad anchor terms like `federal reserve` fire without rate-policy signals.
2. **Duplicate matching logic** — `match_series()` at ingest vs. inline keyword loop in `correlate_all_recent_anomalies()` (L305–311).
3. **Uncapped scorer output** — raw scores in the hundreds inflate correlation confidence.

---

## Root Cause Map

| Finding | Current Code | Smallest Fix |
|---------|-------------|--------------|
| Substring traps | `news_engine.py` L99, L306 | Word boundaries + phrase priority |
| KXFED stress-test FP | `SERIES_KEYWORDS["KXFED"]` includes bare `"federal reserve"` | Anchors + signals + blocklist |
| SCOTUS resignation FP | `"resign"`, `"justice"` match independently | `require_both` co-occurrence |
| GOVSHUT FP | bare `"shutdown"` | Phrase-guarded `required` terms |
| Powell / DNI / pardon traps | no boundary or context | Word boundaries, disambiguation |
| Negation ("no pardon") | not checked | 5-token negation window |
| Correlation bypasses matcher | `correlate_all_recent_anomalies()` inline loop | Call shared matcher |
| `overlap_ratio` floor 0.25 | L311 inflates weak hits | Match-quality score from matcher |
| Flat 48h time penalty | `calculate_correlation_confidence()` L209–211 | Tiered pre-news bands (M2) |
| Uncapped scores (299) | `scorer.py` L211 | `min(100, raw_score)` (M3) |
| 6h dedup too aggressive | `already_flagged_recently(hours=6)` | 2h + score-delta gate (M3) |
| No scorer tests | missing `test_scorer.py` | New unit tests (M3) |
| Cross-market patterns | `cluster_scorer.py` groups by ticker only | Cross-series grouper (M5) |
| Per-actor velocity | Kalshi trades lack trader identity | Blocked until disclosure feeds (M4/M6) |
| Missing STOCK Act feeds | `DEFAULT_FEEDS` L55–90 | Add feeds (M4) |
| `news_engine.py` monolith | 343 lines, 4 concerns | Extract matcher first (M1) |

### Critical gap

Fixing `match_series()` alone is **insufficient**. Correlation re-implements matching:

```python
# news_engine.py L305–311 (must be replaced)
matches = [kw for kw in series_keywords if kw.lower() in text]
if matches:
    overlap_ratio = max(0.25, len(matches) / len(series_keywords))
```

Both ingestion and correlation must call one shared module.

---

## Milestone Overview

```
M1  Keyword Matcher Core          ← START HERE (highest ROI)
M2  Correlation Temporal Model    (depends on M1)
M3  Scorer Hygiene                (parallel with M2 after M1)
M4  Feeds + Source-Topic Gating   (depends on M2)
M5  Cross-Market Detection        (depends on M3)
M6  MNPI Actor Structure          (depends on M4)
M7  Architecture Cleanup          (optional, after behavior correct)
```

**Recommended order:** M1 → M2 + M3 (parallel) → M4 → M5 → M6 → M7

---

## Milestone 1 — Keyword Matcher Core

**Goal:** Eliminate topic-level false positives with the smallest, most testable diff.  
**Status:** Complete (2026-06-10)  
**Estimated effort:** 3–5 days  
**Schema changes:** None  
**Files touched:** `keyword_matcher.py` (new), `news_engine.py`, `tests/test_keyword_matcher.py` (new), `tests/test_news.py` (trim)

### Deliverables

- [x] `keyword_matcher.py` — single source of truth for all keyword logic
- [x] Priority series rules migrated (KXFED, KXSCOTUSRESIGN, KXGOVSHUT, KXTRUMPPARDONS)
- [x] Remaining series rules migrated
- [x] `news_engine.py` wired to shared matcher (ingest + correlation)
- [x] `tests/test_keyword_matcher.py` with regression fixtures
- [x] Fed stress-test regression test passes (returns `None` for KXFED)

### 1.1 Module design — `keyword_matcher.py`

**Types:**

```python
@dataclass
class SeriesRule:
    anchors: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    required: list[str] = field(default_factory=list)   # phrase-guarded only
    context: list[str] = field(default_factory=list)    # co-occurrence partners
    blocklist: list[str] = field(default_factory=list)
    require_both: bool = False
    min_signal_hits: int = 1

@dataclass
class MatchResult:
    series: str
    matched_anchors: list[str]
    matched_signals: list[str]
    negated: bool
    quality: float          # 0.0–1.0, replaces overlap_ratio
    reject_reason: str | None
```

**Functions:**

| Function | Purpose |
|----------|---------|
| `term_in_text(term, text)` | Phrase match for multi-word; `\b` boundary for tokens ≤4 chars |
| `find_term_positions(term, text)` | For negation checks |
| `is_negated(text, pos)` | 5-token window: `{no, not, denied, rejected, failed, won't, unlikely, without, never, refuse}` |
| `evaluate_series(series, text)` | Blocklist → negation → co-occurrence |
| `match_series(title, description, source=None)` | First matching series; respects `FEED_TOPIC_SCOPE` (wired in M4, stub in M1) |
| `match_for_correlation(series_ticker, title, description)` | Targeted evaluation for one anomaly series |

**Backward compatibility:** Plain `list[str]` entries treated as signals with `require_both=False` for incremental migration.

**Matching order:** Longest phrases first within each bucket.

**Short-token boundary list:** `dni`, `fed`, `cr`, `eo`, `bls`, `gdp`, `cpi`, `doj`, `doge`, `ag`

### 1.2 Priority series rules

#### KXFED (stress-test regression fixture)

```python
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
}
```

**Regression fixture (must return `None`):**
> Federal Reserve Board announces that results from its annual bank stress test will be released on Wednesday, June 24, at 4 p.m. EDT.

#### KXSCOTUSRESIGN

```python
"KXSCOTUSRESIGN": {
    "anchors": ["supreme court", "scotus", "high court"],
    "signals": ["resign", "retirement", "retire", "vacancy", "step down", "stepping down", "health reasons"],
    "require_both": True,
    "blocklist": [
        "ruling", "decision", "opinion", "oral argument", "certiorari",
        "cert granted", "dissent", "affirmed", "reversed", "hearing on the merits",
    ],
}
```

Remove bare `"justice"` — matches DOJ/criminal justice unrelated to SCOTUS personnel.

#### KXGOVSHUT

```python
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
}
```

#### KXTRUMPPARDONS / KXTRUMPPARDONFAMILY

```python
"KXTRUMPPARDONS": {
    "signals": ["pardon", "clemency", "commute sentence", "commuted"],
    "blocklist": ["beg your pardon", "i beg your pardon"],
    "require_both": False,
}
```

Negation suppresses: `"no pardon"`, `"denied pardon"`, `"pardon rejected"`.

#### Remaining series (same milestone, rule-only)

| Series | Strategy |
|--------|----------|
| `KXCPI` | Anchors: `bls`, `bureau of labor statistics`; Signals: `cpi`, `consumer price index`, `inflation`; Blocklist: `cpi security`, `cyber` |
| `KXGDP` | Anchors: `bea`; Signals: `gdp`, `gross domestic product` |
| `KXIMPEACH` | Signals: `impeach`, `impeachment`; Blocklist: `acquitted`, `trial ended` |
| `KXCR` | Required phrases only; never bare `cr` |
| `KXAGENCYELIM` | Word-boundary `doge` |
| `KXNEXTAG` | Anchors: `attorney general`; Signals: `nomination`, `nominee` |
| `KXNEXTODNI` | Word-boundary `dni`; require intelligence anchor |
| `KXEOTRUMPTERM` | Required: `executive order` (phrase) |
| `KXVETOOVERRIDE` | Required: `veto override` |
| `KXSCOURT` | Anchors: `supreme court`; Signals: `nominee`, `nomination` |
| `KXTARIFFS` | Anchors: `supreme court`, `scotus`; Signals: `tariff`, `tariffs` |
| Geopolitical/legislative remainder | Anchor+signal where any single word is ambiguous |

### 1.3 Wire into `news_engine.py`

- Import from `keyword_matcher`
- Remove `SERIES_KEYWORDS` dict (moved to matcher)
- `parse_rss_string` / `parse_fed_register_json`: pass `source` to `match_series()`
- `correlate_all_recent_anomalies`: replace inline loop with `match_for_correlation()`

```python
result = match_for_correlation(series_ticker, article["title"], article["description"])
if result and not result.negated and result.quality > 0:
    confidence = calculate_correlation_confidence(..., overlap_ratio=result.quality)
```

### 1.4 Tests — `tests/test_keyword_matcher.py`

| Test | Input | Expected |
|------|-------|----------|
| `test_kxfed_stress_test_is_not_match` | Fed stress test headline (6/9 fixture) | `None` for KXFED |
| `test_kxfed_rate_decision_is_match` | "FOMC holds fed funds rate target range unchanged" | `KXFED` |
| `test_scotus_ruling_not_resignation` | "Supreme Court issues ruling on tariffs case" | not `KXSCOTUSRESIGN` |
| `test_scotus_resignation_is_match` | "Supreme Court justice announces retirement" | `KXSCOTUSRESIGN` |
| `test_shutdown_of_talks_not_govshut` | "Senators shutdown of talks continues" | not `KXGOVSHUT` |
| `test_government_shutdown_is_match` | "Congress faces government shutdown deadline" | `KXGOVSHUT` |
| `test_negation_suppresses_pardon` | "President denied pardon request" | not `KXTRUMPPARDONS` |
| `test_dni_word_boundary` | "mundane policy discussion" | no hit on `dni` |
| `test_colin_powell_not_kxfed` | "Colin Powell legacy remembered" | not `KXFED` |
| `test_existing_positive_cases` | Port passing cases from `test_news.py` | still pass |

**Fixture file:** `tests/fixtures/news_articles.json` — canonical false/true positive articles.

### M1 acceptance criteria

- [ ] Fed stress test article does not match KXFED
- [ ] Valid FOMC rate article still matches KXFED
- [ ] Correlation path uses `match_for_correlation()`, not inline loop
- [ ] `python -m unittest discover tests -v` passes with zero network calls

---

## Milestone 2 — Correlation Temporal Model

**Goal:** Weight the pre-news window; suppress long-lead noise.  
**Status:** Blocked on M1  
**Estimated effort:** 2–3 days  
**Schema changes:** None (notes field for direction tag)

### Deliverables

- [ ] Tiered temporal multipliers in `calculate_correlation_confidence()`
- [ ] Optional post-news reaction window (0–6h at 0.7×)
- [ ] `MIN_CORRELATION_CONFIDENCE = 12.0` threshold
- [ ] Replace bare `except Exception: pass` with specific handling
- [ ] `tests/test_correlation.py`

### Temporal bands

| Band | Condition | Multiplier |
|------|-----------|------------|
| Tier 1 (hot) | 0–2 hours pre-news | 1.8× |
| Tier 2 (warm) | 2–8 hours | 1.3× |
| Tier 3 (cool) | 8–48 hours | linear 1.0 → 0.3 |
| Tier 4 (reaction) | 0–6 hours post-news (`time_diff < 0`) | 0.7× |
| Tier 5 (stale) | 6–48 hours post-news | 0.0× (exclude) |

**Formula:**
```
confidence = min(100, anomaly_score) × source_weight × match_quality × temporal_multiplier
```

### M2 acceptance criteria

- [ ] Same match at 1h lead scores higher than 20h lead
- [ ] Stress test + KXFED anomaly → 0 correlation rows in DB integration test
- [ ] Valid FOMC rate + KXFED anomaly → 1 correlation row

---

## Milestone 3 — Scorer Hygiene

**Goal:** Meaningful alert tiers; avoid missing second signals.  
**Status:** Blocked on M1 (can run parallel with M2)  
**Estimated effort:** 2 days  
**Schema changes:** None

### Deliverables

- [ ] `min(100.0, raw_score)` in `score_market()`
- [ ] Score-delta dedup: suppress only if new score < last × 1.20
- [ ] Dedup window reduced from 6h → 2h
- [ ] `tests/test_scorer.py` (new)

### M3 acceptance criteria

- [ ] `anomaly_score` never exceeds 100
- [ ] Second flag at +15% within 2h suppressed; +30% allowed
- [ ] Score 299 scenario caps at 100

---

## Milestone 4 — Feeds + Source-Topic Gating

**Goal:** Reduce cross-domain matches; add disclosure ground truth.  
**Status:** Blocked on M2  
**Estimated effort:** 3 days  
**Schema changes:** Optional `source_type = disclosure_filing`

### Deliverables

- [ ] `FEED_TOPIC_SCOPE` fully wired in `match_series()`
- [ ] New feeds: Senate eFD, House disclosures, White House briefings, SEC EDGAR Form 4
- [ ] `SOURCE_WEIGHTS`: disclosure_filing=2.0, primary_gov=1.5, mainstream=1.0
- [ ] `tests/test_feed_ingestion.py` with mocked fixtures

### M4 acceptance criteria

- [ ] Fed press release never evaluated against KXINSURRECTION etc.
- [ ] Disclosure filing gets 2.0× source weight in confidence

---

## Milestone 5 — Cross-Market Detection

**Goal:** Surface multi-leg patterns across related series.  
**Status:** Blocked on M3  
**Estimated effort:** 4 days  
**Schema changes:** `cross_market_clusters` table

### Deliverables

- [ ] `cross_market_scorer.py` — group by `mnpi_actors` + 24h window
- [ ] DB table + upsert helpers
- [ ] Scheduler integration after `run_cluster_scorer()`
- [ ] API endpoint `/api/cross-market-clusters`
- [ ] `tests/test_cross_market.py`

### M5 acceptance criteria

- [ ] KXFED + KXCPI anomalies, same `mnpi_actors`, 12h apart → 1 cross-market cluster
- [ ] Same series only → no cross-market cluster

---

## Milestone 6 — MNPI Actor Structure

**Goal:** Context-aware scoring by actor access level.  
**Status:** Blocked on M4  
**Estimated effort:** 3 days  
**Schema changes:** Watchlist v2 (backward compatible)

### Deliverables

- [ ] `watchmarket_watchlist.json` v2 with structured `actors` array
- [ ] `collector.py` loads both flat `risk` and structured actors
- [ ] `CLEARANCE_MULTIPLIER` applied in `score_market()`
- [ ] Phase 6b (deferred): per-actor velocity via STOCK Act disclosure correlation

### Constraint

Kalshi public trade API has no trader identity (`collector.py` trade dict). Per-actor velocity baselines require disclosure feed ingestion (M4) first.

---

## Milestone 7 — Architecture Cleanup (Optional)

**Goal:** Split `news_engine.py` after behavior is correct.  
**Status:** After M1–M6  
**Estimated effort:** 1–2 days

| Extract | From |
|---------|------|
| `keyword_matcher.py` | Done in M1 |
| `correlation_engine.py` | `correlate_*`, `calculate_correlation_confidence` |
| `feed_ingestion.py` | `fetch_and_ingest_feeds`, parsers |

---

## Minimum Viable Fix (if scope must shrink)

1. `keyword_matcher.py` with KXFED, KXSCOTUSRESIGN, KXGOVSHUT rules + blocklists
2. Replace correlation inline loop with `match_for_correlation()`
3. `test_kxfed_stress_test_is_not_match`
4. Cap scorer at 100

**Estimated diff:** ~250 lines added, ~40 changed, 0 schema migrations.

---

## Test Strategy (Full)

```
tests/
  test_keyword_matcher.py   ← M1 (~25 cases)
  test_correlation.py       ← M2 (~10 cases)
  test_scorer.py            ← M3 (~8 cases)
  test_feed_ingestion.py    ← M4 (~8 cases)
  test_cross_market.py      ← M5 (~5 cases)
  test_news.py              ← parser integration only
  fixtures/news_articles.json
```

**CI gate:** `python -m unittest discover tests -v` — zero network calls.

---

## Risks

| Risk | Mitigation |
|------|------------|
| Over-tight rules miss real correlations | Gradated `quality` score; DEBUG log near-misses |
| Stale `news_articles.series_ticker` after rule change | One-time re-tag script or re-ingest |
| Feed URLs break | Fixture-based tests; graceful fetch failure |
| Breaking existing `test_news.py` cases | Update fixtures for anchor+signal co-occurrence |

---

## References

- False positive example: Fed stress test → KXFED (6/9/2026, 15h18m lead, score 299)
- Primary code: `news_engine.py`, `scorer.py`, `cluster_scorer.py`
- Existing tests: `tests/test_news.py` (keyword cases to migrate)
