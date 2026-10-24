# Agent TC

Tell your computer what to do in plain English. Agent TC looks at the screen and does it: it clicks, types and scrolls in ordinary apps and websites, on macOS, Windows and Linux.

```bash
pip install git+https://github.com/TCYTseven/Agent-TC
agent-tc "open a new browser tab and search for flights to Tokyo"
```

The first run asks a few questions with arrow-key menus, then remembers the answers:

```
? Which model provider?
  ❯ Anthropic (Claude)
    OpenAI (GPT)
    Google (Gemini)
    OpenRouter (any model)
    Local with Ollama (free, private)
    Another OpenAI-compatible server (vLLM, LM Studio, ...)
? Which model?
? Let it run Python and Bash on this computer for data tasks?
```

After that, every run is one line. `agent-tc setup` asks again. Run `agent-tc` with no task to type tasks one after another.

Ctrl+C pauses; Ctrl+C again quits, Esc resumes.

## Before you start

- Python 3.9 or newer.
- An API key for your provider (the setup asks for it if it isn't in your environment), or [Ollama](https://ollama.com) for a fully local run.
- Optional: [Tesseract](https://github.com/tesseract-ocr/tesseract) (`brew install tesseract` or `apt install tesseract-ocr`), used only to select text precisely.
- One monitor works best.

The agent controls your mouse and keyboard. Watch it on anything that matters.

## How it clicks

The model reads the screenshot and writes the pixel to act on straight into the action, `agent.click(412, 230)`, which is scaled to your screen and run with pyautogui. There is no separate grounding model, so each step is a single model call.

Plain steps at the start of a task skip the model entirely. In `agent-tc "open chrome and search for flights to Tokyo"`, opening Chrome and typing the search into the address bar happen locally, with no model call at all. A task like "open spotify and play my liked songs" opens Spotify locally and hands the model the rest. Recognised steps: open an app, a new tab or a URL or search once a browser is open, type "quoted text", press keys (`press cmd+shift+4`), wait, scroll up or down. Anything else goes to the model.

Fully local, nothing leaves your computer:

```bash
ollama pull qwen2.5vl:7b
agent-tc --provider ollama --model qwen2.5vl:7b --model_url http://localhost:11434 "open the calculator"
```

## Options

Flags override the saved setup for one run; on/off flags also take a `--no-` form (`--no-enable_local_env`).

| Flag | Default | What it does |
|---|---|---|
| `--provider`, `--model` | from setup | Which model plans each step |
| `--max_steps` | 15 | Steps before it gives up |
| `--settle` | 1.0 | Most seconds to wait for the screen to stop changing after an action |
| `--image_max_side` | 1280 | Screenshot size sent to the model (0 = full size) |
| `--fast_path` | on | Run plain steps like "open chrome" without a model call |
| `--normalized_coords` | on for Gemini | The model answers in 0-1000 coordinates instead of pixels; turn on if clicks land off with another model |
| `--enable_reflection` | off | A second model call per step that checks progress: slower, catches loops |
| `--thinking` | off | Claude extended thinking: slower, sometimes more accurate |
| `--reasoning_effort` | low | OpenAI reasoning models |
| `--enable_local_env` | from setup | Let it run Python and Bash for data tasks |
| `--debug` | off | Detailed logs in `logs/` |

Every provider and its settings: [models.md](./models.md).

## Why it's fast

Each step takes a screenshot, asks one model for the next action, and does it. What's been cut from that loop:

- **No model for plain steps.** "Open X", a search or a URL in a browser, quoted typing and key presses run locally before the model is asked anything.
- **One model call per step.** The model writes the click position itself, so there is no grounding call.
- **Short prompts that fit the task.** About 450 tokens of instructions instead of about 1,900. Actions a task doesn't call for (dragging, holding keys, notes, switching apps) are left out unless its wording asks for them.
- **Short answers.** One line of reasoning, then an action like `agent.click(412, 230)` instead of a sentence describing the element. Output tokens are the slowest part of a call.
- **No reflection call** by default. Each answer starts by checking the previous step.
- **No fixed sleeps.** After an action it waits only until the screen stops changing, and that last frame becomes the next screenshot.
- **Small screenshots, captured at the size the model sees.** Taken with `mss`, scaled once, saved as JPEG, 3 kept in context.
- **Warm connections.** One shared API client per provider, opened while the first screenshot is taken. Claude's instructions are cached between steps, and OpenAI reasoning models run at low effort.

## Use it from Python

```python
import io
import pyautogui
from gui_agents.s3.agents.agent_s import AgentS3
from gui_agents.s3.agents.grounding import PixelACI

screen_w, screen_h = pyautogui.size()
scale = 1280 / max(screen_w, screen_h)
shot_w, shot_h = round(screen_w * scale), round(screen_h * scale)

model = {"engine_type": "anthropic", "model": "claude-sonnet-5-5"}
# The model answers in pixels of the screenshot it sees
aci = PixelACI(None, "darwin", model, screen_w, screen_h, shot_w, shot_h)
agent = AgentS3(model, aci, platform="darwin")

shot = io.BytesIO()
pyautogui.screenshot().resize((shot_w, shot_h)).convert("RGB").save(shot, format="JPEG")
info, actions = agent.predict(instruction="Close the browser", observation={"screenshot": shot.getvalue()})
if actions[0] not in ("DONE", "FAIL", "WAIT", "NEXT"):
    exec(actions[0])
```

[`gui_agents/s3/cli_app.py`](./gui_agents/s3/cli_app.py) has the full loop.

## Benchmarks

`osworld_setup/s3` runs the agent on [OSWorld](https://os-world.github.io) with the settings the published results used: reflection and extended thinking on, and a UI-TARS grounding model (`OSWorldACI`), which the benchmark still uses. See its [instructions](./osworld_setup/s3/OSWorld.md).

## Credits

Built on [Agent S](https://github.com/simular-ai/Agent-S) by Simular AI, under the Apache 2.0 license.
