# Architecture

## Overview

TMA Autotest Infra is AI QA infrastructure for Telegram Mini Apps and TON-related web flows. Its purpose is to convert an exploratory browser session performed by an agent into a repeatable automated regression check.

At the center of the system is an agent built around a vision-language model (VLM). The agent follows a reasoning + action execution pattern: it interprets the current state of the interface, decides what to do next, executes a browser action, observes the result, and continues until the task is complete.

This is important for UI automation because many product flows are not cleanly solvable through static selectors alone. Real interfaces contain visual state, overlays, popups, network selectors, language controls, wallet dialogs, and other context that is easier to handle with a perception-capable agent than with a rigid click recorder.

## Architectural Goal

The system is designed to separate two different responsibilities:

- intelligent exploration of the interface
- repeatable regression execution after the exploration is finished

The agent is responsible for discovering and completing the scenario. The generated autotest is responsible for replay-oriented verification. This separation makes the product useful as infrastructure for development teams rather than as a one-off interactive demo agent.

## Agentic Execution Model

### VLM-Based Agent

The exploratory layer is driven by an open-source VLM-based agent (created from CodeAgent smolagents) core that can reason over the visible browser state and use browser tools to move through the interface.

The agent follows a reasoning + action paradigm:

1. inspect the current UI state
2. infer the next meaningful step
3. call a browser action
4. observe the updated state
5. continue until the task reaches a completion condition

This makes the system suitable for flows where structure and appearance both matter, including Telegram Mini App interfaces and TON wallet connection scenarios.

### Reasoning + Action Loop

<p align="center">
  <img src="images/cua_diagram.png" width="600"/>
</p>

The agent loop is built around iterative decision making instead of static scripted steps. In practice, this means the system can:

- react to visual changes in the page
- navigate modal-driven interfaces
- perform scroll-dependent interactions
- handle multi-step user journeys
- produce a semantically meaningful execution trace

That trace becomes the bridge between intelligent exploration and deterministic replay.



## Core Pipeline

The full pipeline looks like this:

1. A task is submitted to the system.
2. The VLM-based agent explores the target interface in the browser.
3. The backend stores a structured trace of the execution.
4. A code generation stage transforms the trace into an autotest.
5. A deterministic runtime reruns the generated script.
6. The system stores artifacts for inspection and review.

In compact form:

`agent -> trace -> generated autotest -> rerun -> artifacts`

## Main Components

### 1. Agent Core

The agent core is responsible for executing exploratory tasks and acts as the central intelligence component of the system.

It combines:

- task understanding
- step-by-step reasoning
- browser tool selection
- completion detection

<p align="center">
  <img src="images/task.png" width="500"/>
</p>
The agent is optimized for actionability rather than pure text generation. Its primary purpose is to interact with the interface and produce a meaningful execution trace.

The agent architecture is tool-extendable. Additional tools can be attached to the agent to interact with the browser environment, including mechanisms such as XPath queries, DOM locators, and other interface interaction utilities.

The reasoning depth of the agent can be configured depending on the complexity of the task. This allows balancing exploration quality with execution speed for different scenarios.

Model providers are accessed through **OpenRouter**, which allows the system to dynamically switch between different LLM or VLM models depending on the task requirements.

The agent also includes a memory summarization mechanism. This allows long-running sessions to compress historical context and maintain operational continuity, enabling autonomous operation for extended periods (up to ~24 hours in long exploratory runs).

### 2. Browser Tooling and Runtime

The browser runtime provides the execution surface for the agent. It exposes the low-level actions needed to operate a real page, including:

- navigation
- clicking
- typing
- scrolling
- waiting
- reading visible state
- capturing screenshots

This layer is where reasoning becomes action.

