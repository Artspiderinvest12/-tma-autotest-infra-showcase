# Demo Walkthrough

## Goal

Show that the product can turn an AI-driven exploratory browser session into a repeatable automated regression test.

## Public Target

`https://tonconnect-sdk-demo-dapp.vercel.app/`

## Demo Flow

1. Open the product UI.
2. Launch the preset `TON Connect Demo Smoke`.
3. Let the agent open the target page.
4. Switch the network from `Any Network` to `Testnet`.
5. Scroll down and select the `ru` language.
6. Scroll back up and press `Connect Wallet`.
7. In the modal, press the copy-link control next to the QR code.
8. Finish the exploratory run.
9. Save the trace.
10. Generate an autotest from the trace.
11. Rerun the generated autotest.
12. Show screenshots, timeline events, and final run artifacts.

## What Judges Should See

- a successful exploratory run on a public TON-related target
- a captured action trace
- a generated automated test
- a successful rerun of the generated test
- inspectable run artifacts

## Expected Value

The demo should make the product value obvious:

- AI is used for exploration
- the trace becomes a reusable testing asset
- regression checks can be rerun after changes

## Suggested Video Order

1. Show the preset and target URL.
2. Show the agent completing the flow.
3. Show the saved trace.
4. Show the generated autotest.
5. Show the rerun.
6. End on artifacts and final run status.
