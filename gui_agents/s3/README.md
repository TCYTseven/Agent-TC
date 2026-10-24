<h1 align="center">
  <img src="../../images/agent_s.png" alt="Logo" style="vertical-align:middle" width="60"> Agent S3:
  <small>Scaling Agents for Computer Use</small>
</h1>

<p align="center">
  🌐 <a href="https://www.simular.ai/articles/agent-s3">[Blog]</a>
  📄 <a href="https://arxiv.org/abs/2510.02250">[Paper (TMLR 2026)]</a>
  🎥 <a href="https://www.youtube.com/watch?v=VHr0a3UBsh4">[Video]</a>
  🗨️ <a href="https://discord.gg/E2XfsK9fPV">[Discord]</a>
</p>

Agent S3 is the agent in the `gui-agents` package. It takes a natural-language task, looks at the screen, and completes the task by clicking, typing and scrolling in ordinary desktop and web applications. It runs on macOS, Windows and Linux.

## 🎯 Results

<p align="center">
  <img src="../../images/s3_results_new.png" alt="Agent S3 Results" width="700"/>
</p>

On OSWorld, Agent S3 alone reaches 66% in the 100-step setting. With Behavior Best-of-N it reaches 72.6%, above human performance on OSWorld (about 72%). On WindowsAgentArena, accuracy rises from 50.2% with Agent S3 alone to 56.6% when selecting from 3 rollouts; on AndroidWorld, from 68.1% to 71.6%.

