# Milestone Tracker — Correlation False-Positive Remediation

Quick-reference checklist. Full design in [PLAN.md](./PLAN.md).

---

## M1 — Keyword Matcher Core ✅ **COMPLETE**

| # | Task | Status |
|---|------|--------|
| 1.1 | Create `keyword_matcher.py` with `SeriesRule`, `MatchResult`, core functions | ✅ |
| 1.2 | Implement `term_in_text()` — phrase + word-boundary for short tokens | ✅ |
| 1.3 | Implement `is_negated()` — 5-token negation window | ✅ |
| 1.4 | Implement `evaluate_series()` — blocklist → match → co-occurrence | ✅ |
| 1.5 | Implement `match_series()` and `match_for_correlation()` | ✅ |
| 1.6 | Migrate KXFED rules (anchors/signals/blocklist) | ✅ |
| 1.7 | Migrate KXSCOTUSRESIGN rules | ✅ |
| 1.8 | Migrate KXGOVSHUT rules | ✅ |
| 1.9 | Migrate KXTRUMPPARDONS / KXTRUMPPARDONFAMILY rules | ✅ |
| 1.10 | Migrate remaining ~30 series rules | ✅ |
| 1.11 | Wire `news_engine.py` — remove `SERIES_KEYWORDS`, import matcher | ✅ |
| 1.12 | Replace correlation inline loop with `match_for_correlation()` | ✅ |
| 1.13 | Create `tests/fixtures/news_articles.json` | ✅ |
| 1.14 | Create `tests/test_keyword_matcher.py` (~25 cases) | ✅ |
| 1.15 | Update `tests/test_news.py` — delegate keyword tests | ✅ |
| 1.16 | Verify: Fed stress test → no KXFED match | ✅ |
| 1.17 | Verify: `python -m unittest discover tests -v` green | ✅ |

**M1 exit gate:** Stress-test regression passes; correlation uses shared matcher.

---

## M2 — Correlation Temporal Model ✅ **COMPLETE**

| # | Task | Status |
|---|------|--------|
| 2.1 | Tiered temporal multipliers in `calculate_correlation_confidence()` | ✅ |
| 2.2 | Post-news reaction window (0–6h at 0.7×) | ✅ |
| 2.3 | `MIN_CORRELATION_CONFIDENCE = 12.0` gate | ✅ |
| 2.4 | Fix silent `except Exception: pass` in correlation insert | ✅ |
| 2.5 | Create `tests/test_correlation.py` | ✅ |
| 2.6 | DB integration: stress test + KXFED → 0 rows | ✅ |

**M2 exit gate:** Temporal tiers verified; no stress-test correlations in DB.

---

## M3 — Scorer Hygiene ✅ **COMPLETE**

| # | Task | Status |
|---|------|--------|
| 3.1 | Cap `anomaly_score` at 100 in `score_market()` | ✅ |
| 3.2 | Score-delta dedup (20% threshold, 2h window) | ✅ |
| 3.3 | Update `run_scorer()` prefetch to 2h + score map | ✅ |
| 3.4 | Create `tests/test_scorer.py` | ✅ |

**M3 exit gate:** Score never > 100; dedup allows significant re-flags.

---

## M4 — Feeds + Source-Topic Gating ✅ **COMPLETE**

| # | Task | Status |
|---|------|--------|
| 4.1 | Wire `FEED_TOPIC_SCOPE` in `match_series()` | ✅ |
| 4.2 | Add Senate eFD disclosures feed | ✅ |
| 4.3 | Add House Financial Disclosures feed | ✅ |
| 4.4 | Add White House Briefings feed | ✅ |
| 4.5 | Add SEC EDGAR Form 4 Atom feed | ✅ |
| 4.6 | `SOURCE_WEIGHTS` with `disclosure_filing=2.0` | ✅ |
| 4.7 | Create `tests/test_feed_ingestion.py` | ✅ |

**M4 exit gate:** Scoped feeds; disclosure weight applied.

---

## M5 — Cross-Market Detection ✅ **COMPLETE**

| # | Task | Status |
|---|------|--------|
| 5.1 | Create `cross_market_scorer.py` | ✅ |
| 5.2 | Add `cross_market_clusters` DB table | ✅ |
| 5.3 | Scheduler + API integration | ✅ |
| 5.4 | Create `tests/test_cross_market.py` | ✅ |

**M5 exit gate:** Multi-series same-actor clusters detected.

---

## M6 — MNPI Actor Structure

| # | Task | Status |
|---|------|--------|
| 6.1 | Watchlist v2 schema with structured `actors` | ⬜ |
| 6.2 | `collector.py` backward-compatible loader | ⬜ |
| 6.3 | `CLEARANCE_MULTIPLIER` in scorer | ⬜ |
| 6.4 | (Deferred) Per-actor velocity via disclosure correlation | ⬜ |

**M6 exit gate:** Clearance tier boosts anomaly scores.

---

## M7 — Architecture Cleanup (Optional)

| # | Task | Status |
|---|------|--------|
| 7.1 | Extract `correlation_engine.py` | ⬜ |
| 7.2 | Extract `feed_ingestion.py` | ⬜ |
| 7.3 | `news_engine.py` becomes thin orchestrator | ⬜ |

---

## Status Legend

- ⬜ Not started
- 🔄 In progress
- ✅ Complete
- ⏸️ Deferred

**Last updated:** 2026-06-10
