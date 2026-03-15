# --- standard library ---
import os
import sys
import io
import re
import time
import json
import base64
from typing import Any, Optional, Tuple, Sequence, List, Union
from io import BytesIO
import importlib.resources as resources
from pathlib import Path

# --- third-party ---
import yaml
import httpx
import requests
from dotenv import load_dotenv
from pydantic import BaseModel
from PIL import Image, ImageDraw, ImageFont

# --- smolagents ---
from smolagents import (
    ChatMessage, OpenAIServerModel, TokenUsage, Tool, CodeAgent,
    ActionOutput, ActionStep
)

class StopRequested(Exception):
    """Служебное исключение для остановки стрима."""
    pass

_END = object()

def next_or_end(it):
    return next(it, _END)


# --- regex patterns for click_xy parsing ---
_CLICK_RE_MODEL_OUTPUT = re.compile(
    r'click_xy\s*\{[\s\S]*?"?x"?\s*:\s*(\d+)[\s\S]*?"?y"?\s*:\s*(\d+)',
    re.IGNORECASE,
)

_CLICK_RE_OBS = re.compile(
    r'clicked:\s*x:\s*(\d+)\s*,\s*y:\s*(\d+)',
    re.IGNORECASE,
)

_CLICK_RE_PAREN_ARGS = re.compile(
    r'click_xy\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)',
    re.IGNORECASE,
)

_CLICK_RE_PAREN_KWARGS = re.compile(
    r'click_xy\s*\(\s*x\s*=\s*(\d+)\s*,\s*y\s*=\s*(\d+)\s*\)',
    re.IGNORECASE,
)


# --- constants ---
_TIMEOUT = 30.0
_RETRIES = 3
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
url_browser = os.getenv("BROWSE_URL")


# --- stdout encoding fix ---
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


# --- type aliases ---
BBox = Tuple[int, int, int, int]
BoxInput = Union[BBox, Tuple[Tuple[int, int], str, BBox]]


