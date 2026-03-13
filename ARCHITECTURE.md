# Architecture

## Goal

TMA Autotest Infra is built to convert exploratory UI execution into repeatable automated regression checks for Telegram Mini Apps and TON-related web flows.

## Core Pipeline

1. The agent receives a browser task.
2. The agent completes the scenario using browser actions.
3. The backend stores a structured trace of the execution.
4. A code generation step produces an autotest from the trace.
5. A deterministic runtime reruns the generated script.
6. The system stores artifacts for inspection.

## Main Components

### Agent Layer

Responsible for exploratory execution of the task in the browser. It behaves like a user and interacts with the page through browser tools.

### Browser Runtime

Provides the execution environment for page navigation, clicks, scrolling, text input, waiting, and other browser-level interactions.

### Backend Orchestration

Coordinates the lifecycle of a run:

- task submission
- trace collection
- script generation
- rerun execution
- artifact storage

### Trace-to-Test Generation

Transforms the stored action trace into a repeatable automated test script.

### Deterministic Test Runtime

Executes the generated test in a replay-oriented environment and produces stable run outputs.

### Artifact Layer

Stores screenshots, timeline events, diagnostics, and final run status.

## Why This Architecture Matters

The architecture separates exploratory intelligence from repeatable verification:

- the agent is used for discovery
- the generated autotest is used for regression

That separation makes the system useful as infrastructure for development teams instead of a one-off interactive agent.

## Public vs Private Materials

This showcase repository contains only public-facing submission materials.

The private core repository contains the working implementation of:

- backend endpoints and orchestration
- browser service and execution layer
- trace capture flow
- trace-to-test generation
- autotest runtime and internal UI
