# Supported models

Set `engine_type` (the CLI's `--provider` / `--ground_provider`) to one of these. Keys and URLs can be passed as `api_key` / `base_url` (`--model_api_key` / `--model_url`) instead of environment variables.

| engine_type | API key variable | URL variable (default) |
|---|---|---|
| `openai` | `OPENAI_API_KEY` | |
| `anthropic` | `ANTHROPIC_API_KEY` | |
| `gemini` | `GEMINI_API_KEY` | `GEMINI_ENDPOINT_URL` (`https://generativelanguage.googleapis.com/v1beta/openai/`) |
| `azure` | `AZURE_OPENAI_API_KEY` | `AZURE_OPENAI_ENDPOINT`, plus `OPENAI_API_VERSION` |
| `open_router` | `OPENROUTER_API_KEY` | `OPEN_ROUTER_ENDPOINT_URL` (`https://openrouter.ai/api/v1`) |
| `huggingface` | `HF_TOKEN` | `HF_ENDPOINT_URL` (a TGI endpoint) |
| `vllm` | `vLLM_API_KEY` (optional) | `vLLM_ENDPOINT_URL` |
| `ollama` | none | `OLLAMA_HOST` (`/v1` is added) |
| `deepseek` | `DEEPSEEK_API_KEY` | `DEEPSEEK_ENDPOINT_URL` (`https://api.deepseek.com`) |
| `qwen` | `QWEN_API_KEY` | `QWEN_ENDPOINT_URL` (DashScope) |
| `parasail` | `PARASAIL_API_KEY` | `PARASAIL_ENDPOINT_URL` (`https://api.parasail.io/v1`) |

Every engine except `anthropic` speaks the OpenAI Chat Completions API, so any OpenAI-compatible server (LM Studio, llama.cpp, SGLang) works as `vllm` or `openai` with a `base_url`.

Optional engine parameters:

- `image_max_side`: longest side of images sent to this model; larger screenshots are shrunk to JPEG first.
- `temperature`: forces a temperature on every call (o3 needs 1.0).
- `thinking`: Anthropic extended thinking.
- `reasoning_effort`: OpenAI reasoning models (gpt-5, o-series); unset uses the API default, the CLI sends `low`.

```python
from gui_agents.s3.core.mllm import LMMAgent

agent = LMMAgent(engine_params={"engine_type": "openai", "model": "gpt-5-2025-08-07"})
agent.add_message("Describe this screen.", image_content=screenshot_bytes, role="user")
print(agent.get_response())
```

`AgentS3` uses `LMMAgent` for every model it calls.
