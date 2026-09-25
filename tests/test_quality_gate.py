import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:test")
os.environ.setdefault("GEMINI_API_KEY", "test-key-for-unit-tests")

from generator import Assessment, CRITERIA_MAX, Draft  # noqa: E402

import bot  # noqa: E402


class QualityGateTests(unittest.TestCase):
    def test_score_is_summed_from_criteria(self):
        a = Assessment(criteria={"anchor": 2, "mechanism": 2, "evidence": 1, "insight": 1, "reader_value": 1, "integrity": 0})
        self.assertEqual(a.score, 7)
        self.assertTrue(a.passes(7))
        self.assertFalse(a.passes(8))

    def test_max_total_is_ten(self):
        self.assertEqual(sum(CRITERIA_MAX.values()), 10)

    def test_unavailable_never_passes(self):
        a = Assessment(criteria=dict(CRITERIA_MAX), available=False)
        self.assertFalse(a.passes(7))

    def test_decline_message_is_kind_and_specific(self):
        a = Assessment(criteria={"anchor": 0, "mechanism": 1, "evidence": 0, "insight": 1, "reader_value": 0, "integrity": 1},
                       missing=["Add the actual figure - how much did returns change?"])
        msg = bot.decline_message(a)
        self.assertIn("Thank you", msg)
        self.assertIn("3/10", msg)
        self.assertIn("not able to generate", msg)
        self.assertIn("how much did returns change", msg)
        self.assertIn("send the notes again", msg)

    def test_assessment_survives_session_roundtrip(self):
        d = Draft(notes="n", assessment=Assessment(criteria={"anchor": 2}, strengths=["s"]))
        d2 = Draft.from_dict(d.to_dict())
        self.assertEqual(d2.assessment.score, 2)
        self.assertEqual(d2.assessment.strengths, ["s"])


if __name__ == "__main__":
    unittest.main()
