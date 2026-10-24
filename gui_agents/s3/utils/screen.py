"""Screen capture, and waiting for the screen to settle after an action."""

import time

import pyautogui
from PIL import Image

from gui_agents.s3.utils.images import jpeg_bytes


def capture() -> Image.Image:
    """The whole screen pyautogui clicks on. mss is several times faster than
    pyautogui's screenshot on macOS and Linux; pyautogui is the fallback."""
    try:
        import mss

        with mss.mss() as sct:
            shot = sct.grab(sct.monitors[1])
        # Use it only if it has the shape of the screen pyautogui clicks on
        # (with a second monitor the primary one alone does not); retina
        # captures are 2x, which keeps the shape.
        w, h = pyautogui.size()
        if abs(shot.size[0] / shot.size[1] - w / h) < 0.01:
            return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    except Exception:
        pass
    return pyautogui.screenshot()


def encode(img: Image.Image, size) -> bytes:
    if img.size != tuple(size):
        img = img.resize(size, Image.BILINEAR)
    return jpeg_bytes(img)


def grab_screen(size) -> bytes:
    """Screenshot at `size` as JPEG bytes."""
    return encode(capture(), size)


def _signature(img: Image.Image) -> bytes:
    return img.convert("L").resize((96, 54), Image.BILINEAR).tobytes()


def settled_screen(size, max_wait: float = 1.0, min_wait: float = 0.3) -> bytes:
    """Wait until two captures in a row look the same (or max_wait passes) and
    return the last one: most actions settle well before a fixed sleep would
    end, and slow ones get more time than it would give."""
    started = time.time()
    time.sleep(min_wait)
    img = capture()
    sig = _signature(img)
    while time.time() - started < max_wait:
        time.sleep(0.05)
        nxt = capture()
        nsig = _signature(nxt)
        img = nxt
        if nsig == sig:
            break
        sig = nsig
    return encode(img, size)
