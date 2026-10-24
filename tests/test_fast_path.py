import sys
import types
import unittest

sys.modules.setdefault("pyautogui", types.ModuleType("pyautogui"))
from gui_agents.s3 import fast_path  # noqa: E402
from gui_agents.s3.agents.grounding import PixelACI  # noqa: E402

ACI = PixelACI(
    None,
    "darwin",
    {"engine_type": "openai", "model": "m", "api_key": "x"},
    1000,
    500,
    1000,
    500,
)


def plan(task):
    steps, rest = fast_path.plan(task, ACI, "darwin")
    return list(dict.fromkeys(s.label for s in steps)), rest


class TestFastPath(unittest.TestCase):
    def test_a_plain_task_needs_no_model(self):
        self.assertEqual(
            plan("Open Chrome and search for flights to Tokyo."),
            (["open chrome", "search for flights to Tokyo"], ""),
        )
        self.assertEqual(plan("open the calculator"), (["open calculator"], ""))
        self.assertEqual(
            plan('type "hello" and press cmd+s'),
            (["type 'hello'", "press command+s"], ""),
        )

    def test_the_rest_goes_to_the_model(self):
        self.assertEqual(
            plan("open spotify and play my liked songs"),
            (["open spotify"], "play my liked songs"),
        )
        self.assertEqual(
            plan("open chrome and search for shoes on amazon"),
            (["open chrome"], "search for shoes on amazon"),
        )

    def test_and_inside_a_clause_is_not_a_split(self):
        self.assertEqual(
            plan("open safari then search for salt and pepper"),
            (["open safari", "search for salt and pepper"], ""),
        )

    def test_nothing_runs_without_a_known_screen(self):
        for task in (
            "search for salt and pepper",  # no browser opened
            "open a new browser tab and search for flights",
            "open report.pdf in preview",
            "open the file budget.xlsx",
            "type a summary of this page",
            "press the big red button",
        ):
            self.assertEqual(plan(task), ([], task), task)

    def test_search_uses_the_address_bar(self):
        steps, _ = fast_path.plan("open chrome and search for cats", ACI, "darwin")
        self.assertIn("'command', 'l'", steps[1].code)
        self.assertIn("write('cats'", steps[2].code)
        self.assertIn("press('enter')", steps[2].code)


if __name__ == "__main__":
    unittest.main()
