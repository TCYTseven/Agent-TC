"""Model engines. One OpenAI-compatible engine covers every provider that speaks
the Chat Completions API; Anthropic has its own. Clients are created once and
reused, so every call after the first skips the TLS handshake."""

import os

import backoff
from anthropic import Anthropic
from openai import APIConnectionError, APIError, AzureOpenAI, OpenAI, RateLimitError

RETRY = backoff.on_exception(
    backoff.expo, (APIConnectionError, APIError, RateLimitError), max_time=60
)

# engine_type -> (api key env var, base url env var, default base url, default api key)
PROVIDERS = {
    "openai": ("OPENAI_API_KEY", None, None, None),
    "azure": ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", None, None),
    "gemini": (
        "GEMINI_API_KEY",
        "GEMINI_ENDPOINT_URL",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        None,
    ),
    "open_router": (
        "OPENROUTER_API_KEY",
        "OPEN_ROUTER_ENDPOINT_URL",
        "https://openrouter.ai/api/v1",
        None,
    ),
    "vllm": ("vLLM_API_KEY", "vLLM_ENDPOINT_URL", None, "EMPTY"),
    "huggingface": ("HF_TOKEN", "HF_ENDPOINT_URL", None, None),
    "parasail": (
        "PARASAIL_API_KEY",
        "PARASAIL_ENDPOINT_URL",
        "https://api.parasail.io/v1",
        None,
    ),
    "ollama": (None, "OLLAMA_HOST", None, "ollama"),
    "deepseek": (
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_ENDPOINT_URL",
        "https://api.deepseek.com",
        None,
    ),
    "qwen": (
        "QWEN_API_KEY",
        "QWEN_ENDPOINT_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        None,
    ),
}

# Providers whose URL must end in /v1
NEEDS_V1 = {"ollama", "deepseek", "qwen"}


class LMMEngine:
    def __init__(self, model=None, temperature=None, max_tokens=None, **kwargs):
        assert model is not None, "model must be provided"
        self.model = model
        self.temperature = temperature  # forces a temperature (o3 needs 1.0)
        self.max_tokens = max_tokens
        self.llm_client = None

    def _temp(self, temperature):
        return self.temperature if self.temperature is not None else temperature


class LMMEngineOpenAI(LMMEngine):
    """Any Chat Completions endpoint: OpenAI, Azure, Gemini, OpenRouter, vLLM,
    HuggingFace TGI, Parasail, Ollama, DeepSeek, Qwen."""

    def __init__(
        self,
        engine_type="openai",
        base_url=None,
        api_key=None,
        api_version=None,
        organization=None,
        azure_endpoint=None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.engine_type = engine_type
        base_url = base_url or azure_endpoint
        key_env, url_env, default_url, default_key = PROVIDERS.get(
            engine_type, PROVIDERS["openai"]
        )
        base_url = base_url or (os.getenv(url_env) if url_env else None) or default_url
        if engine_type in NEEDS_V1:
            if not base_url:
                raise ValueError(
                    f"{engine_type.capitalize()} endpoint must be provided via 'base_url' "
                    f"parameter or '{url_env}' environment variable."
                )
            if not base_url.rstrip("/").endswith("/v1"):
                base_url = base_url.rstrip("/") + "/v1"
        self.base_url = base_url
        self.api_key = (
            api_key or (os.getenv(key_env) if key_env else None) or default_key
        )
        if engine_type in ("deepseek", "qwen") and not self.api_key:
            raise ValueError(
                f"{engine_type.capitalize()} API key must be provided via 'api_key' "
                f"parameter or '{key_env}' environment variable."
            )
        self.api_version = api_version or os.getenv("OPENAI_API_VERSION")
        self.organization = organization or os.getenv("OPENAI_ORG_ID")
        # HuggingFace TGI serves a single model under the name "tgi"
        if engine_type == "huggingface":
            self.model = "tgi"

    def client(self):
        if self.llm_client is None:
            if not self.api_key:
                raise ValueError(f"No API key for {self.engine_type}")
            if self.engine_type == "azure":
                self.llm_client = AzureOpenAI(
                    azure_endpoint=self.base_url,
                    api_key=self.api_key,
                    api_version=self.api_version,
                )
            else:
                self.llm_client = OpenAI(
                    base_url=self.base_url or None,
                    api_key=self.api_key,
                    organization=self.organization,
                )
        return self.llm_client

    @RETRY
    def generate(self, messages, temperature=0.0, max_new_tokens=None, **kwargs):
        max_tokens = max_new_tokens or self.max_tokens
        if max_tokens and self.engine_type != "openai":
            kwargs["max_tokens"] = max_tokens
        elif max_tokens:
            kwargs["max_completion_tokens"] = max_tokens
        return (
            self.client()
            .chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self._temp(temperature),
                **kwargs,
            )
            .choices[0]
            .message.content
        )


def _to_anthropic(content):
    """OpenAI-style content parts -> Anthropic content blocks."""
    blocks = []
    for part in content:
        if part.get("type") == "image_url":
            header, data = part["image_url"]["url"].split(",", 1)
            mime = header[5:].split(";")[0]
            blocks.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": mime, "data": data},
                }
            )
        else:
            blocks.append(part)
    return blocks


class LMMEngineAnthropic(LMMEngine):
    def __init__(self, base_url=None, api_key=None, thinking=False, **kwargs):
        super().__init__(**kwargs)
        self.base_url = base_url
        self.api_key = api_key
        self.thinking = thinking

    def client(self):
        if self.llm_client is None:
            api_key = self.api_key or os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError(
                    "An API Key needs to be provided in either the api_key parameter "
                    "or as an environment variable named ANTHROPIC_API_KEY"
                )
            self.llm_client = Anthropic(api_key=api_key, base_url=self.base_url or None)
        return self.llm_client

    def _request(self, messages, temperature, max_new_tokens, thinking, **kwargs):
        # The system prompt is long and fixed for a task: cache it so later
        # steps skip re-reading it (lower latency on every step after the first).
        system = [
            {
                "type": "text",
                "text": messages[0]["content"][0]["text"],
                "cache_control": {"type": "ephemeral"},
            }
        ]
        msgs = [
            {"role": m["role"], "content": _to_anthropic(m["content"])}
            for m in messages[1:]
        ]
        if thinking:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": 4096}
            max_tokens = 8192
        else:
            kwargs["temperature"] = self._temp(temperature)
            max_tokens = max_new_tokens or self.max_tokens or 4096
        return self.client().messages.create(
            system=system,
            model=self.model,
            messages=msgs,
            max_tokens=max_tokens,
            **kwargs,
        )

    @RETRY
    def generate(self, messages, temperature=0.0, max_new_tokens=None, **kwargs):
        response = self._request(
            messages, temperature, max_new_tokens, self.thinking, **kwargs
        )
        return next(b.text for b in response.content if b.type == "text")

    @RETRY
    def generate_with_thinking(
        self, messages, temperature=0.0, max_new_tokens=None, **kwargs
    ):
        """Keeps the thinking tokens, wrapped as <thoughts>/<answer>."""
        response = self._request(messages, temperature, max_new_tokens, True, **kwargs)
        thoughts = "".join(b.thinking for b in response.content if b.type == "thinking")
        answer = "".join(b.text for b in response.content if b.type == "text")
        return f"<thoughts>\n{thoughts}\n</thoughts>\n\n<answer>\n{answer}\n</answer>\n"
