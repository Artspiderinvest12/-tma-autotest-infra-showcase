# TON Mini App QA Infra — AI-assisted regression infrastructure


<p align="center">
  <img src="images/logo.jpg" width="420"/>
</p>

<p align="center">

<a href="https://identityhub.app/contests/ai-hackathon">
<img src="https://img.shields.io/badge/Hackathon-Page-black"/>
</a>

<a href="https://operatorvlm.ru">
<img src="https://img.shields.io/badge/Demo-operatorvlm.ru-green"/>
</a>

<a href="https://t.me/pn_test_auth_bot">
<img src="https://img.shields.io/badge/Telegram-Bot-blue"/>
</a>

<a href="./ARCHITECTURE.md">
<img src="https://img.shields.io/badge/Docs-Architecture-lightgrey"/>
</a>

</p>

AI-assisted regression infrastructure for Telegram Mini Apps on TON.

The system turns an exploratory browser session executed by an AI agent into a repeatable automated regression test.  
It allows teams to validate UI flows, capture interaction traces, generate automated tests, inspect execution artifacts, and apply TON-aware checks to critical wallet and connection scenarios.

---

# Hackathon Track

TON AI Agent Hackathon 2026  
Track 1: Agent Infrastructure

---

# Problem

Teams building Telegram Mini Apps and TON-native web products constantly change onboarding flows, wallet connection steps, network selectors, modal logic, and release-critical UI behavior.

Manual regression testing is slow and expensive, while traditional record-and-replay tools tend to break when the UI changes.

As a result, TON builder teams often lack reliable regression coverage for critical interaction flows such as:

- opening `TON Connect`
- switching `Any Network` to `Testnet`
- checking wallet connection methods
- validating wallet modal visibility
- rechecking copy-link and related wallet entry points

---

# Solution

<p align="center">
  <img src="images/menu.jpg" width="420"/>
</p>

The system provides an AI-assisted QA pipeline:

1. The user describes the verification goal and configures the reasoning level for the agent.
2. The AI agent explores the interface inside a browser environment, interacting with the UI similarly to a human user.
3. During exploration, the backend records a structured action trace.
4. The trace is distilled into a deterministic replay autotest.
5. The generated autotest can later be rerun on demand or by schedule.
6. After execution, the team receives a final report with screenshots, timeline events, diagnostics, and TON-aware checks.

The report includes either:

- confirmation of successful execution, or
- recommendations describing problems detected during the replay run

This approach allows TON teams to move much faster from exploratory UI testing to repeatable regression coverage.

Additionally, it helps evaluate how understandable and usable the interface is from the perspective of an autonomous agent.

---

# TON QA Pack

This submission includes a TON-aware assertion layer on top of the generic replay runtime.

Current TON-aware checks cover:

- TON Connect markers on the page
- `Testnet` visibility
- `Connect Wallet` visibility
- TON Connect modal visibility
- wallet method presence
- `Copy link` visibility
- optional Telegram Mini App context markers

This means the platform is not only replaying generic browser actions. It can also validate TON-specific UI states that matter to teams shipping Telegram Mini Apps and wallet-related flows.

---

# MCP Integration

The platform now exposes a lightweight MCP adapter over the backend.

Available MCP tools:

- `run_exploration`
- `save_trace`
- `generate_autotest`
- `run_autotest`
- `get_run_status`
- `get_run_artifact`

This makes the infrastructure usable not only through the main UI, but also by external agent systems that need to trigger exploratory runs, generate autotests, and fetch run artifacts programmatically.

---

# Demo Scenario

Current public target:

`https://tonconnect-sdk-demo-dapp.vercel.app/`

Validated interaction flow:

<p align="center">
<a href="https://youtu.be/AuMUIWpe7jE">
  <img src="images/specs.jpg" width="720"/>
</a>
</p>

<p align="center">
▶ Watch demo
</p>

1. Open the official TON Connect demo dApp.
2. Switch `Any Network` to `Testnet`.
3. Scroll down and select the `ru` language.
4. Scroll back up and open `Connect Wallet`.
5. Press the copy-link control next to the QR code.
6. Save the action trace.
7. Generate an autotest from the trace.
8. Rerun the generated autotest and inspect run artifacts.

This scenario has already been validated end-to-end in the product.

The demo shows:

- how the agent reasons about the interface
- how it interacts with UI elements
- how the exploratory run becomes a replayable regression asset
- how TON-aware checks are attached to the replay flow

---

# What Makes It Different

This is not a simple click recorder.

The interface is first explored by an AI agent capable of reasoning about UI elements and forming interaction hypotheses.

The resulting interaction trace becomes the source for a separate repeatable automated test.

The replay runtime can then apply TON-aware checks to wallet and connection-related scenarios.

This makes the system closer to **AI-assisted QA infrastructure for TON builders** rather than a traditional record-and-replay tool.

---

# Repository Contents

- [`ARCHITECTURE.md`](./ARCHITECTURE.md) — system architecture and component responsibilities
- [`DEMO.md`](./DEMO.md) — demo walkthrough and expected results
- [`ADDITIONAL_NOTES.md`](./ADDITIONAL_NOTES.md) — limitations and submission notes

---

# Current Status

The MVP already works end-to-end on the public TON Connect demo target.

Current capabilities include:

- exploratory agent run
- interaction trace capture
- autotest generation
- deterministic test rerun
- artifact inspection
- TON-aware checks in replay artifacts
- MCP-facing integration surface

---

# Scope of This Submission

This submission focuses on **builder infrastructure for Telegram Mini Apps and TON Connect flows**.

It does not focus on:

- TON payments
- real on-chain execution
- wallet approvals inside external wallet apps
- a consumer-facing chat agent

The core value of the system is the pipeline:

```
explore -> trace -> generate test -> rerun -> inspect artifacts
```

At the current stage, the platform is aimed at UI regression and release-safety workflows for TON teams.

---

# Additional Notes

- Demo walkthrough: [DEMO.md](./DEMO.md)
- Architecture: [ARCHITECTURE.md](./ARCHITECTURE.md)
- Limitations and submission notes: [ADDITIONAL_NOTES.md](./ADDITIONAL_NOTES.md)