def _post_with_retries(url: str, payload: dict, timeout: float, retries: int) -> httpx.Response:
    last = None
    for i in range(retries + 1):
        try:
            r = httpx.post(url, json=payload, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            if i < retries:
                time.sleep(0.2 * (2 ** i))
    raise RuntimeError("http post failed") from last



def make_request_and_get_image(
    url: str = None,
    *,
    timeout: float = _TIMEOUT,
    retries: int = _RETRIES,
) -> Tuple[Image.Image, Optional[str]]:
    """
    Синхронно получить PNG с сервера и открыть её в PIL.Image.

    Возвращает:
        (image, url) — картинка и текущий URL страницы.
    """
    url: str = f"{url_browser}/screenshot"

    resp = _post_with_retries(url, {"data": "data"}, timeout, retries)

    # Теперь ответ — это бинарный PNG
    img = Image.open(io.BytesIO(resp.content))

    # url браузерной страницы лежит в заголовках
    page_url = resp.headers.get("X-Page-Url")

    return img, page_url


def make_shapshot_env() -> str:
    """
    Возвращает строку, в которой описаны интерактивные элементы внутри страницы
    """
    url = f"{url_browser}/snapshoot"
    response = httpx.get(url,  
                          timeout=_TIMEOUT)
    
    snap = response.json()["snapshoot"].split('\n')
    if len(snap) >= 150:
        print('Найдено огромное количество элементов - делаю клиппинг:', len(snap))
        snap = snap[:150]
    return '\n'.join(snap) if snap else "Интерактивных элементов не найдено!"

 
def _extract_xy_from_text(text: str) -> tuple[int, int] | None:
    if not text:
        return None

    last_match = None

    for pattern in (
        _CLICK_RE_MODEL_OUTPUT,
        _CLICK_RE_PAREN_ARGS,
        _CLICK_RE_PAREN_KWARGS,
        _CLICK_RE_OBS,
    ):
        # finditer находит все совпадения, не только первое
        for m in pattern.finditer(text):
            groups = [g for g in m.groups() if g is not None]
            if len(groups) >= 2:
                # запоминаем последнее корректное совпадение
                last_match = (int(groups[-2]), int(groups[-1]))

    return last_match


def extract_click_xy_from_step(step) -> tuple[int, int] | None:

    xy = _extract_xy_from_text(getattr(step, "model_output", "") or "")
    if xy:
        return xy

   
    return None


def annotate_click_marker(
    img: Image.Image,
    css_xy: tuple[int, int],
    scale_x: float,
    scale_y: float,
    color=(0, 255, 0),
    r: int = 10,
    label: str | None = None
) -> Image.Image:
    """
    Рисует сплошную зелёную точку в месте клика.
    css_xy → координаты в CSS, конвертируются в пиксели по scale.
    """
    x_css, y_css = css_xy
    x_px = int(x_css * scale_x)
    y_px = int(y_css * scale_y)

    ann = img.copy()
    draw = ImageDraw.Draw(ann)

    # сплошной круг
    draw.ellipse(
        (x_px - r, y_px - r, x_px + r, y_px + r),
        fill=color,
        outline=None
    )

    # необязательная подпись рядом
    if label:
        try:
            draw.text((x_px + r + 6, y_px - r), f"{label}", fill=color)
        except Exception:
            pass

    return ann


def extract_word_boxes(ocr_payload: dict[str, Any]) -> tuple[list[tuple[tuple[int,int], str, tuple[int,int,int,int]]], str]:
    """
    Возвращает:
      - boxes: [ ((cx,cy), text, (x1,y1,x2,y2)), ... ]
      - log: строка для human-readable вывода
    """
    boxes: list[tuple[tuple[int,int], str, tuple[int,int,int,int]]] = []
    results: Sequence[dict[str, Any]] = ocr_payload.get("ParsedResults") or []
    if not results:
        return [], ""

    overlay: dict[str, Any] = results[0].get("TextOverlay") or {}
    for line in overlay.get("Lines", []):
        for word in line.get("Words", []):
            try:
                x1 = int(float(word.get("Left", 0)))
                y1 = int(float(word.get("Top", 0)))
                w = int(float(word.get("Width", 0)))
                h = int(float(word.get("Height", 0)))
            except Exception:
                continue
            x2 = x1 + w
            y2 = y1 + h
            text = word.get("WordText", "") or ""
            cx = (x1 + x2) // 2
            cy = (y1 + y2) // 2
            boxes.append(((cx, cy), text, (x1, y1, x2, y2)))

    log = "\n".join(f"Найденный текст: {t}, центр=({cx},{cy})" for (cx,cy), t, _ in boxes)
    return boxes, log


def annotate_boxes_only(image: Image.Image,
                        boxes: List[BoxInput],
                        save_path: Optional[str] = None,
                        outline: Tuple[int,int,int] = (255, 0, 0),
                        width: int = 2) -> Image.Image:
    """
    Нарисовать только прямоугольники (bbox) на копии image.
    - boxes: список либо (x1,y1,x2,y2), либо ((cx,cy), text, (x1,y1,x2,y2))
    - возвращает PIL.Image (копию с аннотациями). Если save_path задан — сохраняет туда.
    """
    # безопасная копия
    annotated = image.copy().convert("RGB")
    draw = ImageDraw.Draw(annotated)
    img_w, img_h = annotated.size

    def _norm_and_clip(coords) -> BBox:
        x1, y1, x2, y2 = map(int, coords)
        # нормализуем порядок
        if x2 < x1:
            x1, x2 = x2, x1
        if y2 < y1:
            y1, y2 = y2, y1
        # обрезаем по границам
        x1 = max(0, min(img_w, x1))
        x2 = max(0, min(img_w, x2))
        y1 = max(0, min(img_h, y1))
        y2 = max(0, min(img_h, y2))
        return (x1, y1, x2, y2)

    for b in boxes:
        if isinstance(b, tuple) and len(b) == 4 and all(isinstance(v, (int, float)) for v in b):
            bbox = _norm_and_clip(b)
        else:
            # ожидаем ((cx,cy), text, (x1,y1,x2,y2))
            try:
                bbox = _norm_and_clip(b[2])
            except Exception:
                # пропускаем некорректные записи
                continue
        if bbox[0] == bbox[2] or bbox[1] == bbox[3]:
            # нулевой прямоугольник — пропускаем
            continue
        draw.rectangle(bbox, outline=outline, width=width)

    if save_path:
        annotated.save(save_path)

    return annotated


def calculate_size(image):
    """
    Вычисляет размер отмасштабированных ширины и длины
    """
    width, height = image.size
    vp_w = int(os.getenv("BROWSER_WINDOW_W", "1200"))
    vp_h = int(os.getenv("BROWSER_WINDOW_H", "1000"))
    viewport = {"width": vp_w, "height": vp_h}
    scale_x = width / viewport["width"]
    scale_y = height / viewport["height"]
    return 1, 1

