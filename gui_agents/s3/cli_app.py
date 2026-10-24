import argparse
import datetime
import logging
import os
import platform
import signal
import sys
import time

import pyautogui

from gui_agents.s3.agents.agent_s import AgentS3
from gui_agents.s3.agents.grounding import OSWorldACI
from gui_agents.s3.utils.screen import grab_screen, settled_screen
from gui_agents.s3.utils.local_env import LocalEnv

current_platform = platform.system().lower()
logger = logging.getLogger("desktopenv")

# pyautogui waits 0.1s after every call by default; actions carry their own
# sleeps where an app needs time, so a shorter global pause is enough.
pyautogui.PAUSE = 0.02

paused = False
pause_count = 0  # bumped on every pause, so the loop knows to look again

GENERAL_GROUNDING_PROMPT = (
    "The screenshot is {grounding_width}x{grounding_height} pixels. Find this "
    "element: {ref_expr}\nReply with only its center as pixel coordinates (x, y)."
)


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
    "ground_url": "",
    "ground_api_key": "",
    "max_trajectory_length": 3,
    "max_steps": 15,
    "settle": 1.0,
    "image_max_side": 1280,
    "thinking": False,
    "enable_reflection": False,
    "enable_local_env": False,
    "debug": False,
}


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
        "--ground_provider", help="Grounding model provider; omit to use the main model"
    )
    add("--ground_url")
    add("--ground_api_key")
    add("--ground_model")
    add("--grounding_width", type=int, help="The grounding model's coordinate width")
    add("--grounding_height", type=int)
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
    if args.ground_provider and args.ground_provider != config.get("ground_provider"):
        for name in ("ground_model", "ground_url", "ground_api_key"):
            config.pop(name, None)
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

    if args.thinking and args.provider != "anthropic":
        p.error("--thinking is only supported with --provider anthropic")
    setup_logging(args.debug)
    signal.signal(signal.SIGINT, signal_handler)

    screen_width, screen_height = pyautogui.size()
    size = scale_screen_dimensions(screen_width, screen_height, max_dim_size=2400)

    engine_params = {
        "engine_type": args.provider,
        "model": args.model,
        "base_url": args.model_url,
        "api_key": args.model_api_key,
        "temperature": args.model_temperature,
        "thinking": args.thinking,
        "reasoning_effort": args.reasoning_effort,
        "image_max_side": args.image_max_side,
    }

    if args.ground_provider:
        missing = [
            n
            for n in ("ground_model", "grounding_width", "grounding_height")
            if getattr(args, n) is None
        ]
        if missing:
            p.error(
                f"--ground_provider also needs: {', '.join('--' + m for m in missing)}"
            )
        ground_params = {
            "engine_type": args.ground_provider,
            "model": args.ground_model,
            "base_url": args.ground_url,
            "api_key": args.ground_api_key,
            "grounding_width": args.grounding_width,
            "grounding_height": args.grounding_height,
        }
    else:
        # Ground with the main model: it answers in pixels of the image it sees.
        side = args.image_max_side or max(size)
        if args.provider == "anthropic":
            # Claude shrinks images over ~1.15 megapixels before it sees them;
            # send one already that size so its pixels are ours.
            # and over 1568px on the long side
            side = min(side, 1568, int((1_150_000 * max(size) / min(size)) ** 0.5))
            engine_params["image_max_side"] = side
        elif args.provider not in ("openai", "azure"):
            print(
                "Note: some models answer in 0-1000 normalised coordinates rather "
                "than pixels; if clicks land off, add a grounding model "
                "(--ground_provider)."
            )
        ground_w, ground_h = scale_screen_dimensions(*size, side)
        ground_params = dict(
            engine_params,
            thinking=False,
            grounding_width=ground_w,
            grounding_height=ground_h,
            grounding_prompt=GENERAL_GROUNDING_PROMPT,
        )
        print(f"Grounding with {args.model} at {ground_w}x{ground_h}.")

    local_env = None
    if args.enable_local_env:
        print(
            "⚠️  WARNING: the code agent will execute arbitrary code on this computer!"
        )
        local_env = LocalEnv()

    grounding_agent = OSWorldACI(
        env=local_env,
        platform=current_platform,
        engine_params_for_generation=engine_params,
        engine_params_for_grounding=ground_params,
        width=screen_width,
        height=screen_height,
        # Without a grounding model the planner, which already sees the same
        # image, writes the coordinates itself: one model call per click.
        direct_coords=not args.ground_provider,
    )
    agent = AgentS3(
        engine_params,
        grounding_agent,
        platform=current_platform,
        max_trajectory_length=args.max_trajectory_length,
        enable_reflection=args.enable_reflection,
    )

    # Open the API connections while the user types or the first screenshot is taken
    for engine in {
        id(e): e
        for e in (
            agent.executor.generator_agent.engine,
            grounding_agent.grounding_model.engine,
        )
    }.values():
        engine.warm()

    def run(task):
        agent.reset()
        started = time.time()
        run_agent(agent, task, size, args.max_steps, args.settle)
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
