import importlib.util
from pathlib import Path
import unittest

MODULE_PATH = Path(__file__).resolve().parents[1] / "agent" / "discover.py"
spec = importlib.util.spec_from_file_location("discover", MODULE_PATH)
discover = importlib.util.module_from_spec(spec)
spec.loader.exec_module(discover)

class ClassificationTests(unittest.TestCase):
    def test_skill_badge_explicit_label(self):
        self.assertEqual(discover.classify("Public image text: SKILL BADGE")[0], "skill")

    def test_game_badge_explicit_label(self):
        self.assertEqual(discover.classify("ARCADE GAME")[0], "game")
        self.assertEqual(discover.classify("GAME BADGE")[0], "game")

    def test_completion_badge_separate(self):
        self.assertEqual(discover.classify("COMPLETION BADGE")[0], "completion")

    def test_ambiguous_label_stays_unknown(self):
        self.assertEqual(discover.classify("Arcade points 1.0; badge art")[0], "unknown")

    def test_special_plus_two_is_not_a_category(self):
        category, verified, *_ = discover.classify("Special event +2 points")
        self.assertEqual(category, "unknown")
        self.assertFalse(verified)

if __name__ == "__main__":
    unittest.main()
