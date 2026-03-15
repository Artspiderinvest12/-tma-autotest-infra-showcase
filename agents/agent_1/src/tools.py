"""Tool definitions used by the computer-use agent."""

from __future__ import annotations

import time
from typing import Optional

from smolagents import Tool
import httpx
import os
from typing import Optional, Any, Dict
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
BASE = os.getenv("BROWSE_URL")
vp_w = int(os.getenv("BROWSER_WINDOW_W", "1200"))
vp_h = int(os.getenv("BROWSER_WINDOW_H", "1000"))
TIMEOUT = 5.0


class FinalReport(Tool):
    name = "final_answer"
    description = (
        "Завершение задачи. Вызывай только при завершении или для запроса данных.\n Формируй итоговый ответ по такой структуре:\n"
        " Отчет о выполнении\n"
        "Текущий статус задачи: Успешно | Нужно уточнение у пользователя | Не выполнено\n\n"
        "Полученный результат:\n Краткая сводка (10-50 слов)\n\n"
        "Обнаруженные проблемы во время исполнения:\n- Которые были обнаружены (обязательно, необходимо для воспроизведенияи аналитики для автотестов, разные аномалии из памяти) подробно  | Проблем не обнаружено (если нет)\n\n"
       "Конец примера. Всегда формируй ответ в таком виде. Он строго детерменированный!")
    inputs = {
        "answer":  {"type": "string",
                    "description": "Отчёт"},}

    output_type = "string"
    def forward(
        self,
        answer: str) -> str:
        return answer


class ClickXY(Tool):
    name = "click_xy"
    description = "Клик по точным координатам с проверкой активации (навигация/диалог/фокус/DOM-признаки)."
    inputs = {
        "x": {"type": "integer", "description": f"X-координата (CSS-пиксели) (max={vp_w})"},
        "y": {"type": "integer", "description": f"Y-координата (CSS-пиксели) (max={vp_h})"},
    }
    output_type = "string"

    def forward(self, x: int, y: int) -> str:
        url = f"{BASE}/click_xy"
        try:
            resp = httpx.post(url, json={"x": int(x), "y": int(y)}, timeout=8.0)
            resp.raise_for_status()
            p = resp.json()

            if p.get("status") != "success":
                return f"click failed: {p.get('error') or p}"

            aria =  (p.get("clicked") or {}).get("aria") or {}
            bbox = aria.get("bbox") or {}
            act = bool(p.get("activated"))

            tag = aria.get("tag", "?")
            role = aria.get("role", "?")
            name = aria.get("name", "")
            typ = aria.get("type", "")
            idx = aria.get("index")
            total = aria.get("total")
            if tag == "canvas":
                act = True

            loc = f"[{idx}/{total}]" if idx and total else ""
            return (
    f"aria={aria}"
    f"signals={p.get('signals')}, "     
    f"telemetry_errors={p.get('telemetry_errors')}, "
)
        except Exception as e:
            return f"click failed: {e}"


