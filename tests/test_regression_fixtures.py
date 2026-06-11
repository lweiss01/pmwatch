import json
import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import keyword_matcher as km

FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__), "fixtures", "regression_cases.json"
)


def _load_cases() -> dict:
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


class TestRegressionFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = _load_cases()

    def test_kxfed_rate_decision_accept(self):
        case = self.cases["kxfed_rate_decision_accept"]
        exp = km.explain_for_correlation(
            case["series"],
            case["title"],
            case["description"],
            source=case["source"],
        )
        self.assertEqual(exp.decision, case["expect_decision"])
        self.assertGreaterEqual(exp.quality, case["min_quality"])

    def test_kxfed_stress_test_reject(self):
        case = self.cases["kxfed_stress_test_reject"]
        exp = km.explain_for_correlation(
            case["series"],
            case["title"],
            case["description"],
            source=case["source"],
        )
        self.assertEqual(exp.decision, "reject")
        self.assertEqual(exp.reject_reason, case["expect_reject_reason"])

    def test_colin_powell_blocklist(self):
        case = self.cases["colin_powell_blocklist"]
        exp = km.explain_for_correlation(
            case["series"],
            case["title"],
            case["description"],
            source=case["source"],
        )
        self.assertEqual(exp.decision, "reject")

    def test_post_hoc_negation(self):
        case = self.cases["post_hoc_negation"]
        exp = km.explain_for_correlation(
            case["series"],
            case["title"],
            case["description"],
            source=case.get("source"),
        )
        self.assertEqual(exp.decision, case["expect_decision"])


if __name__ == "__main__":
    unittest.main()
