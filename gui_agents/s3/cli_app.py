import argparse
import datetime
import logging
import os
import platform
import signal
import sys
import time

import pyautogui

from gui_agents.s3 import fast_path
from gui_agents.s3.agents.agent_s import AgentS3
from gui_agents.s3.agents.grounding import PixelACI
from gui_agents.s3.utils.screen import grab_screen, settled_screen
from gui_agents.s3.utils.local_env import LocalEnv

current_platform = platform.system().lower()
logger = logging.getLogger("desktopenv")

# pyautogui waits 0.1s after every call by default; actions carry their own
# sleeps where an app needs time, so a shorter global pause is enough.
pyautogui.PAUSE = 0.02

paused = False
pause_count = 0  # bumped on every pause, so the loop knows to look again


def get_char():
    """Read one key without Enter."""
    try:
        if current_platform in ("darwin", "linux"):
            import termios
            import tty

            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                return sys.stdin.read(1)
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
        import msvcrt

        return msvcrt.getch().decode("utf-8", errors="ignore")
    except Exception:
        return input()


def signal_handler(signum, frame):
    """Ctrl+C pauses; Ctrl+C again quits, Esc resumes."""
    global paused, pause_count
    pause_count += 1
    if paused:
        print("\n\n🛑 Exiting...")
        sys.exit(0)
    paused = True
    print("\n\n🔸 Paused. Ctrl+C again to quit, Esc to resume.")
    while paused:
        try:
            char = get_char()
        except KeyboardInterrupt:
            sys.exit(0)
        if ord(char) == 3:
            print("\n\n🛑 Exiting...")
            sys.exit(0)
        if ord(char) == 27:
            print("\n\n▶️  Resuming...")
            paused = False


