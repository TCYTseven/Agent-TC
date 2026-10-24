import logging
import re
import textwrap
from typing import Dict, List, Optional, Tuple

from gui_agents.s3.agents.grounding import ACI
from gui_agents.s3.core.module import BaseModule
from gui_agents.s3.memory.procedural_memory import PROCEDURAL_MEMORY
from gui_agents.s3.utils.common_utils import (
    call_llm_safe,
    create_pyautogui_code,
    extract_agent_functions,
    parse_code_from_string,
    split_thinking_response,
)

logger = logging.getLogger("desktopenv.agent")

THINKING_BY_DEFAULT = {
    "claude-opus-4-20250514",
    "claude-sonnet-4-20250514",
    "claude-3-7-sonnet-20250219",
    "claude-sonnet-4-5-20250929",
    "claude-opus-4-5-20251101",
}

# Engines that keep every turn's text and only drop old screenshots
LONG_CONTEXT = {"anthropic", "openai", "gemini", "azure", "open_router"}


def format_code_agent_result(result: Dict) -> str:
    lines = [
        "\nCODE AGENT RESULT:",
        f"Task/Subtask Instruction: {result['task_instruction']}",
        f"Steps Completed: {result['steps_executed']}",
        f"Max Steps: {result['budget']}",
        f"Completion Reason: {result['completion_reason']}",
        f"Summary: {result['summary']}",
    ]
    if result["execution_history"]:
        lines.append("Execution History:")
        for i, step in enumerate(result["execution_history"]):
            m = re.search(r"```(python|bash)\s*(.*?)```", step["action"], re.DOTALL)
            body = (
                f"```{m.group(1)}\n{m.group(2).strip()}\n```" if m else step["action"]
            )
            lines.append(f"Step {i + 1}: \n{body}")
    return "\n".join(lines) + "\n\n"


