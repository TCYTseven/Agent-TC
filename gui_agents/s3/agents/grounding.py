import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
import threading
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

import pytesseract
from PIL import Image
from pytesseract import Output

from gui_agents.s3.memory.procedural_memory import PROCEDURAL_MEMORY
from gui_agents.s3.core.mllm import LMMAgent
from gui_agents.s3.utils.images import to_bytes
from gui_agents.s3.utils.common_utils import call_llm_safe
from gui_agents.s3.agents.code_agent import CodeAgent
import logging

logger = logging.getLogger("desktopenv.agent")


class ACI:
    def __init__(self):
        self.notes: List[str] = []


# Agent action decorator
def agent_action(func):
    func.is_agent_action = True
    return func


UBUNTU_APP_SETUP = f"""import subprocess;
import difflib;
import pyautogui;
import time;
pyautogui.press('escape');
time.sleep(0.5);
output = subprocess.check_output(['wmctrl', '-lx']);
output = output.decode('utf-8').splitlines();
window_titles = [line.split(None, 4)[2] for line in output];
closest_matches = difflib.get_close_matches('APP_NAME', window_titles, n=1, cutoff=0.1);
if closest_matches:
    closest_match = closest_matches[0];
    for line in output:
        if closest_match in line:
            window_id = line.split()[0]
            break;
subprocess.run(['wmctrl', '-ia', window_id])
subprocess.run(['wmctrl', '-ir', window_id, '-b', 'add,maximized_vert,maximized_horz'])
"""


SET_CELL_VALUES_CMD = """import uno
import subprocess
import unicodedata, json

def identify_document_type(component):
    if component.supportsService("com.sun.star.sheet.SpreadsheetDocument"):
        return "Calc"

    if component.supportsService("com.sun.star.text.TextDocument"):
        return "Writer"

    if component.supportsService("com.sun.star.sheet.PresentationDocument"):
        return "Impress"

    return None

def _norm_name(s: str | None) -> str | None:
    if s is None:
        return None
    if "\\\\u" in s or "\\\\U" in s or "\\\\x" in s:
        try:
            # json.loads handles all the escape forms safely
            s = json.loads(f"{{s}}")
        except Exception:
            # fallback: best-effort
            try:
                s = s.encode("utf-8").decode("unicode_escape")
            except Exception:
                pass
    # Normalize (NFC works well across platforms)
    return unicodedata.normalize("NFC", s)

def cell_ref_to_indices(cell_ref):
    column_letters = ''.join(filter(str.isalpha, cell_ref))
    row_number = ''.join(filter(str.isdigit, cell_ref))

    col = sum((ord(char.upper()) - ord('A') + 1) * (26**idx) for idx, char in enumerate(reversed(column_letters))) - 1
    row = int(row_number) - 1
    return col, row

def set_cell_values(new_cell_values: dict[str, str], app_name: str = "Untitled 1", sheet_name: str = "Sheet1"):
    app_name  = _norm_name(app_name)
    sheet_name = _norm_name(sheet_name)

    new_cell_values_idx = {{}}
    for k, v in new_cell_values.items():
        try:
            col, row = cell_ref_to_indices(k)
        except:
            col = row = None

        if col is not None and row is not None:
            new_cell_values_idx[(col, row)] = v

    # Clean up previous TCP connections.
    subprocess.run(
        'echo \"osworld-public-evaluation\" | sudo -S ss --kill --tcp state TIME-WAIT sport = :2002',
        shell=True,
        check=True,
        text=True,
        capture_output=True
    )

    # Dynamically allow soffice to listen on port 2002.
    subprocess.run(
        [
            "soffice",
            "--accept=socket,host=localhost,port=2002;urp;StarOffice.Service"
        ]
    )

    local_context = uno.getComponentContext()
    resolver = local_context.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver", local_context
    )
    context = resolver.resolve(
        f"uno:socket,host=localhost,port=2002;urp;StarOffice.ComponentContext"
    )
    desktop = context.ServiceManager.createInstanceWithContext(
        "com.sun.star.frame.Desktop", context
    )

    # Collect all LibreOffice-related opened windows.
    documents = []
    for i, component in enumerate(desktop.Components):
        title = component.Title
        doc_type = identify_document_type(component)
        documents.append((i, component, title, doc_type))

    # Find the LibreOffice Calc app and the sheet of interest.
    spreadsheet = [doc for doc in documents if doc[3] == "Calc"]
    selected_spreadsheet = [doc for doc in spreadsheet if doc[2] == app_name]
    if spreadsheet:
        try:
            if selected_spreadsheet:
                spreadsheet = selected_spreadsheet[0][1]
            else:
                spreadsheet = spreadsheet[0][1]

            sheet = spreadsheet.Sheets.getByName(sheet_name)
        except:
            raise ValueError(f"Could not find sheet {{sheet_name}} in {{app_name}}.")

        for (col, row), value in new_cell_values_idx.items():
            cell = sheet.getCellByPosition(col, row)

            # Set the cell value.
            if isinstance(value, (int, float)):
                cell.Value = value
            elif isinstance(value, str):
                if value.startswith("="):
                    cell.Formula = value
                else:
                    cell.String = value
            elif isinstance(value, bool):
                cell.Value = 1 if value else 0
            elif value is None:
                cell.clearContents(0)
            else:
                raise ValueError(f"Unsupported cell value type: {{type(value)}}")

    else:
        raise ValueError(f"Could not find LibreOffice Calc app corresponding to {{app_name}}.")

set_cell_values(new_cell_values={cell_values}, app_name="{app_name}", sheet_name="{sheet_name}")        
"""


