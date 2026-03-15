# TMA Autotest Infra
![OperatorVLM](images/logo.jpg)
AI QA infrastructure for Telegram Mini Apps and TON-related web flows.

The product turns an exploratory browser session completed by an AI agent into a repeatable automated regression test. The system captures the action trace, generates an autotest from that trace, reruns the generated script, and stores run artifacts such as screenshots, timeline events, and diagnostics.

## Hackathon Track

TON AI Agent Hackathon 2026  
Track 1: Agent Infrastructure

## Problem

Teams building Telegram Mini Apps and TON-related web products constantly change onboarding steps, wallet connection flows, and interface logic. Manual regression checks are slow, and traditional record-and-replay tools are often too fragile when the UI evolves.

## Solution

TMA Autotest Infra provides an AI-assisted QA pipeline:

1. An AI agent explores the interface in a browser.
2. The backend stores the action trace step by step.
3. The system generates an automated test from the saved trace.
4. The generated test is rerun in a deterministic execution flow.
5. The team receives screenshots, timeline events, and final run artifacts.

This makes it possible to move from exploratory UI checking to repeatable regression coverage much faster.

## Demo Scenario

Current public target:

`https://tonconnect-sdk-demo-dapp.vercel.app/`

Current validated flow:

1. Open the official TON Connect demo dApp.
2. Switch `Any Network` to `Testnet`.
3. Scroll down and select the `ru` language.
4. Scroll back up and open `Connect Wallet`.
5. Press the copy-link control next to the QR code.
6. Save the action trace.
7. Generate an autotest from the trace.
8. Rerun the generated autotest and inspect run artifacts.

This flow has already been validated end-to-end in the core product.

## What Makes It Different

This is not a simple click recorder. The interface is first explored by an AI agent, and the resulting trace becomes the source for a separate repeatable automated test. That makes the system closer to AI-assisted QA infrastructure than to a basic record-and-replay utility.

## Repository Contents

- [`ARCHITECTURE.md`](./ARCHITECTURE.md) - high-level system structure and component responsibilities
- [`DEMO.md`](./DEMO.md) - live demo walkthrough and expected results
- [`ADDITIONAL_NOTES.md`](./ADDITIONAL_NOTES.md) - notes for judges about the private core implementation

## Private Core Notice

The core implementation of the product is currently private. This public repository is a showcase repository for the hackathon submission and intentionally contains:

- product overview
- architecture summary
- validated demo flow
- submission-facing documentation

The private repository contains the production backend, browser runtime, trace-to-test generation flow, and internal integrations.

## Current Status

The MVP already works end-to-end on the public TON Connect demo target:

- exploratory run works
- action trace is captured
- autotest generation works
- generated test rerun works
- artifacts are available for inspection

## Scope of This Submission

The current submission focuses on QA infrastructure for Telegram Mini Apps and TON-related web flows. It does not focus on TON payments or on a consumer-facing chat agent. The core value is the pipeline:

`explore -> trace -> generate test -> rerun -> inspect artifacts`
