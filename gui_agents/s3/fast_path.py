"""Run the plain steps at the start of a task without asking a model.

"open chrome and search for flights to Tokyo" needs no screenshot to start:
open Chrome, focus the address bar, type, press Enter. Clauses are matched in
order; the first one that isn't plain (or needs a screen we can't assume)
ends the fast path, and it and everything after go to the model.
"""

import re
from typing import List, NamedTuple, Tuple

BROWSERS = {
    "chrome",
    "google chrome",
    "safari",
    "firefox",
    "edge",
    "microsoft edge",
    "brave",
    "arc",
    "opera",
    "chromium",
}

KEYS = {
    "cmd": "command",
    "command": "command",
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "option": "option",
    "opt": "option",
    "shift": "shift",
    "win": "win",
    "enter": "enter",
    "return": "enter",
    "esc": "escape",
    "escape": "escape",
    "tab": "tab",
    "space": "space",
    "backspace": "backspace",
    "delete": "delete",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "home": "home",
    "end": "end",
    "pageup": "pageup",
    "pagedown": "pagedown",
}
KEYS.update({f"f{i}": f"f{i}" for i in range(1, 13)})

VERBS = r"open|launch|start|go to|visit|navigate to|search|google|look up|type|press|hit|wait|scroll|new tab"
# Clauses split at "and", "then", "," or ";" only before a verb, so
# "search for salt and pepper" stays one clause
SPLIT = re.compile(
    rf"\s*(?:,\s*and then|,\s*then|,\s*and|\band then|\bthen|\band|,|;)\s+(?=(?:{VERBS})\b)",
    re.IGNORECASE,
)
# Words that make "open X" something other than an app name
NOT_APP = {
    "a", "an", "the", "my", "this", "that", "new", "tab", "window", "file",
    "folder", "document", "in", "with", "on", "from", "to", "and", "of", "for",
}  # fmt: skip


class Step(NamedTuple):
    label: str
    code: str
    settle: float  # most seconds to let the screen settle afterwards


def _keys(text: str):
    names = [k for k in re.split(r"[\s+]+", text.lower()) if k]
    keys = []
    for name in names:
        if name in KEYS:
            keys.append(KEYS[name])
        elif len(name) == 1 and name.isalnum():
            keys.append(name)
        else:
            return None
    return keys or None


def _app(phrase: str):
    """The app name in "open <phrase>", or None if the phrase isn't one."""
    m = re.fullmatch(
        r"(?:up\s+)?(?:the\s+)?(.+?)(?:\s+app(?:lication)?)?", phrase.lower()
    )
    words = m.group(1).split()
    if "." in m.group(1) or len(words) > 3 or NOT_APP & set(words):
        return None
    return m.group(1)


def plan(task: str, aci, platform: str) -> Tuple[List[Step], str]:
    """Steps to run now, and the part of the task left for the model ("" if
    none). aci builds the pyautogui code, so platforms behave as in a model
    step."""
    mod = "command" if platform == "darwin" else "ctrl"
    text = task.strip().rstrip(".!")
    text = re.sub(r"^(?:please|can you|could you)\s+", "", text, flags=re.IGNORECASE)
    starts, ends = [0], []
    for sep in SPLIT.finditer(text):
        ends.append(sep.start())
        starts.append(sep.end())
    ends.append(len(text))
    clauses = [text[a:b] for a, b in zip(starts, ends)]
    steps: List[Step] = []
    in_browser = False

    def address_bar(value, label):
        steps.append(Step(label, aci.hotkey([mod, "l"]), 0.5))
        steps.append(Step(label, aci.type(text=value, enter=True), 1.5))

    for i, clause in enumerate(clauses):
        c = clause.strip()
        low = c.lower()
        m = re.fullmatch(r"(?:open|launch|start)\s+(.+)", low)
        app = _app(m.group(1)) if m else None
        if app:
            steps.append(Step(f"open {app}", aci.open(app), 3.0))
            in_browser = app in BROWSERS
            continue
        if in_browser and re.fullmatch(
            r"(?:open\s+)?(?:a\s+)?new\s+(?:browser\s+)?tab", low
        ):
            steps.append(Step("new tab", aci.hotkey([mod, "t"]), 0.5))
            continue
        m = re.fullmatch(
            r"(?:go to|visit|navigate to|open|load)\s+(\S+\.\S+)", c, re.IGNORECASE
        )
        if m and in_browser:
            address_bar(m.group(1), f"go to {m.group(1)}")
            continue
        m = re.fullmatch(
            r"(?:search(?:\s+for)?|google|look up)\s+(.+)", c, re.IGNORECASE
        )
        # "search for X on Amazon" means a site's own search: leave it to the model
        if m and in_browser and not re.search(r"\s(?:on|in|at)\s", m.group(1)):
            address_bar(m.group(1), f"search for {m.group(1)}")
            continue
        m = re.fullmatch(r"type\s+[\"“'](.+)[\"”']", c, re.IGNORECASE)
        if m:
            steps.append(Step(f"type {m.group(1)!r}", aci.type(text=m.group(1)), 0.3))
            continue
        m = re.fullmatch(r"(?:press|hit)\s+(?:the\s+)?(.+?)(?:\s+keys?)?", low)
        keys = _keys(m.group(1)) if m else None
        if keys:
            steps.append(Step(f"press {'+'.join(keys)}", aci.hotkey(keys), 1.0))
            continue
        m = re.fullmatch(
            r"wait(?:\s+for)?\s+(\d+(?:\.\d+)?)\s*(?:s|secs?|seconds?)?", low
        )
        if m:
            steps.append(Step(f"wait {m.group(1)}s", aci.wait(float(m.group(1))), 0.3))
            continue
        m = re.fullmatch(r"scroll\s+(up|down)", low)
        if m:
            clicks = 5 if m.group(1) == "up" else -5
            code = f"import pyautogui; pyautogui.scroll({clicks})"
            steps.append(Step(f"scroll {m.group(1)}", code, 0.5))
            continue
        # "open spotify and play my liked songs": open it here, the rest is the model's
        m = re.match(
            r"(?:open|launch|start)\s+(.+?)\s*,?\s+(?:and then|then|and)\s+(.+)",
            c,
            re.IGNORECASE,
        )
        app = _app(m.group(1)) if m else None
        if app:
            steps.append(Step(f"open {app}", aci.open(app), 3.0))
            return steps, text[starts[i] + m.start(2) :]
        return steps, text[starts[i] :]
    return steps, ""