def click_code(x: int, y: int, num_clicks=1, button_type="left", hold_keys=()) -> str:
    command = "import pyautogui; "
    for k in hold_keys:
        command += f"pyautogui.keyDown({repr(k)}); "
    command += (
        f"pyautogui.click({x}, {y}, clicks={num_clicks}, button={repr(button_type)}); "
    )
    for k in hold_keys:
        command += f"pyautogui.keyUp({repr(k)}); "
    return command


def type_code(platform: str, xy, text: str, overwrite=False, enter=False) -> str:
    """Click xy (if given), then type. Unicode goes through the clipboard
    (pyautogui.write is ASCII only)."""
    mod = repr("command" if platform == "darwin" else "ctrl")
    has_unicode = any(ord(char) > 127 for char in text)
    command = "import pyautogui; "
    if has_unicode:
        command += (
            "\ntry:\n"
            "    import pyperclip\n"
            "except ImportError:\n"
            "    import subprocess\n"
            "    subprocess.run('echo \"osworld-public-evaluation\" | sudo -S apt-get install -y xclip xsel', shell=True, check=True)\n"
            "    subprocess.check_call([subprocess.sys.executable, '-m', 'pip', 'install', 'pyperclip'])\n"
            "    import pyperclip\n\n"
        )
    if xy is not None:
        command += f"pyautogui.click({xy[0]}, {xy[1]}); "
    if overwrite:
        command += f"pyautogui.hotkey({mod}, 'a'); pyautogui.press('backspace'); "
    if has_unicode:
        command += f"pyperclip.copy({repr(text)}); pyautogui.hotkey({mod}, 'v'); "
    else:
        command += f"pyautogui.write({repr(text)}, interval=0); "
    if enter:
        command += "pyautogui.press('enter'); "
    return command


def scroll_code(x: int, y: int, clicks: int, shift=False) -> str:
    fn = "hscroll" if shift else "vscroll"
    return f"import pyautogui; import time; pyautogui.moveTo({x}, {y}); time.sleep(0.1); pyautogui.{fn}({clicks})"


def drag_code(x1: int, y1: int, x2: int, y2: int, hold_keys=(), button="left") -> str:
    command = f"import pyautogui; pyautogui.moveTo({x1}, {y1}); "
    for k in hold_keys:
        command += f"pyautogui.keyDown({repr(k)}); "
    command += f"pyautogui.dragTo({x2}, {y2}, duration=1., button={repr(button)}); pyautogui.mouseUp(); "
    for k in hold_keys:
        command += f"pyautogui.keyUp({repr(k)}); "
    return command


