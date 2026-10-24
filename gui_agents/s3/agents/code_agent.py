import logging
from typing import Dict, List, Tuple, Optional

from gui_agents.s3.memory.procedural_memory import PROCEDURAL_MEMORY
from gui_agents.s3.utils.common_utils import call_llm_safe, split_thinking_response
from gui_agents.s3.core.mllm import LMMAgent

logger = logging.getLogger("desktopenv.agent")


def extract_code_block(action: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract code and determine type from action string."""
    if "```python" in action:
        code_type = "python"
        code = action.split("```python")[1].split("```")[0].strip()
    elif "```bash" in action:
        code_type = "bash"
        code = action.split("```bash")[1].split("```")[0].strip()
    elif "```" in action:
        code_type = None
        code = action.split("```")[1].split("```")[0].strip()
    else:
        code_type = None
        code = None

    logger.debug(
        f"Extracted code block: type={code_type}, length={len(code) if code else 0}"
    )
    return code_type, code


def execute_code(code_type: str, code: str, env_controller) -> Dict:
    """Execute code based on its type."""
    # Log the full code being executed (untruncated)
    logger.info(f"CODING_AGENT_CODE_EXECUTION - Type: {code_type}\nCode:\n{code}")

    try:
        if code_type == "bash":
            result = env_controller.run_bash_script(code, timeout=30)
        elif code_type == "python":
            result = env_controller.run_python_script(code)
        else:
            result = {"status": "error", "error": f"Unknown code type: {code_type}"}

        return result

    except Exception as e:
        logger.error(f"Error executing {code_type} code: {e}")
        return {"status": "error", "error": str(e)}


def format_result(result: Dict, step_count: int) -> str:
    """Format an execution result for the model."""
    if not result:
        return f"\nStep {step_count + 1} Error:\nError: No result returned from execution\n"
    text = f"Step {step_count + 1} Result:\nStatus: {result.get('status', 'unknown')}\n"
    text += f"Return Code: {result.get('returncode', result.get('return_code', -1))}\n"
    if result.get("output"):
        text += f"Output:\n{result['output']}\n"
    if result.get("error"):
        text += f"Error:\n{result['error']}\n"
    if result.get("message"):
        text += f"Message:\n{result['message']}\n"
    return text


def summarize(history: List[Dict], last_result: str) -> str:
    """What the code agent did, without a model call: its last step's thoughts
    and result. The worker also receives the full step history."""
    if not history:
        return "No actions were executed."
    ran = [h for h in history if h["action"].upper().strip() not in ("DONE", "FAIL")]
    last = history[-1]
    parts = [f"Ran {len(ran)} code step(s), then {last['action'].strip()[:10]}."]
    if not ran:
        parts = ["Stopped before running any code."]
    if last.get("thoughts"):
        parts.append(f"Last step's reasoning: {last['thoughts'][:500]}")
    if last_result:
        parts.append(f"Last result: {last_result[:800]}")
    return "\n".join(parts)


class CodeAgent:
    """Runs Python/Bash snippets toward a task, within a step budget."""

    def __init__(self, engine_params: Dict, budget: int = 20):
        if not engine_params:
            raise ValueError("engine_params cannot be None or empty")
        self.engine_params = engine_params
        self.budget = budget
        self.reset()

    def reset(self):
        self.agent = LMMAgent(
            engine_params=self.engine_params,
            system_prompt=PROCEDURAL_MEMORY.CODE_AGENT_PROMPT,
        )

    def execute(self, task_instruction: str, screenshot: str, env_controller) -> Dict:
        if env_controller is None:
            raise ValueError("env_controller is required for code execution")
        logger.info("Code agent: %s (budget %d)", task_instruction, self.budget)
        self.reset()
        self.agent.add_message(
            f"Task: {task_instruction}\n\nCurrent screenshot is provided for context.",
            image_content=screenshot,
            role="user",
        )

        history, completion_reason, last_result = [], None, ""
        step_count = 0
        while step_count < self.budget:
            response = call_llm_safe(self.agent, temperature=1)
            if not response or not response.strip():
                raise RuntimeError(
                    f"Step {step_count + 1}: LLM returned empty response"
                )
            logger.info("CODE AGENT step %d:\n%s", step_count + 1, response)
            action, thoughts = split_thinking_response(response)
            history.append(
                {"step": step_count + 1, "action": action, "thoughts": thoughts}
            )

            if action.upper().strip() in ("DONE", "FAIL"):
                completion_reason = action.upper().strip()
                break

            code_type, code = extract_code_block(action)
            if code:
                result = execute_code(code_type, code, env_controller)
            else:
                result = {"status": "skipped", "message": "No code block found"}
            last_result = format_result(result, step_count)
            logger.info(last_result)
            self.agent.add_message(response, role="assistant")
            self.agent.add_message(last_result, role="user")
            step_count += 1

        if completion_reason is None:
            completion_reason = f"BUDGET_EXHAUSTED_AFTER_{step_count}_STEPS"
        return {
            "task_instruction": task_instruction,
            "completion_reason": completion_reason,
            "summary": summarize(history, last_result),
            "execution_history": history,
            "steps_executed": step_count,
            "budget": self.budget,
        }