for example click_xy tool:
'''python
@app.post("/click_xy")
async def click_xy(xy: dict):
    """
    Экранные координаты (root Xvfb) -> физический клик (xdotool).
    Телеметрия элемента:
      - основной источник истины: pointerdown probe (clicked_probe.target)
      - fallback: document.elementFromPoint() по screen->client маппингу (калибровка -> эвристика)
    """

    def _is_target_closed(e: Exception) -> bool:
        s = repr(e)
        return (
            ("TargetClosedError" in s)
            or ("has been closed" in s)
            or ("Execution context was destroyed" in s)
            or ("most likely because of a navigation" in s)
        )

    telemetry_errors: list[dict] = []

    def _tel_err(stage: str, err: str):
        telemetry_errors.append({"stage": stage, "error": err})

    async def _safe_eval_on_page(page: Page, script: str, arg=None, retries: int = 1):
        """
        Выполняем eval на конкретной page.
        Если target закрылся — пробуем переоткрыть page и повторить.
        """
        last = None
        p = page
        for _ in range(retries + 1):
            if not p or p.is_closed():
                p = await get_alive_page()
                if not p:
                    return None, "no_active_page"
                try:
                    await p.bring_to_front()
                except Exception:
                    pass
            try:
                if arg is None:
                    v = await _eval_pw_or_cdp(p, script)
                else:
                    v = await _eval_pw_or_cdp(p, script, arg)
                return v, None
            except Exception as e:
                last = e
                if _is_target_closed(e):
                    await asyncio.sleep(0.05)
                    continue
                return None, f"eval_error:{e}"
        return None, f"eval_retry_exhausted:{last}"

    # ---------- parse coords ----------
    try:
        x = int(float(xy.get("x")))
        y = int(float(xy.get("y")))
    except Exception:
        return {"status": "failed", "error": "bad_xy_payload"}

    # ---------- get page ----------
    p = await get_alive_page()
    if not p:
        return {"status": "failed", "error": "no_active_page"}

    # best-effort: активируем вкладку
    try:
        await p.bring_to_front()
    except Exception:
        pass

    # ---------- install click probe (best-effort) ----------
    try:
        await _eval_pw_or_cdp(p, CLICK_PROBE_INSTALL_JS)
    except Exception as e:
        _tel_err("click_probe_install", f"eval_error:{e}")

    # ---------- screen -> client mapping for elementFromPoint fallback ----------
    vx = vy = None
    vp_meta = None

    mapped = _screen_to_client_xy_using_calib(x, y)
    if mapped is not None:
        vx, vy = mapped
        vp_meta = {"source": "probe_calib", **COORD_CALIB}
    else:
        try:
            _vx, _vy, vp_meta = await _screen_xy_to_viewport_xy_precise(p, x, y)
            if 0 <= _vx < 100000 and 0 <= _vy < 100000:
                vx, vy = _vx, _vy
        except Exception as e:
            vp_meta = {"error": f"screen_to_viewport_failed:{e!r}"}

    # ---------- element (fallback, ДО клика) ----------
    el_before = None
    if vx is not None and vy is not None:
        el_before, err = await _safe_eval_on_page(
            p,
            """
            (p)=>{
              const {x,y} = p;
              const e = document.elementFromPoint(x,y);
              if (!e) return null;
              const r = e.getBoundingClientRect();

              const role = e.getAttribute('role') || e.tagName.toLowerCase();
              const name =
                e.getAttribute('aria-label') ||
                e.getAttribute('title') ||
                (e.innerText||'').trim().slice(0,120) || null;

              const tag = e.tagName.toLowerCase();
              const type =
                e.type || (
                  tag === 'button' ? 'button' :
                  tag === 'a' ? 'link' :
                  tag === 'input' ? 'input' :
                  tag === 'select' ? 'select' :
                  tag === 'textarea' ? 'textarea' : 'generic'
                );

              const sameType = Array.from(document.querySelectorAll(tag));
              const index = sameType.indexOf(e) + 1;

              return {
                tag, type, role, name,
                index, total: sameType.length,
                cls: e.className || '',
                bbox: {x:r.x, y:r.y, w:r.width, h:r.height}
              };
            }
            """,
            {"x": int(vx), "y": int(vy)},
            retries=1,
        )
        if err:
            _tel_err("element_eval", err)
            el_before = None
    else:
        _tel_err("element_eval", "point_outside_viewport_or_no_mapping")

    # ---------- BEFORE telemetry ----------
    before, err = await _safe_eval_on_page(
        p,
        """
        () => {
          const active = document.activeElement ? document.activeElement.tagName.toLowerCase() : null;
          const txt = (document.body && document.body.innerText) ? document.body.innerText : "";
          return { url: location.href, active, textLen: txt.length };
        }
        """,
        retries=1,
    )
    if err:
        _tel_err("before_eval", err)
        before = None

    # ---------- PHYSICAL CLICK (screen coords) ----------
    sx, sy = x, y
    ok, click_err = await _os_click_xy(sx, sy, hold_ms=40)
    if not ok:
        return {
            "status": "failed",
            "error": click_err,
            "clicked": {"x": x, "y": y},
            "telemetry_partial": bool(telemetry_errors),
            "telemetry_errors": telemetry_errors,
        }

    # ---------- read probe (истина) ----------
    await asyncio.sleep(0.03)  # дать pointerdown записаться
    probe = None
    try:
        probe = await _eval_pw_or_cdp(p, CLICK_PROBE_READ_JS)
    except Exception as e:
        _tel_err("click_probe_read", f"eval_error:{e}")
        probe = None

    # ---------- update calibration from probe ----------
    if isinstance(probe, dict):
        off = probe.get("screen_to_client_offset_css")
        if isinstance(off, dict) and ("dx" in off) and ("dy" in off):
            async with COORD_CALIB_LOCK:
                COORD_CALIB["dx"] = float(off.get("dx") or 0.0)
                COORD_CALIB["dy"] = float(off.get("dy") or 0.0)
                COORD_CALIB["dpr"] = float(probe.get("dpr") or 1.0)
                COORD_CALIB["ts"] = int(probe.get("ts") or 0)
                COORD_CALIB["url"] = probe.get("url")

    # ---------- AFTER telemetry ----------
    after, err = await _safe_eval_on_page(
        p,
        """
        () => {
          const active = document.activeElement ? document.activeElement.tagName.toLowerCase() : null;
          const txt = (document.body && document.body.innerText) ? document.body.innerText : "";
          const popupCandidates = Array.from(document.querySelectorAll(
            'dialog[open],[role="dialog"],[aria-modal="true"],[role="menu"],[role="listbox"]'
          ));
          const isVisible = (el) => {
            if (!(el instanceof HTMLElement)) return false;
            const st = getComputedStyle(el);
            if (st.display === "none" || st.visibility === "hidden" || Number(st.opacity || "1") < 0.05) return false;
            const r = el.getBoundingClientRect();
            return r.width > 4 && r.height > 4;
          };
          const hasPopup = popupCandidates.some(isVisible);
          return { url: location.href, active, textLen: txt.length, hasPopup };
        }
        """,
        retries=1,
    )
    if err:
        _tel_err("after_eval", err)
        after = None

    # ---------- signals ----------
    if isinstance(before, dict) and isinstance(after, dict):
        url_changed = (after.get("url") != before.get("url"))
        focus_changed = (after.get("active") != before.get("active"))
        text_changed = (after.get("textLen") != before.get("textLen"))
        popup_visible = bool(after.get("hasPopup"))
        activated = bool(url_changed or popup_visible or (focus_changed and text_changed))

        signals = {
            "url_changed": url_changed,
            "focus_changed": focus_changed,
            "viewport_text_changed": text_changed,
            "popup_visible": popup_visible,
        }
    else:
        activated = None
        signals = {
            "url_changed": None,
            "focus_changed": None,
            "viewport_text_changed": None,
            "popup_visible": None,
        }

    # ---------- choose aria: probe target > elementFromPoint fallback ----------
    aria_real = None
    if isinstance(probe, dict) and isinstance(probe.get("target"), dict):
        aria_real = probe["target"]
    else:
        aria_real = el_before

    return {
        "status": "success",
        "clicked": {"x": x, "y": y, "aria": aria_real},
        "activated": activated,
        "signals": signals,
        "telemetry_partial": bool(telemetry_errors),
        "telemetry_errors": telemetry_errors,
        "telemetry": {"before": before, "after": after},
        "coord_map": None,
        "viewport_xy": {"x": vx, "y": vy, "meta": vp_meta},
        "clicked_probe": probe,
        "coord_calib": dict(COORD_CALIB),
    }
'''

