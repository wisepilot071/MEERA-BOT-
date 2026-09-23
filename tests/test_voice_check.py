import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import voice_check  # noqa: E402
from linkedin import escape_commentary  # noqa: E402

GOOD = ("Most vitamin C serums change colour within a few months, and the usual explanation stops one step "
        "too early. The ingredient oxidises when exposed to light, heat and air. ") * 12


class AutofixTests(unittest.TestCase):
    def test_dashes(self):
        t, _ = voice_check.autofix("It works—mostly. Keep 4–6 months.")
        self.assertEqual(t, "It works - mostly. Keep 4-6 months.")

    def test_exclamation_and_emoji(self):
        t, fixes = voice_check.autofix("This matters! \U0001F680")
        self.assertEqual(t, "This matters.")
        self.assertTrue(fixes)

    def test_ellipsis_preserved(self):
        t, _ = voice_check.autofix("Wait...")
        self.assertEqual(t, "Wait...")

    def test_spelling(self):
        t, _ = voice_check.autofix("Color changes. We optimized and standardized the Behavior.")
        self.assertEqual(t, "Colour changes. We optimised and standardised the Behaviour.")

    def test_trailing_hashtags_removed(self):
        t, _ = voice_check.autofix("Body text.\n\n#skincare #founder")
        self.assertEqual(t, "Body text.")


    def test_contractions(self):
        t, _ = voice_check.autofix("The retailer was not pleased. If a brand cannot answer, Do not guess.")
        self.assertEqual(t, "The retailer wasn't pleased. If a brand can't answer, Don't guess.")

    def test_between_range_and_i_am(self):
        t, _ = voice_check.autofix("Evidence sits between 2% and 5%. I am not saying more.")
        self.assertEqual(t, "Evidence sits 2-5%. I'm not saying more.")

    def test_ranges(self):
        t, _ = voice_check.autofix("A test on 20 to 30 people. Returns went from 9 to 3.")
        self.assertEqual(t, "A test on 20-30 people. Returns went from 9 to 3.")


class CheckTests(unittest.TestCase):
    def test_filler(self):
        r = voice_check.check(GOOD + " It is very, really, quite significant and considerably so.")
        self.assertTrue(any("filler" in v for v in r.violations))

    def test_misleading(self):
        r = voice_check.check(GOOD + " The label is misleading.")
        self.assertTrue(any("misleading" in v for v in r.violations))

    def test_clean_post_passes(self):
        self.assertTrue(voice_check.check(GOOD).ok)

    def test_banned_words(self):
        r = voice_check.check(GOOD + " This is a game-changing breakthrough.")
        self.assertTrue(any("banned" in v for v in r.violations))

    def test_bullets_and_bait(self):
        r = voice_check.check(GOOD + "\n\n- first point\n- second\n\nAgree?")
        self.assertTrue(any("bullet" in v for v in r.violations))
        self.assertTrue(any("bait" in v for v in r.violations))

    def test_placeholders(self):
        r = voice_check.check(GOOD + " Returns fell by [X%].")
        self.assertEqual(r.placeholders, ["[X%]"])

    def test_too_short(self):
        self.assertTrue(any("short" in v for v in voice_check.check("Short post.").violations))


class LinkedInEscapeTests(unittest.TestCase):
    def test_escape(self):
        self.assertEqual(escape_commentary("pH (3.5) @ 25C"), r"pH \(3.5\) \@ 25C")


if __name__ == "__main__":
    unittest.main()