def setup_logging(debug: bool):
    os.makedirs("logs", exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d@%H%M%S")
    fmt = logging.Formatter("[%(asctime)s %(levelname)s %(module)s] %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    file_handler = logging.FileHandler(f"logs/agent-{stamp}.log", encoding="utf-8")
    file_handler.setFormatter(fmt)
    stdout = logging.StreamHandler(sys.stdout)
    stdout.setFormatter(fmt)
    stdout.addFilter(logging.Filter("desktopenv"))
    root.addHandler(file_handler)
    root.addHandler(stdout)


def scale_screen_dimensions(width: int, height: int, max_dim_size: int):
    scale = min(max_dim_size / width, max_dim_size / height, 1)
    return int(width * scale), int(height * scale)


def notify_done():
    if current_platform == "darwin":
        os.system(
            'osascript -e \'display notification "Task Completed" with title "Agent TC"\''
        )
    elif current_platform == "linux":
        os.system("notify-send 'Agent TC' 'Task Completed' 2>/dev/null")


def run_fast_path(aci, task: str, size):
    """Run the plain steps at the start of the task locally. Returns the
    instruction for the model, or "" when nothing is left for it."""
    steps, rest = fast_path.plan(task, aci, current_platform)
    if not steps:
        return task
    label = None
    for step in steps:
        while paused:
            time.sleep(0.1)
        if step.label != label:
            label = step.label
            print(f"⚡ {label} (no model call)")
        logger.info("EXECUTING CODE: %s", step.code)
        exec(step.code)
        settled_screen(size, max_wait=step.settle, min_wait=min(1.0, step.settle / 3))
    if not rest:
        notify_done()
        return ""
    done = "; ".join(dict.fromkeys(s.label for s in steps))
    return f"{task}\n(Already done for you: {done}. Continue from the current screen.)"


def run_agent(agent, instruction: str, size, max_steps: int = 15, settle: float = 1.0):
    screenshot = grab_screen(size)
    for step in range(max_steps):
        while paused:
            time.sleep(0.1)
        started = time.time()
        info, code = agent.predict(
            instruction=instruction, observation={"screenshot": screenshot}
        )
        action = code[0]
        print(f"🔄 Step {step + 1}/{max_steps} ({time.time() - started:.1f}s)")

        signal_word = action.strip().upper()
        if signal_word in ("DONE", "FAIL"):
            notify_done()
            break
        if signal_word not in ("NEXT", "WAIT"):
            while paused:
                time.sleep(0.1)
            logger.info("EXECUTING CODE: %s", action)
            exec(action)
        # The next screenshot is taken once the screen stops changing
        pauses = pause_count
        screenshot = settled_screen(size, max_wait=settle)
        if pause_count != pauses:  # the screen may have changed while paused
            screenshot = grab_screen(size)


DEFAULTS = {
    "provider": "openai",
    "model": "gpt-5-2025-08-07",
    "model_url": "",
    "model_api_key": "",
    "reasoning_effort": "low",
    "max_trajectory_length": 3,
    "max_steps": 15,
    "settle": 1.0,
    "image_max_side": 1280,
    "thinking": False,
    "enable_reflection": False,
    "enable_local_env": False,
    "fast_path": True,
    "debug": False,
}

# Models that answer in 0-1000 normalised coordinates rather than pixels
NORMALIZED_PROVIDERS = {"gemini"}


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="agent-tc",
        description='Do a task on this computer: agent-tc "open a new browser tab". '
        "The first run asks a few questions; `agent-tc setup` asks them again. "
        "Flags override the saved choices.",
    )
    p.add_argument("task", nargs="*", help="The task, or `setup`")
    p.add_argument("--task", dest="task_flag", help=argparse.SUPPRESS)
    add = p.add_argument
    add("--provider", help="anthropic, openai, gemini, open_router, ollama, vllm, ...")
    add("--model")
    add("--model_url", help="URL of the main model API")
    add("--model_api_key")
    add("--model_temperature", type=float, help="Fix the temperature (o3 needs 1.0)")
    add(
        "--thinking",
        action=argparse.BooleanOptionalAction,
        help="Anthropic extended thinking (slower, sometimes more accurate)",
    )
    add(
        "--reasoning_effort", help="OpenAI reasoning models: minimal, low, medium, high"
    )
    add(
        "--normalized_coords",
        action=argparse.BooleanOptionalAction,
        help="The model answers in 0-1000 coordinates, not pixels (on for gemini)",
    )
    add("--max_trajectory_length", type=int, help="Screenshots kept in context (3)")
    add(
        "--enable_reflection",
        action=argparse.BooleanOptionalAction,
        help="A reflection call per step (slower)",
    )
    add("--max_steps", type=int, help="Steps before giving up (15)")
    add(
        "--settle",
        type=float,
        help="Most seconds to wait for the screen to stop changing (1.0)",
    )
    add(
        "--image_max_side",
        type=int,
        help="Longest side of screenshots sent to the model (1280; 0 = full size)",
    )
    add(
        "--enable_local_env",
        action=argparse.BooleanOptionalAction,
        help="Let the code agent run code on this computer (WARNING: arbitrary code)",
    )
    add(
        "--fast_path",
        action=argparse.BooleanOptionalAction,
        help="Run plain steps like 'open chrome' without a model call (on)",
    )
    add("--debug", action=argparse.BooleanOptionalAction)
    args = p.parse_args(argv)

    words = args.task or ([args.task_flag] if args.task_flag else [])
    args.run_setup = words == ["setup"]
    args.task = "" if args.run_setup else " ".join(words)
    return p, args


def apply_config(args, config):
    """Flags win, then the saved setup, then the defaults. A provider given as
    a flag doesn't inherit the saved provider's model, URL or key."""
    config = dict(config)
    if args.provider and args.provider != config.get("provider"):
        for name in ("model", "model_url", "model_api_key"):
            config.pop(name, None)
        if args.model is None:
            from gui_agents.s3.setup_wizard import PROVIDERS

            models = PROVIDERS.get(args.provider, (None, None, []))[2]
            if models:
                args.model = models[0][0]
    for name in vars(args):
        if getattr(args, name) is None:
            setattr(args, name, config.get(name, DEFAULTS.get(name)))
    return args


def main():
    from gui_agents.s3.setup_wizard import load_config, run_setup

    p, args = parse_args()
    config = load_config()
    # First run (and nothing given on the command line): ask, like create-next-app
    if args.run_setup or (not config and args.provider is None and sys.stdin.isatty()):
        config = run_setup()
        if args.run_setup:
            return
    apply_config(args, config)
    if config.get("ground_provider"):
        print(
            "Note: the saved grounding server is no longer used; the model clicks itself."
        )

    if args.thinking and args.provider != "anthropic":
        p.error("--thinking is only supported with --provider anthropic")
    setup_logging(args.debug)
    signal.signal(signal.SIGINT, signal_handler)

    screen_width, screen_height = pyautogui.size()
    # The model clicks in pixels of the screenshot it sees, so capture at
    # exactly that size: nothing is resized again before upload.
    side = args.image_max_side or max(screen_width, screen_height)
    if args.provider == "anthropic":
        # Claude shrinks images over ~1.15 megapixels or 1568px on the long
        # side before it sees them; send one already that size so its pixels
        # are ours.
        long_side, short_side = max(screen_width, screen_height), min(
            screen_width, screen_height
        )
        side = min(side, 1568, int((1_150_000 * long_side / short_side) ** 0.5))
    size = scale_screen_dimensions(screen_width, screen_height, side)
    normalized = args.normalized_coords
    if normalized is None:
        normalized = args.provider in NORMALIZED_PROVIDERS

    engine_params = {
        "engine_type": args.provider,
        "model": args.model,
        "base_url": args.model_url,
        "api_key": args.model_api_key,
        "temperature": args.model_temperature,
        "thinking": args.thinking,
        "reasoning_effort": args.reasoning_effort,
        "image_max_side": 0,
    }
    print(
        f"{args.model} clicks by reading a {size[0]}x{size[1]} screenshot"
        + (" (0-1000 coordinates)." if normalized else ".")
    )

    local_env = None
    if args.enable_local_env:
        print(
            "⚠️  WARNING: the code agent will execute arbitrary code on this computer!"
        )
        local_env = LocalEnv()

    aci = PixelACI(
        local_env,
        current_platform,
        engine_params,
        width=screen_width,
        height=screen_height,
        coord_width=size[0],
        coord_height=size[1],
        normalized=normalized,
    )
    agent = AgentS3(
        engine_params,
        aci,
        platform=current_platform,
        max_trajectory_length=args.max_trajectory_length,
        enable_reflection=args.enable_reflection,
    )

    # Open the API connection while the user types or the first screenshot is taken
    agent.executor.generator_agent.engine.warm()

    def run(task):
        agent.reset()
        started = time.time()
        instruction = run_fast_path(aci, task, size) if args.fast_path else task
        if instruction:
            run_agent(agent, instruction, size, args.max_steps, args.settle)
        print(f"⏱  Finished in {time.time() - started:.1f}s")

    if args.task.strip():
        run(args.task)
        return
    while True:
        task = input("What should it do? ").strip()
        if task:
            run(task)


if __name__ == "__main__":
    main()