@lru_cache(maxsize=8)
def ocr_elements(image_bytes: bytes) -> Tuple[str, List]:
    """Word-level OCR boxes for a screenshot, computed once per screenshot."""
    image = Image.open(BytesIO(image_bytes))
    data = pytesseract.image_to_data(image, output_type=Output.DICT)
    elements, table = [], ["Text Table:\nWord id\tText"]
    words_in_block = defaultdict(int)
    for i, word in enumerate(data["text"]):
        word = re.sub(r"^[^a-zA-Z\s.,!?;:\-\+]+|[^a-zA-Z\s.,!?;:\-\+]+$", "", word)
        if not word:
            continue
        block = data["block_num"][i]
        words_in_block[block] += 1
        table.append(f"{len(elements)}\t{word}")
        elements.append(
            {
                "id": len(elements),
                "text": word,
                "group_num": block,
                "word_num": words_in_block[block],
                "left": data["left"][i],
                "top": data["top"][i],
                "width": data["width"][i],
                "height": data["height"][i],
            }
        )
    return "\n".join(table) + "\n", elements


# ACI primitives are parameterized by description, and coordinate generation uses a pretrained grounding model
class OSWorldACI(ACI):
    def __init__(
        self,
        env,
        platform: str,
        engine_params_for_generation: Dict,
        engine_params_for_grounding: Dict,
        width: int = 1920,
        height: int = 1080,
        code_agent_budget: int = 20,
        code_agent_engine_params: Dict = None,
        grounding_max_tokens: Optional[int] = None,
    ):
        super().__init__()

        self.env = env
        self.platform = (
            platform  # Dictates how the switch_applications agent action works.
        )

        # Configure scaling
        self.width = width
        self.height = height

        # Maintain state for save_to_knowledge
        self.notes = []

        # Screenshot used during ACI execution
        self.obs = None

        # Configure the visual grounding model responsible for coordinate generation
        self.grounding_model = LMMAgent(engine_params_for_grounding)
        self.engine_params_for_grounding = engine_params_for_grounding
        self.grounding_max_tokens = grounding_max_tokens
        # UI-TARS was trained on this exact prompt; a general model grounding
        # its own screenshot gets one that names the image size instead.
        self.grounding_prompt = engine_params_for_grounding.get(
            "grounding_prompt",
            "Query:{ref_expr}\nOutput only the coordinate of one point in your response.\n",
        )
        self._coord_cache: Dict = {}
        self._ocr_lock = threading.Lock()

        # Configure text grounding agent
        self.text_span_agent = LMMAgent(
            engine_params=engine_params_for_generation,
            system_prompt=PROCEDURAL_MEMORY.PHRASE_TO_WORD_COORDS_PROMPT,
        )

        # Configure code agent
        code_agent_engine_params = (
            code_agent_engine_params or engine_params_for_generation
        )
        self.code_agent = CodeAgent(code_agent_engine_params, code_agent_budget)

        # Store task instruction for code agent
        self.current_task_instruction = None
        self.last_code_agent_result = None

    def generate_coords(self, ref_expr: str, obs: Dict) -> List[int]:
        """Ask the grounding model for the (x, y) of the described element.
        Answers are memoised per screenshot, so grounding the same element twice
        (a retried step, a repeated description) costs nothing."""
        key = (ref_expr, hash(to_bytes(obs["screenshot"])))
        if key in self._coord_cache:
            return self._coord_cache[key]
        agent = LMMAgent(
            engine=self.grounding_model.engine
        )  # one per call: thread-safe
        agent.image_max_side = self.grounding_model.image_max_side
        agent.add_message(
            text_content=self.grounding_prompt.format(
                ref_expr=ref_expr, **self.engine_params_for_grounding
            ),
            image_content=obs["screenshot"],
            put_text_last=True,
        )
        response = call_llm_safe(agent, max_new_tokens=self.grounding_max_tokens)
        logger.debug("GROUNDING RESPONSE: %s", response)
        numbers = re.findall(r"\d+", response)
        assert len(numbers) >= 2, f"No coordinate in grounding response: {response!r}"
        coords = [int(numbers[0]), int(numbers[1])]
        if len(self._coord_cache) > 64:
            self._coord_cache.clear()
        self._coord_cache[key] = coords
        return coords

    def generate_coords_many(self, ref_exprs: List[str], obs: Dict) -> List[List[int]]:
        """Ground several descriptions concurrently (one round trip of latency)."""
        with ThreadPoolExecutor(len(ref_exprs)) as pool:
            return list(pool.map(lambda r: self.generate_coords(r, obs), ref_exprs))

    # Given the state and worker's text phrase, generate the coords of the first/last word in the phrase
    def generate_text_coords(
        self, phrase: str, obs: Dict, alignment: str = ""
    ) -> List[int]:
        with self._ocr_lock:  # both ends of a highlight share one OCR pass
            ocr_table, elements = ocr_elements(to_bytes(obs["screenshot"]))

        alignment_prompt = ""
        if alignment == "start":
            alignment_prompt = "**Important**: Output the word id of the FIRST word in the provided phrase.\n"
        elif alignment == "end":
            alignment_prompt = "**Important**: Output the word id of the LAST word in the provided phrase.\n"

        agent = LMMAgent(
            engine=self.text_span_agent.engine,
            system_prompt=self.text_span_agent.system_prompt,
        )
        agent.add_message(
            alignment_prompt + "Phrase: " + phrase + "\n" + ocr_table, role="user"
        )
        agent.add_message("Screenshot:\n", image_content=obs["screenshot"], role="user")
        response = call_llm_safe(agent, max_new_tokens=512)
        numbers = re.findall(r"\d+", response)
        text_id = int(numbers[-1]) if numbers else 0
        elem = elements[min(text_id, len(elements) - 1)]

        # Compute the element coordinates
        if alignment == "start":
            coords = [elem["left"], elem["top"] + (elem["height"] // 2)]
        elif alignment == "end":
            coords = [elem["left"] + elem["width"], elem["top"] + (elem["height"] // 2)]
        else:
            coords = [
                elem["left"] + (elem["width"] // 2),
                elem["top"] + (elem["height"] // 2),
            ]
        # OCR boxes are in screenshot pixels; clicks are in screen pixels
        shot_w, shot_h = Image.open(BytesIO(to_bytes(obs["screenshot"]))).size
        return [
            round(coords[0] * self.width / shot_w),
            round(coords[1] * self.height / shot_h),
        ]

    def assign_screenshot(self, obs: Dict):
        self.obs = obs

    def set_task_instruction(self, task_instruction: str):
        """Set the current task instruction for the code agent."""
        self.current_task_instruction = task_instruction

    # Resize from grounding model dim into OSWorld dim (1920 * 1080)
    def resize_coordinates(self, coordinates: List[int]) -> List[int]:
        grounding_width = self.engine_params_for_grounding["grounding_width"]
        grounding_height = self.engine_params_for_grounding["grounding_height"]

        return [
            round(coordinates[0] * self.width / grounding_width),
            round(coordinates[1] * self.height / grounding_height),
        ]

    @agent_action
    def click(
        self,
        element_description: str,
        num_clicks: int = 1,
        button_type: str = "left",
        hold_keys: List = [],
    ):
        """Click on the element
        Args:
            element_description:str, a detailed descriptions of which element to click on. This description should be at least a full sentence.
            num_clicks:int, number of times to click the element
            button_type:str, which mouse button to press can be "left", "middle", or "right"
            hold_keys:List, list of keys to hold while clicking
        """
        coords1 = self.generate_coords(element_description, self.obs)
        x, y = self.resize_coordinates(coords1)
        return click_code(x, y, num_clicks, button_type, hold_keys)

    @agent_action
    def switch_applications(self, app_code):
        """Switch to a different application that is already open
        Args:
            app_code:str the code name of the application to switch to from the provided list of open applications
        """
        if self.platform == "darwin":
            return f"import pyautogui; import time; pyautogui.hotkey('command', 'space', interval=0.5); pyautogui.typewrite({repr(app_code)}); pyautogui.press('enter'); time.sleep(1.0)"
        elif self.platform == "linux":
            return UBUNTU_APP_SETUP.replace("APP_NAME", app_code)
        elif self.platform == "windows":
            return f"import pyautogui; import time; pyautogui.hotkey('win', 'd', interval=0.5); pyautogui.typewrite({repr(app_code)}); pyautogui.press('enter'); time.sleep(1.0)"
        else:
            assert (
                False
            ), f"Unsupported platform: {self.platform}. Supported platforms are: darwin, linux, windows."

    @agent_action
    def open(self, app_or_filename: str):
        """Open any application or file with name app_or_filename. Use this action to open applications or files on the desktop, do not open manually.
        Args:
            app_or_filename:str, the name of the application or filename to open
        """
        if self.platform == "linux":
            return f"import pyautogui; import time; pyautogui.hotkey('win'); time.sleep(0.5); pyautogui.write({repr(app_or_filename)}); time.sleep(1.0); pyautogui.hotkey('enter'); time.sleep(0.5)"
        elif self.platform == "darwin":
            return f"import pyautogui; import time; pyautogui.hotkey('command', 'space', interval=0.5); pyautogui.typewrite({repr(app_or_filename)}); pyautogui.press('enter'); time.sleep(1.0)"
        elif self.platform == "windows":
            return (
                "import pyautogui; import time; "
                "pyautogui.hotkey('win'); time.sleep(0.5); "
                f"pyautogui.write({repr(app_or_filename)}); time.sleep(1.0); "
                "pyautogui.press('enter'); time.sleep(0.5)"
            )
        else:
            assert (
                False
            ), f"Unsupported platform: {self.platform}. Supported platforms are: darwin, linux, windows."

    @agent_action
    def type(
        self,
        element_description: Optional[str] = None,
        text: str = "",
        overwrite: bool = False,
        enter: bool = False,
    ):
        """Type text/unicode into a specific element
        Args:
            element_description:str, a detailed description of which element to enter text in. This description should be at least a full sentence.
            text:str, the text to type
            overwrite:bool, Assign it to True if the text should overwrite the existing text, otherwise assign it to False. Using this argument clears all text in an element.
            enter:bool, Assign it to True if the enter key should be pressed after typing the text, otherwise assign it to False.
        """
        xy = None
        if element_description is not None:
            coords1 = self.generate_coords(element_description, self.obs)
            xy = self.resize_coordinates(coords1)
        return type_code(self.platform, xy, text, overwrite, enter)

    @agent_action
    def save_to_knowledge(self, text: List[str]):
        """Save facts, elements, texts, etc. to a long-term knowledge bank for reuse during this task. Can be used for copy-pasting text, saving elements, etc.
        Args:
            text:List[str] the text to save to the knowledge
        """
        self.notes.extend(text)
        return """WAIT"""

    @agent_action
    def drag_and_drop(
        self, starting_description: str, ending_description: str, hold_keys: List = []
    ):
        """Drag from the starting description to the ending description
        Args:
            starting_description:str, a very detailed description of where to start the drag action. This description should be at least a full sentence.
            ending_description:str, a very detailed description of where to end the drag action. This description should be at least a full sentence.
            hold_keys:List list of keys to hold while dragging
        """
        coords1, coords2 = self.generate_coords_many(
            [starting_description, ending_description], self.obs
        )
        x1, y1 = self.resize_coordinates(coords1)
        x2, y2 = self.resize_coordinates(coords2)
        return drag_code(x1, y1, x2, y2, hold_keys)

    @agent_action
    def highlight_text_span(
        self, starting_phrase: str, ending_phrase: str, button: str = "left"
    ):
        """Highlight a text span between a provided starting phrase and ending phrase. Use this to highlight words, lines, and paragraphs.
        Args:
            starting_phrase:str, the phrase that denotes the start of the text span you want to highlight. If you only want to highlight one word, just pass in that single word.
            ending_phrase:str, the phrase that denotes the end of the text span you want to highlight. If you only want to highlight one word, just pass in that single word.
            button:str, the button to use to highlight the text span. Defaults to "left". Can be "left", "right", or "middle".
        """
        with ThreadPoolExecutor(2) as pool:
            start = pool.submit(
                self.generate_text_coords, starting_phrase, self.obs, "start"
            )
            end = pool.submit(self.generate_text_coords, ending_phrase, self.obs, "end")
            coords1, coords2 = start.result(), end.result()
        return drag_code(*coords1, *coords2, button=button)

    @agent_action
    def set_cell_values(
        self, cell_values: Dict[str, Any], app_name: str, sheet_name: str
    ):
        """Use this to set individual cell values in a spreadsheet. For example, setting A2 to "hello" would be done by passing {"A2": "hello"} as cell_values. The sheet must be opened before this command can be used.
        Args:
            cell_values: Dict[str, Any], A dictionary of cell values to set in the spreadsheet. The keys are the cell coordinates in the format "A1", "B2", etc.
                Supported value types include: float, int, string, bool, formulas.
            app_name: str, The name of the spreadsheet application. For example, "Some_sheet.xlsx".
            sheet_name: str, The name of the sheet in the spreadsheet. For example, "Sheet1".
        """
        return SET_CELL_VALUES_CMD.format(
            cell_values=cell_values, app_name=app_name, sheet_name=sheet_name
        )

    @agent_action
    def call_code_agent(self, task: str = None):
        """Call the code agent to execute code for tasks or subtasks that can be completed solely with coding.

        Args:
            task: str, the task or subtask to execute. If None, uses the current full task instruction.

        **🚨 CRITICAL GUIDELINES:**
        - **ONLY pass a task parameter for SPECIFIC subtasks** (e.g., "Calculate sum of column B", "Filter data by date")
        - **NEVER pass a task parameter for full tasks** - let it default to the original task instruction
        - **NEVER rephrase or modify the original task** - this prevents hallucination corruption
        - **If unsure, omit the task parameter entirely** to use the original task instruction

        Use this for tasks that can be fully accomplished through code execution, particularly for:
        - Spreadsheet applications (LibreOffice Calc, Excel): data processing, filtering, sorting, calculations, formulas, data analysis
        - Document editors (LibreOffice Writer, Word): text processing, content editing, formatting, document manipulation
        - Code editors (VS Code, text editors): code editing, file processing, text manipulation, configuration
        - Data analysis tools: statistical analysis, data transformation, reporting
        - File management: bulk operations, file processing, content extraction
        - System utilities: configuration, setup, automation
        """
        logger.info("=" * 50)
        logger.info("GROUNDING AGENT: Calling Code Agent")
        logger.info("=" * 50)

        # **CRITICAL**: Only use provided task for specific subtasks, otherwise use original task instruction
        if task is not None:
            # This is a subtask - use the provided task
            task_to_execute = task
            logger.info(f"Executing SUBTASK: {task_to_execute}")
        else:
            # This is a full task - use the original task instruction to prevent hallucination
            task_to_execute = self.current_task_instruction
            logger.info(f"Executing FULL TASK: {task_to_execute}")

        if task_to_execute:
            screenshot = self.obs.get("screenshot", "") if self.obs else ""
            logger.info(f"Screenshot available: {'Yes' if screenshot else 'No'}")

            logger.info("Executing code agent...")
            result = self.code_agent.execute(
                task_to_execute, screenshot, self.env.controller
            )

            # Store the result for the worker to access
            self.last_code_agent_result = result

            logger.info("Code agent execution completed")
            logger.info(f"Result - Completion reason: {result['completion_reason']}")
            logger.info(f"Steps executed: {result['steps_executed']}")
            logger.info(f"Summary: {result['summary']}")

            logger.info("=" * 50)
            logger.info("GROUNDING AGENT: Code Agent Call Finished")
            logger.info("=" * 50)

            # Return code to be executed in the environment
            return "import time; time.sleep(0.5)"
        else:
            logger.warning("No task instruction available for code agent call")
            return "import time; time.sleep(0.2)"

    @agent_action
    def scroll(self, element_description: str, clicks: int, shift: bool = False):
        """Scroll the element in the specified direction
        Args:
            element_description:str, a very detailed description of which element to enter scroll in. This description should be at least a full sentence.
            clicks:int, the number of clicks to scroll can be positive (up) or negative (down).
            shift:bool, whether to use shift+scroll for horizontal scrolling
        """
        coords1 = self.generate_coords(element_description, self.obs)
        x, y = self.resize_coordinates(coords1)
        return scroll_code(x, y, clicks, shift)

    @agent_action
    def hotkey(self, keys: List):
        """Press a hotkey combination
        Args:
            keys:List the keys to press in combination in a list format (e.g. ['ctrl', 'c'])
        """
        # add quotes around the keys
        keys = [f"'{key}'" for key in keys]
        return f"import pyautogui; pyautogui.hotkey({', '.join(keys)})"

    @agent_action
    def hold_and_press(self, hold_keys: List, press_keys: List):
        """Hold a list of keys and press a list of keys
        Args:
            hold_keys:List, list of keys to hold
            press_keys:List, list of keys to press in a sequence
        """

        press_keys_str = "[" + ", ".join([f"'{key}'" for key in press_keys]) + "]"
        command = "import pyautogui; "
        for k in hold_keys:
            command += f"pyautogui.keyDown({repr(k)}); "
        command += f"pyautogui.press({press_keys_str}); "
        for k in hold_keys:
            command += f"pyautogui.keyUp({repr(k)}); "

        return command

    @agent_action
    def wait(self, time: float):
        """Wait for a specified amount of time
        Args:
            time:float the amount of time to wait in seconds
        """
        return f"""import time; time.sleep({time})"""

    @agent_action
    def done(
        self,
    ):
        """End the current task with a success. Use this when you believe the entire task has been fully completed."""
        return """DONE"""

    @agent_action
    def fail(self):
        """End the current task with a failure. Use this when you believe the entire task is impossible to complete."""
        return """FAIL"""


class PixelACI(OSWorldACI):
    """No grounding model. The planner reads the screenshot and writes pixel
    coordinates into each action; they are scaled to the screen and run with
    pyautogui. One model call per step, and an action costs a few output
    tokens instead of a sentence describing the element."""

    # Need OCR plus a second model call, or OSWorld's LibreOffice setup
    hidden_actions = ("highlight_text_span", "set_cell_values")

    def __init__(
        self,
        env,
        platform: str,
        engine_params: Dict,
        width: int,
        height: int,
        coord_width: int,
        coord_height: int,
        normalized: bool = False,
        code_agent_budget: int = 20,
    ):
        ACI.__init__(self)
        self.env = env
        self.platform = platform
        self.width, self.height = width, height
        # The size of the screenshot the model sees: its pixels are the
        # coordinate space. Normalised models answer in 0-1000 on both axes.
        self.image_width, self.image_height = coord_width, coord_height
        self.normalized = normalized
        self.coord_width = 1000 if normalized else coord_width
        self.coord_height = 1000 if normalized else coord_height
        self.obs = None
        self.code_agent = CodeAgent(engine_params, code_agent_budget)
        self.current_task_instruction = None
        self.last_code_agent_result = None

    def to_screen(self, x, y) -> List[int]:
        x = min(max(float(x), 0), self.coord_width - 1)
        y = min(max(float(y), 0), self.coord_height - 1)
        return [
            round(x * self.width / self.coord_width),
            round(y * self.height / self.coord_height),
        ]

    @agent_action
    def click(
        self,
        x: int,
        y: int,
        num_clicks: int = 1,
        button_type: str = "left",
        hold_keys: List = [],
    ):
        """Click at (x, y). button_type: "left", "right" or "middle"; hold_keys: keys held while clicking."""
        return click_code(*self.to_screen(x, y), num_clicks, button_type, hold_keys)

    @agent_action
    def type(
        self,
        text: str,
        x: Optional[int] = None,
        y: Optional[int] = None,
        overwrite: bool = False,
        enter: bool = False,
    ):
        """Type text, first clicking (x, y) if given. overwrite clears the field first; enter presses Enter after."""
        xy = self.to_screen(x, y) if x is not None and y is not None else None
        return type_code(self.platform, xy, text, overwrite, enter)

    @agent_action
    def scroll(self, x: int, y: int, clicks: int, shift: bool = False):
        """Scroll at (x, y): clicks > 0 up, < 0 down; shift scrolls sideways."""
        return scroll_code(*self.to_screen(x, y), clicks, shift)

    @agent_action
    def drag_and_drop(self, x1: int, y1: int, x2: int, y2: int, hold_keys: List = []):
        """Drag from (x1, y1) to (x2, y2). Also selects text: drag from its first character to its last."""
        return drag_code(*self.to_screen(x1, y1), *self.to_screen(x2, y2), hold_keys)
