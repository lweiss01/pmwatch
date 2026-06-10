import json
import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import keyword_matcher


FIXTURES_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "fixtures",
    "news_articles.json",
)


class TestKeywordMatcher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(FIXTURES_PATH, encoding="utf-8") as f:
            cls.fixtures = json.load(f)

    def test_kxfed_stress_test_is_not_match(self):
        """Regression: Fed stress test announcement must not match KXFED."""
        article = self.fixtures["fed_stress_test_false_positive"]
        self.assertIsNone(keyword_matcher.match_series(article["title"], article["description"]))
        self.assertIsNone(
            keyword_matcher.match_for_correlation(
                "KXFED", article["title"], article["description"]
            )
        )

    def test_kxfed_rate_decision_is_match(self):
        article = self.fixtures["fed_rate_decision_true_positive"]
        self.assertEqual(
            keyword_matcher.match_series(article["title"], article["description"]),
            "KXFED",
        )
        result = keyword_matcher.match_for_correlation(
            "KXFED", article["title"], article["description"]
        )
        self.assertIsNotNone(result)
        self.assertGreater(result.quality, 0)

    def test_scotus_ruling_not_resignation(self):
        article = self.fixtures["scotus_ruling_false_positive"]
        self.assertNotEqual(
            keyword_matcher.match_series(article["title"], article["description"]),
            "KXSCOTUSRESIGN",
        )
        self.assertIsNone(
            keyword_matcher.match_for_correlation(
                "KXSCOTUSRESIGN", article["title"], article["description"]
            )
        )

    def test_scotus_resignation_is_match(self):
        article = self.fixtures["scotus_resignation_true_positive"]
        self.assertEqual(
            keyword_matcher.match_series(article["title"], article["description"]),
            "KXSCOTUSRESIGN",
        )

    def test_shutdown_of_talks_not_govshut(self):
        article = self.fixtures["shutdown_talks_false_positive"]
        self.assertNotEqual(
            keyword_matcher.match_series(article["title"], article["description"]),
            "KXGOVSHUT",
        )
        self.assertIsNone(
            keyword_matcher.match_for_correlation(
                "KXGOVSHUT", article["title"], article["description"]
            )
        )

    def test_government_shutdown_is_match(self):
        article = self.fixtures["government_shutdown_true_positive"]
        self.assertEqual(
            keyword_matcher.match_series(article["title"], article["description"]),
            "KXGOVSHUT",
        )

    def test_negation_suppresses_pardon(self):
        article = self.fixtures["negated_pardon_false_positive"]
        self.assertIsNone(
            keyword_matcher.match_for_correlation(
                "KXTRUMPPARDONS", article["title"], article["description"]
            )
        )

    def test_dni_word_boundary(self):
        self.assertIsNone(
            keyword_matcher.match_for_correlation(
                "KXNEXTODNI",
                "mundane policy discussion",
                "A routine update with no intelligence community changes.",
            )
        )

    def test_colin_powell_not_kxfed(self):
        self.assertIsNone(
            keyword_matcher.match_for_correlation(
                "KXFED",
                "Colin Powell legacy remembered",
                "Historians revisit the former secretary of state's career.",
            )
        )

    def test_existing_positive_cases(self):
        """Port legacy test_news keyword cases."""
        series_ag = keyword_matcher.match_series(
            "President nominates new Attorney General Blanche",
            "Blanche has been chosen to lead the Justice Department (DOJ).",
        )
        self.assertEqual(series_ag, "KXNEXTAG")

        series_sc = keyword_matcher.match_series(
            "Supreme Court Justice retirement announcement expected",
            "A major vacancy is opening up on the high court (SCOTUS).",
        )
        self.assertEqual(series_sc, "KXSCOTUSRESIGN")

        series_cpi = keyword_matcher.match_series(
            "Latest CPI index shows rise in consumer prices",
            "Inflation figures released by the Bureau of Labor Statistics (BLS).",
        )
        self.assertEqual(series_cpi, "KXCPI")

        series_none = keyword_matcher.match_series(
            "Local sports team wins championship",
            "Fans celebrate in the streets after historical victory.",
        )
        self.assertIsNone(series_none)

    def test_cabinet_resignation_fixture(self):
        article = self.fixtures["cabinet_resignation_true_positive"]
        self.assertEqual(
            keyword_matcher.match_series(article["title"], article["description"]),
            "KXCABOUT",
        )

    def test_term_in_text_word_boundary(self):
        self.assertFalse(keyword_matcher.term_in_text("dni", "mundane policy discussion"))
        self.assertTrue(keyword_matcher.term_in_text("dni", "the dni nominee was announced"))

    def test_blocklist_rejects_before_anchor_signal(self):
        result = keyword_matcher.evaluate_series(
            "KXFED",
            "federal reserve announces bank stress test schedule for major lenders",
        )
        self.assertIsNone(result)

    def test_match_quality_in_valid_range(self):
        article = self.fixtures["fed_rate_decision_true_positive"]
        result = keyword_matcher.match_for_correlation(
            "KXFED", article["title"], article["description"]
        )
        self.assertIsNotNone(result)
        self.assertGreaterEqual(result.quality, 0.25)
        self.assertLessEqual(result.quality, 1.0)


if __name__ == "__main__":
    unittest.main()
