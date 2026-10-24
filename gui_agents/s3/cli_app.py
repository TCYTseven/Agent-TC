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
    global paused
    if paused:
        print("\n\n🛑 Exiting Agent-S...")
        sys.exit(0)
    paused = True
    print("\n\n🔸 Paused. Ctrl+C again to quit, Esc to resume.")
    while paused:
        try:
            char = get_char()
        except KeyboardInterrupt:
            sys.exit(0)
        if ord(char) == 3:
            print("\n\n🛑 Exiting Agent-S...")
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
            'osascript -e \'display notification "Task Completed" with title "Agent S"\''
        )
    elif current_platform == "linux":
        os.system("notify-send 'Agent S' 'Task Completed' 2>/dev/null")


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
        screenshot = settled_screen(size, max_wait=settle)


def main():
    p = argparse.ArgumentParser(description="Run Agent S3 on this computer.")
    p.add_argument(
        "--provider",
        default="openai",
        help="openai, anthropic, gemini, ollama, vllm, ...",
    )
    p.add_argument("--model", default="gpt-5-2025-08-07")
    p.add_argument("--model_url", default="", help="URL of the main model API")
    p.add_argument("--model_api_key", default="")
    p.add_argument(
        "--model_temperature",
        type=float,
        default=None,
        help="Fix the temperature (o3 needs 1.0)",
    )
    p.add_argument(
        "--thinking",
        action="store_true",
        help="Anthropic extended thinking (slower, sometimes more accurate)",
    )
    p.add_argument(
        "--reasoning_effort",
        default="low",
        help="OpenAI reasoning models: minimal, low, medium or high",
    )

    p.add_argument(
        "--ground_provider",
        default=None,
        help="Grounding model provider. Omit to ground with the main model.",
    )
    p.add_argument("--ground_url", default="")
    p.add_argument("--ground_api_key", default="")
    p.add_argument("--ground_model", default=None)
    p.add_argument(
        "--grounding_width",
        type=int,
        default=None,
        help="Screenshot width after the grounding model's own rescaling",
    )
    p.add_argument("--grounding_height", type=int, default=None)

    p.add_argument(
        "--max_trajectory_length",
        type=int,
        default=3,
        help="Screenshots kept in the model's context",
    )
    p.add_argument(
        "--enable_reflection",
        action="store_true",
        help="Add a reflection call per step (slower)",
    )
    p.add_argument("--max_steps", type=int, default=15)
    p.add_argument(
        "--settle",
        type=float,
        default=1.0,
        help="Most seconds to wait for the screen to stop changing after an action",
    )
    p.add_argument(
        "--image_max_side",
        type=int,
        default=1280,
        help="Longest side of screenshots sent to the main model (0 = full size)",
    )
    p.add_argument(
        "--enable_local_env",
        action="store_true",
        help="Let the code agent run code on this computer (WARNING: arbitrary code)",
    )
    p.add_argument("--debug", action="store_true")
    p.add_argument("--task", help="The task. Omit for an interactive prompt.")
    args = p.parse_args()

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
            side = min(side, int((1_150_000 * max(size) / min(size)) ** 0.5))
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

    if args.task and args.task.strip():
        run(args.task)
        return
    while True:
        run(input("Query: "))
        if input("Another query? (y/n): ").lower() != "y":
            break


if __name__ == "__main__":
    main()
