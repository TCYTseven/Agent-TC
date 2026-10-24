"""What each engine sends, checked against a fake client."""

import unittest
from unittest.mock import MagicMock

from gui_agents.s3.core.mllm import LMMAgent


def sent(engine_params, system="sys"):
    agent = LMMAgent(dict(engine_params, api_key="x"), system)
    agent.add_message("hi", role="user")
    client = MagicMock()
    agent.engine.llm_client = client
    if engine_params["engine_type"] == "anthropic":
        block = MagicMock(type="text", text="ok")
        client.messages.create.return_value.content = [block]
        agent.get_response()
        return client.messages.create.call_args.kwargs
    agent.get_response()
    return client.chat.completions.create.call_args.kwargs


class TestEngines(unittest.TestCase):
    def test_no_token_cap_unless_asked(self):
        kw = sent({"engine_type": "gemini", "model": "gemini-2.5-pro"})
        self.assertNotIn("max_tokens", kw)
        kw = sent({"engine_type": "gemini", "model": "g", "max_tokens": 99})
        self.assertEqual(kw["max_tokens"], 99)

    def test_reasoning_effort_only_for_reasoning_models(self):
        kw = sent(
            {"engine_type": "openai", "model": "gpt-5", "reasoning_effort": "low"}
        )
        self.assertEqual(kw["reasoning_effort"], "low")
        self.assertNotIn("temperature", kw)
        for model in ("gpt-5-chat-latest", "o1-mini", "gpt-4o"):
            kw = sent(
                {"engine_type": "openai", "model": model, "reasoning_effort": "low"}
            )
            self.assertNotIn("reasoning_effort", kw, model)
        kw = sent({"engine_type": "openai", "model": "gpt-5"})
        self.assertNotIn("reasoning_effort", kw)  # API default when unset

    def test_anthropic_accepts_plain_string_system(self):
        agent = LMMAgent({"engine_type": "anthropic", "model": "m", "api_key": "x"})
        agent.messages[0]["content"] = "judge prompt"
        agent.add_message("hi", role="user")
        client = MagicMock()
        client.messages.create.return_value.content = [
            MagicMock(type="text", text="ok")
        ]
        agent.engine.llm_client = client
        self.assertEqual(agent.get_response(), "ok")
        self.assertEqual(
            client.messages.create.call_args.kwargs["system"][0]["text"], "judge prompt"
        )

    def test_thinking_defaults(self):
        from tests.test_step import build

        agent, _ = build(["x"])
        self.assertFalse(agent.executor.use_thinking)
        from gui_agents.s3.agents.worker import Worker

        w = Worker(
            {
                "engine_type": "anthropic",
                "model": "claude-sonnet-4-5-20250929",
                "api_key": "x",
            },
            agent.executor.grounding_agent,
        )
        self.assertTrue(w.use_thinking)  # matches the published OSWorld runs
        w = Worker(
            {
                "engine_type": "openai",
                "model": "gpt-4o",
                "api_key": "x",
                "thinking": True,
            },
            agent.executor.grounding_agent,
        )
        self.assertFalse(w.use_thinking)  # no thinking API: ignored, not broken


if __name__ == "__main__":
    unittest.main()
