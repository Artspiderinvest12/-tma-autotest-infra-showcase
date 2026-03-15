# standard library
import os
import sys
import re
import time
import importlib.resources as resources
from pathlib import Path
from typing import Any, Tuple, Union, Optional, List

# third-party
import yaml
import httpx
from dotenv import load_dotenv
from pydantic import BaseModel
from PIL import ImageChops, ImageStat

# local modules
from .utils import (
    make_request_and_get_image, 
    calculate_size,
    extract_click_xy_from_step,
    annotate_click_marker,
    make_shapshot_env
)
from .custom_agent import PatteRN_Agent, Server

# smolagents / project tools
from smolagents import (
    ChatMessage, OpenAIServerModel, TokenUsage, Tool, CodeAgent,
    ActionStep, ActionOutput, ChatMessageStreamDelta, FinalAnswerStep,
    TaskStep, Generator
)
from smolagents.models import ChatMessageToolCallStreamDelta

# MCP tools
from .tools import (
    ClickXY, ScrollPage, ScrollPageFull, GoBack, Goto, TypeActive, Reload, Move, ClearField,
    ToggleCheckbox, SelectOption, FinalReport
)



load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
api_key = os.getenv("API_KEY")
proxy = os.getenv("PROXY")


try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


# ---------------------- Утилиты ----------------------------

def load_prompt(name: str) -> dict[str, Any]:
    """Load YAML prompt template from the prompts package."""
    with resources.files("prompts").joinpath(name).open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)

def extract_thought(text: str) -> str:
    """Return fragment between 'Thought:' and 'Action:' markers."""
    match = re.search(r"Thought:\s*(.*?)\s*Action:", text, flags=re.DOTALL | re.IGNORECASE)
    return match.group(1).strip().replace('<THOUGHT_PROTOCOL>', '').replace('</THOUGHT_PROTOCOL>', '') if match else ""

def extract_task_content(text: str) -> str | None:
    """
    Извлекает содержимое между <task>...</task>.
    Возвращает None, если таких тегов нет.
    """
    match = re.search(r"<task>(.*?)</task>", text, flags=re.DOTALL)
    return match.group(1).strip() if match else None

def save_params_in_step(memory_step: ActionStep, *args, **kwargs):
    """
    Сохраняет данные для следующего шага и запоминает данные для будущих пост обработок.
    """
    # безопасно вытаскиваем значения
    image = kwargs.get("image")
    ocr_log = kwargs.get("ocr_log", "")
    snap = kwargs.get("snap", "")

    if image is not None:
        # Для конкретного ActionStep храним только актуальный скрин этого шага.
        memory_step.observations_images = [image]


    memory_step.ocr = f"<🔹 OCR>\n{ocr_log}\n</OCR>\n"
    memory_step.snap = f"<🔹 aria_snapshot>\n{snap}\n</aria_snapshot>\n"


    observation_prefix = (
        "Проверь: есть ли визуальная реакция на скриншоте."
    )

    memory_step.observations = f"{memory_step.observations}\n{observation_prefix}"
   

class Tasks(BaseModel):
    tasks: List[str]|None = None
    levels: List[str]|None = None
    url: Optional[str] = None
    scenario: Optional[str] = None
    reasoning: Optional[bool] = None
    level: Optional[str] = None
    model: Optional[str] = None
    mode: Optional[str] = None

