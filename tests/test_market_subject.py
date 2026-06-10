import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import market_subject


class TestMarketSubject(unittest.TestCase):
    def test_extract_subject_code_from_nextag_ticker(self):
        self.assertEqual(
            market_subject.extract_subject_code("KXNEXTAG-29-TCRU"),
            "TCRU",
        )

    def test_extract_subject_code_skips_rate_suffix(self):
        self.assertIsNone(market_subject.extract_subject_code("KXFED-26DEC-T4.5"))

    def test_resolve_subject_from_api_fields(self):
        name = market_subject.resolve_subject_name(
            "KXNEXTAG-29-TCRU",
            "KXNEXTAG",
            subject_name="Ted Cruz",
        )
        self.assertEqual(name, "Ted Cruz")

    def test_resolve_subject_from_pardon_title(self):
        name = market_subject.resolve_subject_name(
            "KXTRUMPPARDONS-29JAN21-GMAX",
            "KXTRUMPPARDONS",
            market_title="Will Ghislaine Maxwell receive a presidential pardon before Jan 21, 2029?",
        )
        self.assertEqual(name, "Ghislaine Maxwell")

    def test_resolve_subject_from_ticker_fallback(self):
        name = market_subject.resolve_subject_name(
            "KXNEXTAG-29-TCRU",
            "KXNEXTAG",
            market_title="Who will be Trump's next Attorney General?",
        )
        self.assertEqual(name, "Ted Cruz")

    def test_article_mentions_subject(self):
        terms = market_subject.name_to_search_terms("Ted Cruz")
        self.assertTrue(
            market_subject.article_mentions_subject(
                "Senator Ted Cruz met with Trump transition officials",
                terms,
            )
        )
        self.assertFalse(
            market_subject.article_mentions_subject(
                "Todd Blanche Was Once Seen as Tempering Trump’s Tactics",
                terms,
            )
        )

    def test_person_scoped_series_requires_subject_gate(self):
        terms = market_subject.resolve_subject_search_terms(
            "KXNEXTAG-29-TCRU",
            "KXNEXTAG",
            subject_name="Ted Cruz",
        )
        self.assertIsNotNone(terms)
        self.assertIn("ted cruz", terms)

    def test_non_person_series_has_no_subject_gate(self):
        terms = market_subject.resolve_subject_search_terms(
            "KXFED-26DEC-T4.5",
            "KXFED",
            market_title="Fed funds upper bound",
        )
        self.assertIsNone(terms)


if __name__ == "__main__":
    unittest.main()
