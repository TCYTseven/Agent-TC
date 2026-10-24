"""One agent step against fake engines: counts model calls and checks the
grounded action that comes out."""

import io
import unittest

from PIL import Image

from gui_agents.s3.agents.agent_s import AgentS3
from gui_agents.s3.agents.grounding import OSWorldACI


class FakeEngine:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def generate(self, messages, **kwargs):
        self.calls += 1
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def screenshot():
    out = io.BytesIO()
    Image.new("RGB", (1000, 500), "white").save(out, format="JPEG")
    return out.getvalue()


def build(plan_replies, ground_replies="(500, 250)", reflection=False):
    params = {"engine_type": "openai", "model": "gpt-4o", "api_key": "x"}
    ground = dict(params, grounding_width=1000, grounding_height=500)
    aci = OSWorldACI(None, "linux", params, ground, width=2000, height=1000)
    aci.grounding_model.engine = FakeEngine([ground_replies])
    agent = AgentS3(params, aci, platform="linux", enable_reflection=reflection)
    agent.executor.generator_agent.engine = FakeEngine(plan_replies)
    if reflection:
        agent.executor.reflection_agent.engine = FakeEngine(["Case 2: continue."])
    return agent, aci


CLICK = 'Click it.\n```python\nagent.click("The OK button", 1, "left")\n```'


class TestStep(unittest.TestCase):
    def test_one_planner_call_and_one_grounding_call_per_click(self):
        agent, aci = build([CLICK])
        info, actions = agent.predict("press ok", {"screenshot": screenshot()})
        self.assertEqual(agent.executor.generator_agent.engine.calls, 1)
        self.assertEqual(aci.grounding_model.engine.calls, 1)
        self.assertIn("pyautogui.click(1000, 500", actions[0])

    def test_bad_format_is_retried(self):
        agent, aci = build(["no code here", CLICK])
        _, actions = agent.predict("press ok", {"screenshot": screenshot()})
        self.assertEqual(agent.executor.generator_agent.engine.calls, 2)
        self.assertIn("pyautogui.click", actions[0])

    def test_drag_grounds_both_ends(self):
        drag = '```python\nagent.drag_and_drop("file A", "folder B")\n```'
        agent, aci = build([drag])
        _, actions = agent.predict("move", {"screenshot": screenshot()})
        self.assertEqual(aci.grounding_model.engine.calls, 2)
        self.assertIn("dragTo", actions[0])

    def test_reflection_is_off_by_default_and_opt_in(self):
        agent, _ = build([CLICK])
        self.assertIsNone(agent.executor.reflection_agent)
        agent, _ = build([CLICK], reflection=True)
        obs = {"screenshot": screenshot()}
        agent.predict("press ok", obs)
        agent.predict("press ok", obs)
        self.assertEqual(agent.executor.reflection_agent.engine.calls, 1)

    def test_old_screenshots_are_dropped(self):
        agent, _ = build([CLICK])
        agent.executor.max_trajectory_length = 2
        for _ in range(5):
            agent.predict("press ok", {"screenshot": screenshot()})
        images = sum(
            "image" in part["type"]
            for m in agent.executor.generator_agent.messages
            for part in m["content"]
        )
        self.assertEqual(images, 2)


if __name__ == "__main__":
    unittest.main()
