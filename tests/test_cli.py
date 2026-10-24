import sys
import types
import unittest

sys.modules.setdefault("pyautogui", types.ModuleType("pyautogui"))
from gui_agents.s3 import cli_app  # noqa: E402

SAVED = {
    "provider": "anthropic",
    "model": "claude-sonnet-5-5",
    "model_api_key": "sk-ant",
    "enable_local_env": True,
}


def run(*argv):
    _, args = cli_app.parse_args(list(argv))
    return cli_app.apply_config(args, SAVED)


class TestCli(unittest.TestCase):
    def test_saved_setup_fills_unset_flags(self):
        args = run("open", "a", "tab")
        self.assertEqual(args.task, "open a tab")
        self.assertEqual(
            (args.provider, args.model), ("anthropic", "claude-sonnet-5-5")
        )
        self.assertTrue(args.enable_local_env)
        self.assertEqual(args.max_steps, 15)

    def test_another_provider_does_not_inherit_the_saved_model_or_key(self):
        args = run("--provider", "openai", "x")
        self.assertEqual(args.model, "gpt-5-2025-08-07")
        self.assertEqual(args.model_api_key, "")

    def test_saved_switches_can_be_turned_off(self):
        self.assertFalse(run("--no-enable_local_env", "x").enable_local_env)

    def test_setup_word(self):
        _, args = cli_app.parse_args(["setup"])
        self.assertTrue(args.run_setup)


if __name__ == "__main__":
    unittest.main()