### 3. Backend Orchestration

The backend coordinates the lifecycle of each run:

- task submission
- session state
- trace capture
- script generation
- rerun execution
- artifact persistence

This layer turns a one-time agent session into a structured and inspectable workflow.

### 4. Trace Layer

The trace layer stores the operational history of the session in a structured format. It is not just a raw event dump. It acts as the intermediate representation between exploration and replay.

The trace is valuable because it preserves:

- what the agent attempted
- what actually happened in the UI
- how the scenario progressed step by step

### 5. Trace-to-Test Generation

Once the exploratory run is complete, the system uses the stored trace as the source material for automated test generation.

This stage converts a successful exploratory run into a reusable software artifact: an autotest that can be executed again later as a regression check.

### 6. Deterministic Test Runtime

The generated test is executed in a replay-oriented runtime. This separates exploration from verification:

- exploration is flexible and agent-driven
- regression is repeatable and test-driven

This architectural split is one of the key strengths of the system.

### 7. Artifact and Reporting Layer

The system stores artifacts that make runs inspectable by humans:

- screenshots
- timeline events
- run diagnostics
- final status

These outputs make the infrastructure useful not only for automation, but also for QA review, debugging, and demo presentation.

## Why VLM Matters Here

A VLM-based agent is especially useful for Telegram Mini Apps and TON-related interfaces because these flows often depend on visible context rather than purely stable markup.

Examples include:

- wallet connection modals
- language selectors
- network selectors
- QR-driven connection flows
- dynamic interface blocks revealed only after scrolling

A perception-capable agent can handle these flows more naturally than a rigid recorder-only approach.

## Why This Is More Than Record-and-Replay

Traditional record-and-replay tools usually capture literal low-level actions and depend heavily on stable UI structure. TMA Autotest Infra instead uses an agentic exploration stage first, then turns the resulting trace into a repeatable regression artifact.

That means the product is better described as AI-assisted QA infrastructure than as a simple test recorder.

## Future MCP Surface

The current submission focuses on the working QA pipeline, not on exposing every capability as a public protocol surface. However, the architecture is intentionally compatible with future MCP-style packaging.

In a later iteration, the same infrastructure can be exposed through an MCP-compatible interface so other agents or developer tools can call capabilities such as:

- create and start exploratory runs
- retrieve traces
- trigger trace-to-test generation
- launch reruns
- fetch artifacts and run status

This means the current architecture can evolve from an internal QA system into a more general agent tooling surface without changing its fundamental design.

## Public vs Private Materials

This showcase repository contains only public submission-facing materials.

The private core repository contains the working implementation of:

- agent orchestration
- browser execution services
- trace capture flow
- trace-to-test generation
- autotest runtime
- internal product UI and integration logic
