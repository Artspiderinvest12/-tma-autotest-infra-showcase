from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
import asyncio
import uvicorn
import io
import base64
import httpx
import json
from smolagents import AgentError
from typing import Optional, List, Union
from datetime import datetime
from time import monotonic
from pathlib import Path
from dotenv import load_dotenv
import os

# -- Утилиты --
from src.initialize import (AgentState, 
create_model_client, 
create_agent, 
Tasks,
Task_Params,
TaskStep, 
ActionStep, 
ActionOutput, 
extract_thought,  
ChatMessageStreamDelta, FinalAnswerStep)
from src.utils_report import SUB_HR, HR, HR_BOLD, SECTION, fmt_ts
from src.utils import StopRequested, _END, next_or_end



def encode_step_image(img) -> str:
    buffer = io.BytesIO()
    # сжимаем: уменьшаем до разумного размера
    img = img.convert("RGB")
    img.thumbnail((960, 960))  # подбери под себя
    img.save(buffer, format="JPEG", quality=80, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


start_url = "https://bing.com"
app = FastAPI()
app.state.agent_state = AgentState()


def _serialize_action_trace_step(step: ActionStep, fallback_index: int) -> dict:
    model_output = getattr(step, "model_output", "")

    try:
        thought = extract_thought(model_output)
    except Exception:
        thought = model_output or ""

    timing = getattr(step, "timing", None)
    start_time = getattr(timing, "start_time", None)
    end_time = getattr(timing, "end_time", None)

    duration = (
        round(end_time - start_time, 2)
        if start_time is not None and end_time is not None
        else None
    )

    token_usage = getattr(step, "token_usage", None)
    input_tokens = int(getattr(token_usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(token_usage, "output_tokens", 0) or 0)
    total_tokens = int(
        getattr(token_usage, "total_tokens", input_tokens + output_tokens)
        or (input_tokens + output_tokens)
    )

    return {
        "step_number": int(getattr(step, "step_number", fallback_index) or fallback_index),
        "step_kind": "action",
        "thought": thought,
        "code_action": str(getattr(step, "code_action", "") or ""),
        "observations": str(getattr(step, "observations", "") or ""),
        "started_at": start_time,
        "ended_at": end_time,
        "duration_sec": duration,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "is_final_answer": bool(getattr(step, "is_final_answer", False)),
    }


def _build_action_trace_payload(st: AgentState) -> dict | None:
    raw_steps = getattr(st, "steps", None)
    if not raw_steps or not isinstance(raw_steps, list):
        return None

    trace_steps: list[dict] = []
    for i, step in enumerate(raw_steps):
        if isinstance(step, ActionStep):
            trace_steps.append(_serialize_action_trace_step(step, i))

    if not trace_steps:
        return None

    run_start = next((s["started_at"] for s in trace_steps if s["started_at"] is not None), None)
    run_end = next((s["ended_at"] for s in reversed(trace_steps) if s["ended_at"] is not None), None)
    total_duration = (
        round(run_end - run_start, 2)
        if run_start is not None and run_end is not None
        else None
    )

    model_id = getattr(getattr(st, "model", None), "model_id", None)

    return {
        "trace_version": 1,
        "task": str(getattr(st, "task", "") or ""),
        "model_id": model_id,
        "run_started_at": run_start,
        "run_ended_at": run_end,
        "total_duration_sec": total_duration,
        "steps_count": len(trace_steps),
        "steps": trace_steps,
    }


# --------------------------- ЗАПУСК ЗАДАЧ -------------------------------------
@app.post("/assign_task_from_last_step")
async def run_agent_from_last_step(payload: dict):
    st: AgentState = app.state.agent_state

    if not st.agent:
        return JSONResponse(
            {"status": "Агент не инициализирован! Невозможно продолжить выполнение!"},
            status_code=400,
        )
    if st.running:
        return JSONResponse(
            {"status": "Агент уже выполняет задачу"},
            status_code=409,
        )
    st.task = payload.get("task", "Задачи нет, вызывай FinalAnswer()")
    st.stop_task = False
    st.running = True



    async def run(text: str):
        final = None
        delta_buf: list[str] = []
        last_flush = monotonic()

        sum_in = 0
        sum_out = 0
        sum_total = 0

        st.iterator = st.agent.run(text, max_steps=400, stream=True, reset=False)

        try:
            st.steps.append(st.agent.memory.steps[-1])
        except Exception:
            print("Произошла ошибка при извлечении начальной задачи для сохрания отчета")

        try:
            while True:
                if st.stop_task:
                    st.agent.interrupt()
                    raise StopRequested()
                step = await asyncio.to_thread(next_or_end, st.iterator)
                if step is _END:
                    break
                
                if isinstance(step, ActionStep):
                    event = {
                        "type": "step",
                        "step_type": "think",
                        "thoughts": extract_thought(step.model_output) if step.model_output else "",
                        "code": step.code_action}
                    tu = getattr(step, "token_usage", None)

                    if tu:
                        in_t = int(getattr(tu, "input_tokens", 0) or 0)
                        out_t = int(getattr(tu, "output_tokens", 0) or 0)
                        tot_t = int(getattr(tu, "total_tokens", in_t + out_t) or (in_t + out_t))

                        event["input_tokens"] = in_t
                        event["output_tokens"] = out_t
                        event["total_tokens"] = tot_t

                        sum_in += in_t
                        sum_out += out_t
                        sum_total += tot_t

                    if step.observations_images:
                        event["image"] = encode_step_image(step.observations_images[-1])

                    st.steps.append(step)
                    yield json.dumps(event, ensure_ascii=False) + "\n"
                    await asyncio.sleep(0)

                    if step.is_final_answer:
                        final = step.action_output

                elif isinstance(step, ChatMessageStreamDelta):
                    if step.content:
                        delta_buf.append(step.content)

                    now = monotonic()
                    if now - last_flush >= 0.03:
                        yield json.dumps({
                            "type": "delta",
                            "step_type": "tokens",
                            "thoughts": "".join(delta_buf),
                        }, ensure_ascii=False) + "\n"
                        delta_buf.clear()
                        last_flush = now

        except StopRequested:
            st.running = False
            st.stop_task = False

            if delta_buf:
                yield json.dumps({
                    "type": "delta",
                    "step_type": "tokens",
                    "thoughts": "".join(delta_buf),
                }, ensure_ascii=False) + "\n"
                delta_buf.clear()

            yield json.dumps({
                "type": "user_action_stop",
                "usage": {
                    "input_tokens": sum_in,
                    "output_tokens": sum_out,
                    "total_tokens": sum_total,
                }
            }, ensure_ascii=False) + "\n"
            return

        except AgentError:
            st.stop_task = False
            st.running = False
            yield json.dumps({
                "type": "user_action_stop",
                "usage": {
                    "input_tokens": sum_in,
                    "output_tokens": sum_out,
                    "total_tokens": sum_total,
                }
            }, ensure_ascii=False) + "\n"
            return

        except Exception as e:
            st.running = False
            yield json.dumps({"type": "error", "raw": str(e)}, ensure_ascii=False) + "\n"
            return



        st.running = False

        if delta_buf:
            yield json.dumps({
                "type": "delta",
                "step_type": "tokens",
                "thoughts": "".join(delta_buf),
            }, ensure_ascii=False) + "\n"
            delta_buf.clear()

        yield json.dumps({
            "type": "done",
            "final": str(final),
            "usage": {
                "input_tokens": sum_in,
                "output_tokens": sum_out,
                "total_tokens": sum_total,
            }
        }, ensure_ascii=False) + "\n"

    return StreamingResponse(
        run(st.task),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/run")
async def run_agent(task: Task_Params):
    st: AgentState = app.state.agent_state
    if st.agent:
        st.reset()

    st.activate(task)



    async def run(text: str):
        final = None
        delta_buf: list[str] = []
        last_flush = monotonic()


        sum_in = 0
        sum_out = 0
        sum_total = 0

        st.iterator = st.agent.run(text, max_steps=800, stream=True)

        try:
            st.steps.append(st.agent.memory.steps[-1])
        except Exception:
            print("Произошла ошибка при извлечении начальной задачи для сохрания отчета")

        try:
            step_index = 0
            while True:
                if st.stop_task:
                    st.agent.interrupt()
                    raise StopRequested()
                step = await asyncio.to_thread(next_or_end, st.iterator)
                if step is _END:
                    break



                if isinstance(step, ActionStep):
                    step_index += 1
                    event = {
                        "type": "step",
                        "step_type": "think",
                        "thoughts": extract_thought(step.model_output) if step.model_output else "",
                        "code": step.code_action, 
                        "step_index": step_index,
                        
                    }

                    tu = getattr(step, "token_usage", None)
                    if tu:
                        in_t = int(getattr(tu, "input_tokens", 0) or 0)
                        out_t = int(getattr(tu, "output_tokens", 0) or 0)
                        tot_t = int(getattr(tu, "total_tokens", in_t + out_t) or (in_t + out_t))

                        event["input_tokens"] = in_t
                        event["output_tokens"] = out_t
                        event["total_tokens"] = tot_t

                        sum_in += in_t
                        sum_out += out_t
                        sum_total += tot_t



                    if step.observations_images:
                        event["image"] = encode_step_image(step.observations_images[-1])

                    st.steps.append(step)
                    yield json.dumps(event, ensure_ascii=False) + "\n"
                    await asyncio.sleep(0)

                    if step.is_final_answer:
                        final = step.action_output

                elif isinstance(step, ChatMessageStreamDelta):
                    if step.content:
                        delta_buf.append(step.content)

                    now = monotonic()
                    if now - last_flush >= 0.001:
                        yield json.dumps({
                            "type": "delta",
                            "step_type": "tokens",
                            "thoughts": "".join(delta_buf),
                        }, ensure_ascii=False) + "\n"
                        delta_buf.clear()
                        last_flush = now

        except StopRequested:
            st.stop_task = False
            st.running = False

            if delta_buf:
                yield json.dumps({
                    "type": "delta",
                    "step_type": "tokens",
                    "thoughts": "".join(delta_buf),
                }, ensure_ascii=False) + "\n"
                delta_buf.clear()

            yield json.dumps({
                "type": "user_action_stop",
                "usage": {
                    "input_tokens": sum_in,
                    "output_tokens": sum_out,
                    "total_tokens": sum_total,
                }
            }, ensure_ascii=False) + "\n"
            return

        except AgentError:
            st.stop_task = False
            st.running = False
            yield json.dumps({
                "type": "user_action_stop",
                "usage": {
                    "input_tokens": sum_in,
                    "output_tokens": sum_out,
                    "total_tokens": sum_total,
                }
            }, ensure_ascii=False) + "\n"
            return

        except Exception as e:
            st.running = False
            yield json.dumps({"type": "error", "raw": str(e)}, ensure_ascii=False) + "\n"
            return

     

        st.running = False

        if delta_buf:
            yield json.dumps({
                "type": "delta",
                "step_type": "tokens",
                "thoughts": "".join(delta_buf),
            }, ensure_ascii=False) + "\n"
            delta_buf.clear()

        yield json.dumps({
            "type": "done",
            "final": str(final),
            "usage": {
                "input_tokens": sum_in,
                "output_tokens": sum_out,
                "total_tokens": sum_total,
            }
        }, ensure_ascii=False) + "\n"

    return StreamingResponse(
        run(st.task),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/run_tasks")
async def run_tasks_with_reasoning(request: Tasks):
    st: AgentState = app.state.agent_state
    tasks = request.tasks or []
    reasoning_efforts = request.levels or []

    if len(tasks) != len(reasoning_efforts):
        return JSONResponse(
            {"status": "Ошибка! Должно быть одинаковое по числу задач и уровней размышления!"},
            status_code=400,
        )

    # защита от параллельного запуска
    if getattr(st, "running", False):
        return JSONResponse({"status": "Агент уже выполняет задачу"}, status_code=409)

    def select_params_for_agent(reasoning_effort: str):
        st.model = create_model_client(model_id="anthropic/claude-haiku-4.5")

        if reasoning_effort == "minimal":
            reasoning_effort = "low"

        st.model.reasoning_effort = reasoning_effort if reasoning_effort != "no_reasoning" else "none"
        st.agent = create_agent(st.model)

    st.running = True
    st.stop_task = False

    async def run(tasks: List[str], reasoning_efforts: List[str]):
        action_last_img = None

        # totals по всему запросу
        total_in = 0
        total_out = 0
        total_tokens = 0

        # чтобы в StopRequested/ошибке можно было дослать хвост
        delta_buf: list[str] = []
        last_flush = monotonic()

        current_task_index = 0
        current_sum_in = 0
        current_sum_out = 0
        current_sum_total = 0

        try:
            for idx, (task_text, reasoning_effort) in enumerate(zip(tasks, reasoning_efforts)):
                current_task_index = idx
                current_sum_in = 0
                current_sum_out = 0
                current_sum_total = 0

                if st.stop_task:
                    raise StopRequested()

                select_params_for_agent(reasoning_effort)

                delta_buf = []
                last_flush = monotonic()
                final = None
                step_index = 0

                # формируем текст для продолжения (со скрином)
                if idx > 0:
                    task_text = (
                        "Прошлая задача была успешно выполнена!\n"
                        "Вот текущая задача, которую нужно решить:\n"
                        f"{task_text}\n"
                        "Последний сделанный скриншот:\n"
                    )
                    iterator = st.agent.run(
                        task_text,
                        images=[action_last_img] if action_last_img is not None else None,
                        max_steps=400,
                        stream=True,
                    )
                else:
                    iterator = st.agent.run(task_text, max_steps=400, stream=True)

                # ----------- стрим одной задачи -----------
                while True:
                    if st.stop_task:
                        st.agent.interrupt()
                        raise StopRequested()

                    step = await asyncio.to_thread(next_or_end, iterator)
                    if step is _END:
                        break

                    if st.stop_task:
                        st.agent.interrupt()
                        raise StopRequested()

                    if isinstance(step, ActionStep):
                        step_index += 1

                        event = {
                            "type": "step",
                            "task_index": idx,
                            "task_total": len(tasks),
                            "step_index": step_index,
                            "step_type": "think",
                            "thoughts": extract_thought(step.model_output) if step.model_output else "",
                            "code": step.code_action,
                        }

                        tu = getattr(step, "token_usage", None)
                        if tu:
                            in_t = int(getattr(tu, "input_tokens", 0) or 0)
                            out_t = int(getattr(tu, "output_tokens", 0) or 0)
                            tot_t = int(getattr(tu, "total_tokens", in_t + out_t) or (in_t + out_t))

                            event["input_tokens"] = in_t
                            event["output_tokens"] = out_t
                            event["total_tokens"] = tot_t

                            current_sum_in += in_t
                            current_sum_out += out_t
                            current_sum_total += tot_t

                            total_in += in_t
                            total_out += out_t
                            total_tokens += tot_t

                        if step.observations_images:
                            event["image"] = encode_step_image(step.observations_images[-1])

                        yield json.dumps(event, ensure_ascii=False) + "\n"
                        await asyncio.sleep(0)

                        if step.is_final_answer:
                            final = step.action_output

                    elif isinstance(step, ChatMessageStreamDelta):
                        if step.content:
                            delta_buf.append(step.content)

                        now = monotonic()
                        if now - last_flush >= 0.03:
                            yield json.dumps({
                                "type": "delta",
                                "task_index": idx,
                                "task_total": len(tasks),
                                "step_type": "tokens",
                                "thoughts": "".join(delta_buf),
                            }, ensure_ascii=False) + "\n"
                            delta_buf.clear()
                            last_flush = now

                # дослать хвост дельты
                if delta_buf:
                    yield json.dumps({
                        "type": "delta",
                        "task_index": idx,
                        "task_total": len(tasks),
                        "step_type": "tokens",
                        "thoughts": "".join(delta_buf),
                    }, ensure_ascii=False) + "\n"
                    delta_buf.clear()

                # done по конкретной задаче + usage (как в /run)
                yield json.dumps({
                    "type": "done",
                    "task_index": idx,
                    "task_total": len(tasks),
                    "final": str(final),
                    "usage": {
                        "input_tokens": current_sum_in,
                        "output_tokens": current_sum_out,
                        "total_tokens": current_sum_total,
                    }
                }, ensure_ascii=False) + "\n"

                # подготовка данных для следующей задачи
                try:
                    last_mem_step = st.agent.memory.steps[-1]
                    if getattr(last_mem_step, "observations_images", None):
                        action_last_img = last_mem_step.observations_images[-1]
                except Exception:
                    pass

        except StopRequested:
            st.running = False
            st.stop_task = False

            # дослать хвост дельты, если остался
            if delta_buf:
                yield json.dumps({
                    "type": "delta",
                    "task_index": current_task_index,
                    "task_total": len(tasks),
                    "step_type": "tokens",
                    "thoughts": "".join(delta_buf),
                }, ensure_ascii=False) + "\n"
                delta_buf.clear()

            yield json.dumps({
                "type": "user_action_stop",
                "task_index": current_task_index,
                "task_total": len(tasks),
                "usage": {
                    "input_tokens": total_in,
                    "output_tokens": total_out,
                    "total_tokens": total_tokens,
                }
            }, ensure_ascii=False) + "\n"
            return

        except AgentError:
            st.running = False
            st.stop_task = False
            yield json.dumps({
                "type": "user_action_stop",
                "task_index": current_task_index,
                "task_total": len(tasks),
                "usage": {
                    "input_tokens": total_in,
                    "output_tokens": total_out,
                    "total_tokens": total_tokens,
                }
            }, ensure_ascii=False) + "\n"
            return

        except Exception as e:
            st.running = False
            yield json.dumps({
                "type": "error",
                "task_index": current_task_index,
                "task_total": len(tasks),
                "raw": str(e)
            }, ensure_ascii=False) + "\n"
            return

        finally:
            st.running = False

    return StreamingResponse(
        run(tasks, reasoning_efforts),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )




# -------------------------- СБРОС ДАННЫХ ------------------------------------------

@app.post("/stop")
async def stopping_agent(request: Request):
    st: AgentState = app.state.agent_state
    if st.running:
        st.stop_task = True
        try:
            st.agent.interrupt()
        except Exception:
            pass
        return JSONResponse({"ok": True, "status": "stop_requested"})
    return JSONResponse({"ok": True, "status": "idle"})


@app.post("/reset_session")
async def reset_session():
    st: AgentState = app.state.agent_state
    st.reset()
    return JSONResponse({"status": "reset_done"})



# ----------------------- ФОРМИРОВАНИЕ ОТЧЕТА ОБ ИСПОЛЬЗОВАНИИ

async def llm_gen(report: str, steps: str, **kwargs) -> str:
    steps = "\n".join(steps)
    prompt_for_report = [
        {"role": "system", "content": kwargs["system_prompt"]}, 
        {"role": "user", "content": f"{report}, шаги: {steps}"}
    ]
    payload = {
        "messages": prompt_for_report,
        "model": "deepseek-chat",
        "thinking": {
            "type": "disabled"
        },
        "frequency_penalty": 0,
        "max_tokens": 6000,
        "presence_penalty": 0,
        "response_format": {
            "type": "text"
        },
        "stop": None,
        "stream": False,
        "stream_options": None,
        "temperature": 1,
        "top_p": 1,
        "tools": None,
        "tool_choice": "none",
        "logprobs": False,
        "top_logprobs": None}
    try:
        async with httpx.AsyncClient(timeout=None, headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Authorization': f'Bearer {deepseek_token}'}) as client:
                response = await client.post("https://api.deepseek.com/chat/completions", json=payload)
                data = response.json()
                content = data["choices"][0]["message"]["content"]
    except Exception as e:
        print(e)
        content = "Отчет не сформирован!"
    
    finally:
        return content

@app.get("/action_trace")
async def form_action_trace():
    st: AgentState = app.state.agent_state
    trace = _build_action_trace_payload(st)

    if trace is None:
        return JSONResponse({"status": "У агента нет action-steps и памяти!"})

    return JSONResponse({"trace": trace})


@app.get("/report")
async def form_report(request: Request):
    st: AgentState = app.state.agent_state

    steps_out: list[str] = []
    answer: str = ""

    raw_steps = getattr(st, "steps", None)
    if not raw_steps or not isinstance(raw_steps, list):
        return JSONResponse({"status": "У агента нет шагов и памяти!"})

    total_input_cost = 0.0
    total_output_cost = 0.0

    # ---------- безопасное вычисление времени ----------
    run_start = None
    run_end = None

    if len(raw_steps) >= 2:
        run_start = getattr(getattr(raw_steps[1], "timing", None), "start_time", None)

    last_step = raw_steps[-1]
    run_end = getattr(getattr(last_step, "timing", None), "end_time", None)

    total_duration = (
        round(run_end - run_start, 2)
        if run_start is not None and run_end is not None
        else 0.0
    )

    # ---------- основной цикл ----------
    for i, step in enumerate(raw_steps):
        chunk = ""

        # ---------------- TASK STEP ----------------
        if isinstance(step, TaskStep):
            task_text = getattr(step, "task", "—")

            if i > 1:
                title = "📥 ЗАДАЧА ПРОДОЛЖЕНИЯ С ПОСЛЕДНЕГО УСПЕШНОГО ЭНДПОИНТА"
            else:
                title = "📥 TASK INPUT"

            chunk = (
                f"{HR_BOLD}\n"
                f"{title}\n"
                f"{HR}\n"
                f"{task_text}\n\n"
            )

        # ---------------- ACTION STEP ----------------
        elif isinstance(step, ActionStep):
            model_output = getattr(step, "model_output", "")

            try:
                thought = extract_thought(model_output)
            except Exception:
                thought = model_output or "—"

            token_usage = getattr(step, "token_usage", None)
            input_tokens = getattr(token_usage, "input_tokens", 0) or 0
            output_tokens = getattr(token_usage, "output_tokens", 0) or 0
            total_tokens = getattr(token_usage, "total_tokens", input_tokens + output_tokens)

            in_sum = round((input_tokens / 1_000_000) * 0 * 75, 2)
            out_sum = round((output_tokens / 1_000_000) * 0 * 75, 2)

            total_input_cost += in_sum
            total_output_cost += out_sum

            timing = getattr(step, "timing", None)
            start_time = getattr(timing, "start_time", None)
            end_time = getattr(timing, "end_time", None)

            duration = (
                round(end_time - start_time, 2)
                if start_time is not None and end_time is not None
                else 0.0
            )

            chunk = (
                f"{HR_BOLD}\n"
                f"🔹 ШАГ №{getattr(step, 'step_number', i)}\n"
                f"{HR}\n"

                f"{SECTION} ХОД РАССУЖДЕНИЙ\n"
                f"{thought}\n"
                f"{SUB_HR}\n"

                f"{SECTION} ВЫПОЛНЕННОЕ ДЕЙСТВИЕ / КОД\n"
                f"{getattr(step, 'code_action', '—')}\n"
                f"{SUB_HR}\n"

                f"{SECTION} РЕЗУЛЬТАТЫ ВЫПОЛНЕНИЯ\n"
                f"{getattr(step, 'observations', '—')}\n"
                f"{SUB_HR}\n"

                f"{SECTION} ИСПОЛЬЗОВАНИЕ ТОКЕНОВ И СТОИМОСТЬ\n"
                f"Входные токены : {input_tokens:<10} | Стоимость: {in_sum} руб\n"
                f"Выходные токены: {output_tokens:<10} | Стоимость: {out_sum} руб\n"
                f"Всего токенов  : {total_tokens}\n"
                f"{SUB_HR}\n"

                f"{SECTION} ВРЕМЯ\n"
                f"Начало генерации: {fmt_ts(start_time) if start_time else '—'}\n"
                f"Окончание       : {fmt_ts(end_time) if end_time else '—'}\n"
                f"Длительность    : {duration} сек.\n"
                f"{HR}\n\n"
            )

            if getattr(step, "is_final_answer", False):
                answer = (
                    str(getattr(step, "action_output", None))
                    if getattr(step, "action_output", None) is not None
                    else "Финальный ответ имеет значение None (Обратитесь в техническую поддержку)"
                )

        # ---------- добавление чанка ----------
        if chunk:
            steps_out.append(chunk)

    if not answer:
        answer = "Финальный ответ не сформирован."

    total_cost = round(total_input_cost + total_output_cost, 2)

    final_block = (
        f"{HR_BOLD}\n"
        f"🏁 ИТОГОВЫЙ РЕЗУЛЬТАТ\n"
        f"{HR}\n"
        f"{answer}\n\n\n\n\n\n"
        f"{SECTION} СТОИМОСТЬ ВЫПОЛНЕНИЯ\n"
        f"Входные токены (всего): {round(total_input_cost, 2)}\n"
        f"Выходные токены (всего): {round(total_output_cost, 2)}\n"
        f"Общая стоимость       : {total_cost} РУБЛЕЙ\n"
        f"Не включена стоимость оптимизационных сообщений +/- ~ 0.2 рублей\n"
        f"{SECTION} ВРЕМЯ ВЫПОЛНЕНИЯ\n"
        f"Общее время работы агента: {total_duration} сек.\n"
        f"{HR_BOLD}\n"
    )

    steps_out.append(final_block)

    return JSONResponse({"steps": "\n".join(steps_out)})

    

async def main(port: int):
    """
    Запуск агентского сервиса
    """
    config = uvicorn.Config(app, port=port)
    server = uvicorn.Server(config)

    await server.serve()



if __name__ == "__main__":
    load_dotenv(Path(__file__).resolve().with_name(".env"), override=False)
    deepseek_token = os.getenv("DEEPSEEK")
    port = int(os.getenv("PORT"))
    asyncio.run(main(port))