class Worker(BaseModule):
    def __init__(
        self,
        worker_engine_params: Dict,
        grounding_agent: ACI,
        platform: str = "ubuntu",
        max_trajectory_length: int = 8,
        enable_reflection: bool = False,
    ):
        """Generates one grounded action per step, without hierarchical planning.

        max_trajectory_length: screenshots kept in context.
        enable_reflection: run a second model call per step that comments on the
            trajectory. Off by default: the action prompt already checks the
            previous action, and the extra call nearly doubles step latency.
        """
        super().__init__(worker_engine_params, platform)
        self.temperature = worker_engine_params.get("temperature") or 0.0
        # The published OSWorld runs used extended thinking on these Claude
        # models; the CLI always sets "thinking" explicitly.
        self.use_thinking = worker_engine_params.get(
            "thinking", worker_engine_params.get("model", "") in THINKING_BY_DEFAULT
        ) and hasattr(self._create_agent().engine, "generate_with_thinking")
        # None leaves the cap to the provider: thinking models count their
        # reasoning against it, so a small cap returns empty answers.
        self.max_new_tokens = worker_engine_params.get("max_new_tokens")
        self.grounding_agent = grounding_agent
        self.max_trajectory_length = max_trajectory_length
        self.enable_reflection = enable_reflection
        self.reset()

    def reset(self):
        skipped_actions = [] if self.platform == "linux" else ["set_cell_values"]
        env = getattr(self.grounding_agent, "env", None)
        if not getattr(env, "controller", None):
            skipped_actions.append("call_code_agent")

        sys_prompt = PROCEDURAL_MEMORY.construct_simple_worker_procedural_memory(
            type(self.grounding_agent), skipped_actions=skipped_actions
        ).replace("CURRENT_OS", self.platform)
        if getattr(self.grounding_agent, "direct_coords", False):
            ground = self.grounding_agent.engine_params_for_grounding
            w, h = ground["grounding_width"], ground["grounding_height"]
            sys_prompt += PROCEDURAL_MEMORY.DIRECT_COORDS_NOTE.format(
                width=w, height=h, ex1=w * 3 // 5, ey1=h * 4 // 5
            )

        self.generator_agent = self._create_agent(sys_prompt)
        self.reflection_agent = (
            self._create_agent(PROCEDURAL_MEMORY.REFLECTION_ON_TRAJECTORY)
            if self.enable_reflection
            else None
        )
        self.turn_count = 0
        self.worker_history = []
        self.reflections = []

    def flush_messages(self):
        """Keep the context within max_trajectory_length screenshots."""
        agents = [a for a in (self.generator_agent, self.reflection_agent) if a]
        if self.engine_params.get("engine_type", "") in LONG_CONTEXT:
            for agent in agents:
                img_count = 0
                for msg in reversed(agent.messages):
                    keep = []
                    for part in reversed(msg["content"]):
                        if "image" in part.get("type", ""):
                            img_count += 1
                            if img_count > self.max_trajectory_length:
                                continue
                        keep.append(part)
                    msg["content"] = keep[::-1]
        else:
            gen = self.generator_agent.messages
            if len(gen) > 2 * self.max_trajectory_length + 1:
                del gen[1:3]
            if self.reflection_agent:
                ref = self.reflection_agent.messages
                if len(ref) > self.max_trajectory_length + 1:
                    del ref[1]

    def _generate_reflection(self, instruction: str, obs: Dict):
        if not self.enable_reflection:
            return None, None
        if self.turn_count == 0:
            text = f"\nTask Description: {instruction}\nCurrent Trajectory below:\n"
            self.reflection_agent.add_system_prompt(
                self.reflection_agent.system_prompt + "\n" + text
            )
            self.reflection_agent.add_message(
                "The initial screen is provided. No action has been taken yet.",
                image_content=obs["screenshot"],
                role="user",
            )
            return None, None
        self.reflection_agent.add_message(
            self.worker_history[-1], image_content=obs["screenshot"], role="user"
        )
        full = call_llm_safe(
            self.reflection_agent,
            temperature=self.temperature,
            use_thinking=self.use_thinking,
            max_new_tokens=512,
        )
        reflection, thoughts = split_thinking_response(full)
        self.reflections.append(reflection)
        logger.info("REFLECTION: %s", reflection)
        return reflection, thoughts

    def _plan_and_ground(self, obs: Dict) -> Tuple[str, str, Optional[str]]:
        """Ask for the next action and ground it. The grounded code from the
        format check is the code that runs, so each step grounds once."""
        messages = list(self.generator_agent.messages)
        plan, plan_code, exec_code = "", "", None
        for attempt in range(3):
            plan = call_llm_safe(
                self.generator_agent,
                messages=messages,
                temperature=self.temperature,
                use_thinking=self.use_thinking,
                max_new_tokens=self.max_new_tokens,
            )
            plan_code = parse_code_from_string(plan)
            problems = []
            if len(extract_agent_functions(plan_code)) != 1:
                problems.append(
                    "Incorrect code: There must be a single agent action in the code response."
                )
            else:
                try:
                    exec_code = create_pyautogui_code(
                        self.grounding_agent, plan_code, obs
                    )
                except Exception as e:
                    logger.error("Could not evaluate plan code %r: %s", plan_code, e)
                    problems.append(
                        "Incorrect code: The agent action must be a valid function and use valid parameters from the docstring list."
                    )
            if not problems:
                break
            exec_code = None
            feedback = "- " + "\n- ".join(problems)
            messages += [
                {"role": "assistant", "content": [{"type": "text", "text": plan}]},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": PROCEDURAL_MEMORY.FORMATTING_FEEDBACK_PROMPT.replace(
                                "FORMATTING_FEEDBACK", feedback
                            ),
                        }
                    ],
                },
            ]
        return plan, plan_code, exec_code

    def generate_next_action(self, instruction: str, obs: Dict) -> Tuple[Dict, List]:
        self.grounding_agent.assign_screenshot(obs)
        self.grounding_agent.set_task_instruction(instruction)

        message = (
            "The initial screen is provided. No action has been taken yet."
            if self.turn_count == 0
            else ""
        )
        if self.turn_count == 0:
            self.generator_agent.add_system_prompt(
                self.generator_agent.system_prompt.replace(
                    "TASK_DESCRIPTION", instruction
                )
            )

        reflection, reflection_thoughts = self._generate_reflection(instruction, obs)
        if reflection:
            message += f"REFLECTION: You may use this reflection on the previous action and overall trajectory:\n{reflection}\n"
        if self.grounding_agent.notes:
            message += (
                f"\nCurrent Text Buffer = [{','.join(self.grounding_agent.notes)}]\n"
            )

        previous = getattr(self.grounding_agent, "last_code_agent_result", None)
        if previous is not None:
            message += format_code_agent_result(previous)
            self.grounding_agent.last_code_agent_result = None

        self.generator_agent.add_message(
            message or "Next step.", image_content=obs["screenshot"], role="user"
        )

        plan, plan_code, exec_code = self._plan_and_ground(obs)
        # The code agent runs while the action is grounded; report this step's run
        code_result = getattr(self.grounding_agent, "last_code_agent_result", None)
        if exec_code is None:
            exec_code = self.grounding_agent.wait(1.333)  # skip a turn
        self.worker_history.append(plan)
        self.generator_agent.add_message(plan, role="assistant")
        logger.info("PLAN:\n %s", plan)

        self.turn_count += 1
        self.flush_messages()
        return {
            "plan": plan,
            "plan_code": plan_code,
            "exec_code": exec_code,
            "reflection": reflection,
            "reflection_thoughts": reflection_thoughts,
            "code_agent_output": code_result,
        }, [exec_code]
