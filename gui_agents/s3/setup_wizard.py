"""First-run setup: pick a provider, a model and how clicks are found, with
arrow keys, and save it so later runs are one line."""

import getpass
import json
import os
import re
import sys

CONFIG_PATH = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
    "agent-tc",
    "config.json",
)

# provider -> (label, key env var, models [(id, note)])
PROVIDERS = {
    "anthropic": (
        "Anthropic (Claude)",
        "ANTHROPIC_API_KEY",
        [
            ("claude-sonnet-5-5", "recommended"),
            ("claude-haiku-5-5", "fastest"),
            ("claude-opus-5-5", "most capable"),
        ],
    ),
    "openai": (
        "OpenAI (GPT)",
        "OPENAI_API_KEY",
        [("gpt-5-2025-08-07", "recommended"), ("gpt-5-mini", "faster, cheaper")],
    ),
    "gemini": (
        "Google (Gemini)",
        "GEMINI_API_KEY",
        [("gemini-2.5-flash", "fast"), ("gemini-2.5-pro", "more capable")],
    ),
    "open_router": (
        "OpenRouter (any model)",
        "OPENROUTER_API_KEY",
        [("anthropic/claude-sonnet-4.5", "")],
    ),
    "ollama": (
        "Local with Ollama (free, private)",
        None,
        [("qwen2.5vl:7b", "needs: ollama pull qwen2.5vl:7b")],
    ),
    "vllm": (
        "Another OpenAI-compatible server (vLLM, LM Studio, ...)",
        None,
        [],
    ),
}

BOLD, DIM, CYAN, GREEN, RESET = "\033[1m", "\033[2m", "\033[36m", "\033[32m", "\033[0m"


def load_config() -> dict:
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(config: dict):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    # It may hold an API key: readable by this user only
    fd = os.open(CONFIG_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(config, f, indent=2)


def _read_key():
    """One keypress: 'up', 'down', 'enter', a character, or 'quit'."""
    if os.name == "nt":
        import msvcrt

        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            return {"H": "up", "P": "down"}.get(msvcrt.getwch(), "")
        return {"\r": "enter", "\x03": "quit"}.get(ch, ch)
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            seq = sys.stdin.read(2)
            return {"[A": "up", "[B": "down"}.get(seq, "")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return {"\r": "enter", "\n": "enter", "\x03": "quit"}.get(ch, ch)


def select(question: str, options, default: int = 0) -> int:
    """Arrow-key menu (numbers work too). Returns the chosen index."""
    print(f"{GREEN}?{RESET} {BOLD}{question}{RESET} {DIM}(use arrow keys){RESET}")
    if not sys.stdin.isatty():
        for i, opt in enumerate(options):
            print(f"  {i + 1}. {opt}")
        answer = input(f"  [{default + 1}] ").strip()
        return (
            int(answer) - 1
            if answer.isdigit() and 0 < int(answer) <= len(options)
            else default
        )
    current = default
    while True:
        for i, opt in enumerate(options):
            mark = f"{CYAN}❯ {opt}{RESET}" if i == current else f"  {opt}"
            print(f"\r\033[K  {mark}")
        key = _read_key()
        if key == "quit":
            raise KeyboardInterrupt
        if key == "enter":
            break
        if key == "up":
            current = (current - 1) % len(options)
        elif key == "down":
            current = (current + 1) % len(options)
        elif key.isdigit() and 0 < int(key) <= len(options):
            current = int(key) - 1
        print(f"\033[{len(options)}A", end="")
    # Collapse the menu to the answer
    print(f"\033[{len(options)}A\033[J  {CYAN}{options[current]}{RESET}")
    return current


def ask(question: str, default: str = "", secret: bool = False) -> str:
    hint = f" {DIM}({default}){RESET}" if default else ""
    prompt = f"{GREEN}?{RESET} {BOLD}{question}{RESET}{hint} "
    answer = getpass.getpass(prompt) if secret else input(prompt)
    return answer.strip() or default


def confirm(question: str, default: bool = False) -> bool:
    return select(question, ["No", "Yes"], default=int(default)) == 1


def run_setup() -> dict:
    if os.name == "nt":
        os.system("")  # turns on ANSI colours and cursor moves in the Windows console
    print(f"\n{BOLD}Agent TC setup{RESET}  {DIM}(saved to {CONFIG_PATH}){RESET}\n")
    keys = list(PROVIDERS)
    # Start on a provider whose key is already set
    default = next(
        (
            i
            for i, k in enumerate(keys)
            if PROVIDERS[k][1] and os.getenv(PROVIDERS[k][1])
        ),
        0,
    )
    provider = keys[
        select("Which model provider?", [PROVIDERS[k][0] for k in keys], default)
    ]
    label, key_env, models = PROVIDERS[provider]
    config = {"provider": provider}

    if models:
        options = [f"{m}  {DIM}{note}{RESET}" if note else m for m, note in models]
        choice = select("Which model?", options + ["Other..."])
        config["model"] = (
            models[choice][0] if choice < len(models) else ask("Model name:")
        )
    else:
        config["model"] = ask("Model name:")

    if provider == "ollama":
        config["model_url"] = ask("Ollama URL:", "http://localhost:11434")
    elif provider == "vllm":
        config["model_url"] = ask("Server URL:", "http://localhost:8000/v1")

    if key_env:
        if os.getenv(key_env):
            print(f"  {GREEN}✓{RESET} Using {key_env} from your environment")
        else:
            key = ask(f"API key ({key_env}):", secret=True)
            if key:
                config["model_api_key"] = key

    grounding = select(
        "How should it find what to click?",
        [
            f"The same model  {DIM}fastest, nothing to host{RESET}",
            f"A UI-TARS grounding server  {DIM}most accurate on busy screens{RESET}",
        ],
    )
    if grounding == 1:
        servers = ["huggingface", "vllm", "ollama"]
        config["ground_provider"] = servers[
            select("Where is it served?", ["Hugging Face endpoint", "vLLM", "Ollama"])
        ]
        default_url = {
            "huggingface": "http://localhost:8080",
            "vllm": "http://localhost:8000/v1",
            "ollama": "http://localhost:11434",
        }[config["ground_provider"]]
        config["ground_url"] = ask("Grounding server URL:", default_url)
        config["ground_model"] = ask("Grounding model name:", "ui-tars-1.5-7b")
        while True:
            numbers = re.findall(r"\d+", ask("Its coordinate resolution:", "1920x1080"))
            if len(numbers) == 2:
                break
            print("  Two numbers please, like 1920x1080")
        config["grounding_width"], config["grounding_height"] = map(int, numbers)
        if config["ground_provider"] == "huggingface" and not os.getenv("HF_TOKEN"):
            token = ask("Hugging Face token (HF_TOKEN):", secret=True)
            if token:
                config["ground_api_key"] = token

    config["enable_local_env"] = confirm(
        "Let it run Python and Bash on this computer for data tasks?"
    )

    save_config(config)
    print(
        f'\n{GREEN}✓{RESET} Saved. Run a task with:  {BOLD}agent-tc "open a new browser tab"{RESET}'
    )
    print(f"  {DIM}agent-tc setup  to change these choices{RESET}\n")
    return config
