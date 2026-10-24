import sys
import time
import types
import unittest
from unittest.mock import patch

from PIL import Image

sys.modules.setdefault("pyautogui", types.ModuleType("pyautogui"))
from gui_agents.s3.utils import screen  # noqa: E402


class TestSettle(unittest.TestCase):
    def test_returns_as_soon_as_the_screen_stops_changing(self):
        frames = iter([Image.new("RGB", (200, 100), c) for c in ("red", "blue")])
        still = Image.new("RGB", (200, 100), "blue")
        with patch.object(screen, "capture", lambda: next(frames, still)):
            started = time.time()
            screen.settled_screen((100, 50), max_wait=2.0)
            self.assertLess(time.time() - started, 0.5)

    def test_gives_up_at_max_wait_on_a_moving_screen(self):
        colors = iter(range(10_000))
        moving = lambda: Image.new("RGB", (200, 100), (next(colors) % 256, 0, 0))
        with patch.object(screen, "capture", moving):
            started = time.time()
            screen.settled_screen((100, 50), max_wait=0.4)
            self.assertLess(time.time() - started, 0.7)


if __name__ == "__main__":
    unittest.main()