These results were measured with reflection on and the original verbose prompt. The `osworld_setup/s3` scripts still turn reflection on, keep extended thinking for the Claude models the paper used, and leave reasoning effort at the API default; the CLI defaults are tuned for speed (see [Speed](#-speed)).

## 🛠️ Install

```bash
pip install -e .          # from a clone, or: pip install gui-agents
brew install tesseract    # or apt-get install tesseract-ocr; used to select text
```

Requirements: a single monitor, and care. The agent runs Python on your computer to move the mouse and type.

Set the key for the provider you use:

```bash
export OPENAI_API_KEY=...      # or ANTHROPIC_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY, HF_TOKEN
```

Every provider and its variables are in [models.md](../../models.md).

## 🚀 Run

**Fastest setup (one API key, nothing to host).** The main model does its own grounding:

```bash
agent_s --provider anthropic --model claude-sonnet-4-5 --task "Open a new browser tab"
```

**Most accurate setup.** A dedicated grounding model, as in the paper. [UI-TARS-1.5-7B](https://huggingface.co/ByteDance-Seed/UI-TARS-1.5-7B) on a Hugging Face endpoint, vLLM or Ollama:

```bash
agent_s \
    --provider openai --model gpt-5-2025-08-07 \
    --ground_provider huggingface --ground_url http://localhost:8080 \
    --ground_model ui-tars-1.5-7b --grounding_width 1920 --grounding_height 1080
```

Dedicated grounding models are trained to answer "where is this element?" with a pixel. General chat models are much weaker at it on hard, dense screens, so for precise clicking keep UI-TARS. Grounding with the main model saves a second server and works well on ordinary screens.

**Fully local.** Both models can run on your machine through any OpenAI-compatible server (vLLM, Ollama, LM Studio, llama.cpp). For example with vLLM serving UI-TARS for grounding and Ollama serving a vision model for planning:

```bash
vllm serve ByteDance-Seed/UI-TARS-1.5-7B --port 8000
agent_s \
    --provider ollama --model qwen2.5vl:7b --model_url http://localhost:11434 \
    --ground_provider vllm --ground_url http://localhost:8000/v1 \
    --ground_model ByteDance-Seed/UI-TARS-1.5-7B --grounding_width 1920 --grounding_height 1080
```

Grounding dimensions must match your grounding model's output resolution: UI-TARS-1.5-7B uses 1920x1080, UI-TARS-72B uses 1000x1000. Without `--ground_provider` they are worked out for you (for Claude, the screenshot is also kept under the 1.15 megapixels it reads at full size). Some models, Gemini among them, tend to answer in 0-1000 normalised coordinates; with those, use a grounding model.

Ctrl+C pauses the agent; Ctrl+C again quits, Esc resumes.

### Options

| Flag | Default | What it does |
|---|---|---|
| `--provider`, `--model` | `openai`, `gpt-5-2025-08-07` | Main (planning) model |
| `--model_url`, `--model_api_key` | | Custom endpoint and key |
| `--ground_provider`, `--ground_url`, `--ground_model`, `--grounding_width`, `--grounding_height` | unset | Dedicated grounding model. Omit all of them to ground with the main model |
| `--max_steps` | 15 | Steps before giving up |
| `--settle` | 0.5 | Seconds to wait after an action before the next screenshot |
| `--max_trajectory_length` | 3 | Screenshots kept in context |
| `--image_max_side` | 1280 | Longest side of screenshots sent to the main model (0 = full size) |
| `--reasoning_effort` | `low` | OpenAI reasoning models (gpt-5, o-series) |
| `--thinking` | off | Anthropic extended thinking: slower, sometimes more accurate (`--provider anthropic` only) |
| `--enable_reflection` | off | A second model call per step that checks the trajectory: slower, catches loops |
| `--model_temperature` | | Fix the temperature (o3 needs 1.0) |
| `--enable_local_env` | off | Let the code agent run Python and Bash on this computer |
| `--debug` | off | Debug logging (logs go to `logs/`) |
| `--task` | | The task; omit for an interactive prompt |

`--enable_local_env` executes arbitrary code with your permissions; Bash snippets time out after 30 seconds and Python after 60. Only use it in a trusted environment.

## ⚡ Speed

Where a step's time goes, and what this version does about each:

| Per step | Before | Now |
|---|---|---|
| Planner calls | 1 | 1 |
| Reflection calls | 1 | 0 (opt-in) |
| Grounding calls for one click | 2 (the format check grounded the action, then it was grounded again to run it) | 1 |
| Grounding calls for a drag | 4, one after another | 2, at the same time |
| Planner output | four paragraphs of analysis | one sentence per section |
| Fixed sleeps around an action | 2.0s plus pyautogui's 0.1s per call | `--settle` (0.5s), 0.02s per call |
| Screenshot | PNG, full size to every model | JPEG, encoded once, 1280px to the planner |
| Screenshots in context | 8 | 3 |
| API clients | Anthropic client rebuilt on every call | one client per model, reused |
| Anthropic system prompt | re-read every step | cached |
| OpenAI reasoning models | default effort | `low` effort in the CLI |
| Failed requests | a 400 retried for 60s, three times over | retried only on rate limits, server errors and dropped connections |
| Code agent | one extra model call to summarise each run | none |

## `gui_agents` SDK

```python
import io
import pyautogui
from gui_agents.s3.agents.agent_s import AgentS3
from gui_agents.s3.agents.grounding import OSWorldACI
from gui_agents.s3.utils.local_env import LocalEnv  # optional, for the code agent

engine_params = {
    "engine_type": "anthropic",
    "model": "claude-sonnet-4-5",
    "image_max_side": 1280,  # optional: shrink screenshots for this model
}
# A dedicated grounding model; or reuse engine_params and give the size of
# the image the model sees as grounding_width/height.
engine_params_for_grounding = {
    "engine_type": "huggingface",
    "model": "ui-tars-1.5-7b",
    "base_url": "http://localhost:8080",
    "grounding_width": 1920,
    "grounding_height": 1080,
}

grounding_agent = OSWorldACI(
    env=None,  # LocalEnv() to enable the code agent
    platform="linux",  # "darwin", "windows"
    engine_params_for_generation=engine_params,
    engine_params_for_grounding=engine_params_for_grounding,
    width=1920,
    height=1080,
)
agent = AgentS3(engine_params, grounding_agent, platform="linux")

buffer = io.BytesIO()
pyautogui.screenshot().save(buffer, format="JPEG")
info, action = agent.predict(instruction="Close VS Code", observation={"screenshot": buffer.getvalue()})
exec(action[0])
```

[`cli_app.py`](cli_app.py) has the full loop.

## OSWorld

See the [OSWorld deployment instructions](../../osworld_setup/s3/OSWorld.md).