class ClearField(Tool):
    name = "clear_field"
    description = (
        "Очищает активное поле ввода: устанавливает фокус кликом по указанным координатам и удаляет текущее содержимое. Используй только когда действительно нужно очистить поле (ЕСЛИ ОНО ЗАНЯТО ДРУГИМ ЗНАЧЕНИЕМ)"

    )

    inputs = {
        "x": {
            "type": "integer",
            "description": "координата по X"
        },
        "y": {
            "type": "integer",
            "description": (
                "Координата по Y"
            )
        },
    }

    output_type = "string"

    def forward(self, x: int, y: int) -> str:
        """
        Агент вызывает этот инструмент для очистки поля.
        """
        url = f"{BASE}/clear_field"
        payload = {"x": x, "y": y}

        try:
            resp = httpx.post(url, json=payload, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            cleared = data.get("cleared_field")
            status = data.get("status")
            return f"clear_field: field={repr(cleared)}, status={repr(status)}"
        except Exception as e:
            return f"clear_field failed: {e}"



class ToggleCheckbox(Tool):
    name = "toggle_checkbox"
    description = (
        "Идемпотентно переводит чекбокс/переключатель (checkbox/switch) в заданное состояние. "
        "Работает с нативными и кастомными UI (PrimeFaces и пр.): если элемент скрыт, использует "
        "set_checked, иначе кликает по виджету или связанному label. Поддерживает область поиска."
    )
    inputs = {
        "role": {"type": "string", "description": "Обычно 'checkbox' или 'switch'."},
        "name": {"type": "string", "description": "Подпись/aria-label/title (можно подстрокой)."},
        "checked": {"type": "boolean", "description": "True — включить, False — выключить."},
        "exact": {"type": "boolean", "description": "Строгое совпадение имени; если не найдёт — ослабит до частичного.", "optional": True, "nullable": True},
        "index": {"type": "integer", "description": "Индекс среди всех совпадений (0..).", "optional": True, "nullable": True},
        "index_visible": {"type": "integer", "description": "Индекс среди видимых совпадений (0..).", "optional": True, "nullable": True},
        "frame": {"type": "string", "description": "Имя/подстрока URL/селектор iframe.", "optional": True, "nullable": True},
        "within": {"type": "object", "description": "Сузить область: {role,name?,exact?,index?}.", "optional": True, "nullable": True},
        "near": {"type": "object", "description": "Уточнение по соседу: {role?,name?} или {text?}.", "optional": True, "nullable": True},
        "timeout_ms": {"type": "integer", "description": "Таймаут ожиданий, мс (по умолчанию 5000).", "optional": True, "nullable": True},
        "state": {"type": "string", "description": "Желаемое состояние ожидания ('visible'|'attached'|'stable').", "optional": True, "nullable": True},
    }
    output_type = "string"

    def forward(
        self,
        role: str,
        name: str,
        checked: bool,
        exact: bool | None = None,
        index: int | None = None,
        index_visible: int | None = None,
        frame: str | None = None,
        within: dict | None = None,
        near: dict | None = None,
        timeout_ms: int | None = None,
        state: str | None = None,
    ) -> str:
        url = f"{BASE}/toggle_checkbox"
        payload = {
            "role": role,
            "name": name,
            "checked": checked,
            "exact": exact if exact is not None else None,
            "index": index,
            "index_visible": index_visible,
            "frame": frame or None,
            "within": within or None,
            "near": near or None,
            "timeout_ms": timeout_ms or None,
            "state": state or None,
        }
        payload = {k: v for k, v in payload.items() if v is not None}

        try:
            resp = httpx.post(url, json=payload, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            toggled = data.get("checkbox")
            status = data.get("status")
            return f"toggle_checkbox: {repr(toggled)}, status={repr(status)}, checked={checked}"
        except Exception as e:
            return f"toggle_checkbox failed: {e}"



class SelectOption(Tool):
    name = "select_option"
    description = (
        "Выбирает значение в выпадающем списке. Подходит для нативных <select> (роль 'listbox'/'combobox') "
        "и части кастомных реализаций. Значение можно передать как видимый текст опции (label) "
        "или как её атрибут value — зависит от конкретного UI. Если список кастомный и не выбирается напрямую, "
        "сначала открой выпадашку кликом (click_elements по 'combobox'), затем кликни по нужной опции ('option')."
    )

    inputs = {
        "role": {
            "type": "string",
            "description": (
                "Роль элемента списка. Обычно 'combobox' (кнопка открытия) или 'listbox' (контейнер опций). "
                "Выбирай ту роль, к которой применяешь действие выбора."
            ),
        },
        "name": {
            "type": "string",
            "description": (
                "Доступное имя поля (метка, aria-label, placeholder). "
                "Если метка длинная, укажи устойчивый фрагмент."
            ),
        },
        "value": {
            "type": "string",
            "description": (
                "Значение для выбора. Это может быть текст опции (label) или её атрибут value. "
                "Если не сработало, попробуй альтернативный формат."
            ),
        },
    }

    output_type = "string"

    def forward(self, role: str, name: str, value: str) -> str:
        """
        Агент вызывает этот инструмент, чтобы выбрать значение в выпадающем списке.
        Эквивалент Playwright:
            page.get_by_role(role, name=name).select_option(value)
        """
        url = f"{BASE}/select_option"
        payload = {"role": role, "name": name, "value": value}

        try:
            resp = httpx.post(url, json=payload, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            selected = data.get("selected_option")
            status = data.get("status")
            return f"select_option: value={repr(selected)}, status={repr(status)}"
        except Exception as e:
            return f"select_option failed: {e}"



class ScrollPage(Tool):
    name = "scroll_page"
    description = (
        "ЛОКАЛЬНЫЙ скролл только для конкретной выделенной области под курсором: таблицы, списка, модального окна, панели, внутреннего контейнера. "
        "Этот инструмент НЕ предназначен для прокрутки всей страницы. Он крутит колесо мыши в точке (x, y), поэтому сдвинется только тот scrollable-контейнер, который находится именно под этой точкой. "
        "Если нужно прокрутить весь документ целиком, используй scroll_page_full, а не этот инструмент.\n"
        "Важные поля в ответе: changed=True значит прокрутилось хоть что-то; changed=False значит по этой точке движения не было или уже достигнут край. "
        "target_found=False значит под указанной точкой не найден локальный scrollable-контейнер. "
        "target=container значит прокрутился именно внутренний контейнер. "
        "target=window значит вместо локальной области сдвинулась сама страница, то есть координата выбрана неудачно для локального скролла. "
        "contY — текущий scrollTop локального контейнера; contY=None обычно означает, что контейнер под точкой не определился."
    )
    inputs = {
        "delta_y": {
            "type": "integer",
            "description": (
                "Количество пикселей для локальной прокрутки выбранной области. "
                "Положительное значение прокручивает вниз, отрицательное вверх."
            ),
        },
        "x": {
            "type": "integer",
            "description": (
                "X-координата внутри нужной scrollable-области. "
                "Точка должна находиться прямо над тем контейнером, который нужно прокрутить."
            ),
        },
        "y": {
            "type": "integer",
            "description": (
                "Y-координата внутри нужной scrollable-области. "
                "Если указать точку вне нужного контейнера, прокрутится другая область или скролла не будет."
            ),
        },
    }
    output_type = "string"

    def forward(self, delta_y: int, x: int, y: int) -> str:
        url = f"{BASE}/scroll_page"
        try:
            resp = httpx.post(url, json={"delta_y": int(delta_y), "x": int(x), "y": int(y)}, timeout=TIMEOUT)
            resp.raise_for_status()
                
            payload = resp.json()
            return (
            f"scroll_page: requested={delta_y}, changed={payload.get('changed')}, "
            f"target_found={payload.get('target_scrollable_found')}, target={payload.get('effective_target')}, "
            f"winY={payload.get('window_scrollY_after')}, contY={payload.get('container_scrollY_after')}"
            )
        except Exception as e:
            return f"scroll_page failed: {e}"


class ScrollPageFull(Tool):
    name = "scroll_page_full"
    description = (
        "Прокручивает именно основной scroll root всей страницы, а не локальный контейнер под курсором. "
        "Используй, когда нужно гарантированно сдвинуть весь документ вверх или вниз.\n"
        "Важные поля в ответе: changed=True значит главный page-level scroll реально сдвинулся. "
        "changed=False вместе со scroll_exhausted=True означает, что в запрошенном направлении страница уже упёрлась в предел и дальше этим инструментом листать бессмысленно. "
        "method показывает, что именно сработало: dom_document — основной документ; dom_primary_container — главный крупный контейнер приложения; x11_page_key — клавишный фолбэк; noop — движения не было. "
        "remaining — сколько пикселей ещё осталось до нижней границы документа после текущего скролла. "
        "at_bottom=True значит вниз дальше скроллить уже нельзя."
    )
    inputs = {
        "delta_y": {
            "type": "integer",
            "description": "Количество пикселей для прокрутки. Положительное значение листает вниз, отрицательное вверх.",
        }
    }
    output_type = "string"

    def forward(self, delta_y: int) -> str:
        url = f"{BASE}/scroll_page_full"
        try:
            resp = httpx.post(url, json={"delta_y": int(delta_y)}, timeout=TIMEOUT)
            resp.raise_for_status()
            payload = resp.json()
            pag = payload.get("pagination") or {}
            pag_first = pag.get("first") or {}
            return (
                f"scroll_page_full: requested={delta_y}, changed={payload.get('changed')}, "
                f"method={payload.get('method')}, winY={payload.get('window_scrollY_after')}, "
                f"docY={payload.get('document_scroll_after')}, maxY={payload.get('document_max_scroll_after')}, "
                f"remaining={payload.get('remaining_scroll_after')}, scroll_exhausted={payload.get('scroll_exhausted')}, "
                f"at_bottom={payload.get('at_bottom')}, tried_containers={payload.get('tried_containers')}, "
                f"pagination_total={pag.get('total')}, pagination_visible={pag.get('visible')}, "
                f"pagination_first={pag_first.get('class') or pag_first.get('tag')}"
            )
        except Exception as e:
            return f"scroll_page_full failed: {e}"



class TypeActive(Tool):
    name = "type_active"
    description = "Вводит текст в активный элемент (поле ввода): вставляет переданное содержимое в текущий фокус. Используй как основной способ ввода данных после установки фокуса на нужное поле."
    inputs = {"text": {"type": "string", "description": "Текст для ввода. Только текст."}}
    output_type = "string"

    def forward(self, text: str) -> str:
        url = f"{BASE}/type_active"
        try:
            resp = httpx.post(url, json={"text": text}, timeout=TIMEOUT)
            resp.raise_for_status()
            payload = resp.json()
            typed = payload.get("typed")
            current_value = payload.get("current_value")
            return f"type_active: typed={repr(typed)}, current_value={repr(current_value)}"
        except Exception as e:
            return f"type_active failed: {e}"



class Goto(Tool):
    name = "goto"
    description = "Перейти на указанный URL внутри браузера."
    inputs = {"url": {"type": "string", "description": "Адрес ссылки страницы"}}
    output_type = "string"

    def forward(self, url: str) -> str:
        endpoint = f"{BASE}/goto"
        try:
            resp = httpx.post(endpoint, json={"url": url}, timeout=TIMEOUT)
            resp.raise_for_status()
            payload = resp.json()
            status = payload.get("status")
            page_url = payload.get("url")
            return f"goto: {repr(page_url)} -> status={status}"
        except Exception as e:
            return f"goto failed: {e}"



class GoBack(Tool):
    name = "go_back"
    description = "Вернуться на предыдущую страницу."
    inputs = {}
    output_type = "string"

    def forward(self) -> str:
        endpoint = f"{BASE}/go_back"
        try:
            resp = httpx.post(endpoint, timeout=TIMEOUT)
            resp.raise_for_status()
            payload = resp.json()
            return f"go_back -> url={repr(payload.get('url'))}, status={payload.get('status')}"
        except Exception as e:
            return f"go_back failed: {e}"



class GoForward(Tool):
    name = "go_forward"
    description = "Перейти на следующую страницу из истории."
    inputs = {}
    output_type = "string"

    def forward(self) -> str:
        endpoint = f"{BASE}/go_forward"
        try:
            resp = httpx.post(endpoint, timeout=TIMEOUT)
            resp.raise_for_status()
            payload = resp.json()
            return f"go_forward -> url={repr(payload.get('url'))}, status={payload.get('status')}"
        except Exception as e:
            return f"go_forward failed: {e}"



class Reload(Tool):
    name = "reload"
    description = "Обновить текущую страницу."
    inputs = {}
    output_type = "string"

    def forward(self) -> str:
        endpoint = f"{BASE}/reload"
        try:
            resp = httpx.post(endpoint, timeout=TIMEOUT)
            resp.raise_for_status()
            payload = resp.json()
            return f"reload -> url={repr(payload.get('url'))}, status={payload.get('status')}"
        except Exception as e:
            return f"reload failed: {e}"



class Move(Tool):

    name = "move"
    description = "Переместить курсор мыши к указанным координатам."
    inputs = {
        "x": {"type": "integer", "description": "X-координата"},
        "y": {"type": "integer", "description": "Y-координата"},
    }
    output_type = "string"

    def forward(self, x: int, y: int) -> str:
        url = f"{BASE}/move"
        try:
            resp = httpx.post(url, json={"x": int(x), "y": int(y)}, timeout=TIMEOUT)
            resp.raise_for_status()
            payload = resp.json()
            moved = payload.get("moved_to")
            return f"move: moved_to={repr(moved)}"
        except Exception as e:
            return f"move failed: {e}"