class Task_Params(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    reasoning: Optional[bool] = None
    level: Optional[str] = None
    url: Optional[str] = None
    scenario: Optional[str] = None
    model: Optional[str] = None
    task: Optional[str] = None
    mode: Optional[str] = None

class TaskWithSession(BaseModel):
    session_id: str
    task: Task_Params

class TasksWithSession(BaseModel):
    session_id: str
    tasks: Tasks

class SessionOnly(BaseModel):
    session_id: str


# ----------------------- Функция после шага --------------------

def save_screenshot(memory_step: ActionStep, 
                    agent: PatteRN_Agent, 
                    delay_seconds: float = 3.0, 
                    viewport={"width": 1200, "height": 1000}) -> None:
    """Capture and annotate the current page after an agent step."""
    st = 0
    memory_step.print_results_for_process = memory_step.observations

    time.sleep(3)
    # Пробегаемся по прошлым шагам и корректируем поля
    st = 0
    for previous in agent.memory.steps:
        if isinstance(previous, ActionStep):
            # очистим устаревшие изображения у шагов, которые далеко в прошлом
            if previous.step_number <= (memory_step.step_number - 2):
                previous.observations = previous.print_results_for_process
                previous.observations_images = []
            st += 1

        if isinstance(previous, TaskStep):
            if isinstance(previous.task, str) and "<task>" in previous.task:
                previous.task = extract_task_content(previous.task)
                previous.task_images = []
            elif (
                memory_step.step_number >= 1
                and getattr(previous, "task_images", None)
                and isinstance(previous.task, str)
                and "Последний сделанный скриншот:" in previous.task
            ):
                # В batch этот унаследованный скрин нужен только как стартовый контекст
                # для первого шага новой задачи. Дальше используем свежие step-скрины.
                previous.task_images = []
    
    if st >= 16:
        agent.optimize_memory(memory_step.step_number)


    try:
        print("Делаю запрос")
        img, _ = make_request_and_get_image()
        snap = "" #make_shapshot_env()
        scale_x, scale_y = calculate_size(img)
        #boxes, ocr_log = make_detection_on_image(img)
        ocr_log = "Нет данных"
        print("Сделал")
    except Exception as e:
        print('Произошла ошибка', e)
        agent.interrupt()
        return

    css_click = extract_click_xy_from_step(memory_step)
    image_procced= img.copy()

    if css_click is not None:
        image_procced = annotate_click_marker(
            image_procced,
            css_click,
            scale_x=scale_x,
            scale_y=scale_y,
            color=(0, 0, 0),
            r=0.5,
            label=""
        )

    save_params_in_step(memory_step=memory_step,
                        image=image_procced, 
                        ocr_log=ocr_log, 
                        snap=snap)

# --------------- Создание клиента агента и модели -------------------

class AgentState:
    def __init__(self):
        self.agent: PatteRN_Agent | None = None
        self.task: str | None = None
        self.running: bool = False
        self.model: Server | None = None
        self.steps: list[dict] = []
        self.stop_task: bool = False
        self.iterator = None
        self.interrupt: bool = False
        self.final: bool = False

    def reset(self):
        """Полный сброс состояния текущего агента."""
        self.agent = None
        self.task = None
        self.running = False
        self.interrupt = False
        self.final = False
        self.model = None
        self.steps.clear()
        self.stop_task = False
        self.iterator = None

    def activate(self, params: "Task_Params"):
        """
        Инициализирует модель и агента под конкретный запрос Task_Params.
        Вызывается перед /runf
        """
        # 1) выбираем id модели
        model_id = "anthropic/claude-haiku-4.5"
   
        # 2) создаём клиента модели
        self.model = create_model_client(model_id=model_id)
        # 3) настраиваем уровень рассуждений (если включен reasoning)
        if params.reasoning and params.level:
            if params.level == "minimal":
                params.level = "low"
            self.model.reasoning_effort = params.level
        else:
            self.model.reasoning_effort = "none"

        # 4) создаём агента
        self.agent = create_agent(self.model)

        # 5) формируем текст задачи
        base_task = params.task or ""
        url_part = f"\nВот URL страницы для старта (Опционально): {params.url}" if params.url else ""
        self.task = base_task + url_part

        # 6) доп. инструкции (scenario) — только если они заданы
        if params.scenario:
            self.agent.instructions = params.scenario

        # 7) обновляем флаги состояния
        self.running = True
        self.stop_task = False
        self.iterator = None  
        self.steps = []

def create_model_client(model_id: str = "qwen/qwen3-vl-30b-a3b-thinking") -> Server:

    http_client = httpx.Client(proxy=proxy) if proxy else httpx.Client()
    return Server(
        model_id=model_id,  
        api_base="https://openrouter.ai/api/v1",
        api_key=api_key,
        # client_kwargs={"http_client": http_client},
    )

def create_agent(model: Server):
    """
    Создает агента со всеми инструментами.
    """
    tools = [ClickXY(), 
             TypeActive(), 
             ScrollPage(),
             ScrollPageFull(),
             Goto(),  
             GoBack(), 
             ClearField()]
    
    prompt_templates = load_prompt("use.yaml")
    agent = PatteRN_Agent(tools,
        model, 
        step_callbacks=[save_screenshot], 
        verbosity_level=1,
        prompt_templates=prompt_templates, 
        stream_outputs=True, 
            )
    agent.tools['final_answer'] = FinalReport()
    return agent
 
