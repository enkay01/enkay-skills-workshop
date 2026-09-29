# Product roadmap: a desktop tool for vision models

Updated 29 September 2026. Read this first when choosing the next implementation task. Use IMPLEMENTATION-PLAN.md for engine details; this document defines the product destination.

## Destination

A user supplies a task to a vision-capable model. The model receives an image of the desktop or selected window, requests mouse/keyboard or semantic actions, sees the result, and continues across applications. The Windows engine is independent of the model provider. Provider adapters translate image and action formats without changing the engine.

The tool should attempt ordinary Windows desktop applications through general input and screenshots, with UI Automation where available. Application-specific OCR profiles are optional accelerators. They must not be required for general use. Protected desktops, capture restrictions, elevated targets, and applications rejecting input remain explicit capability limits; universal successful task completion is not a promise.

## Position today

The user reports phases 1–4 complete and another agent planning phase 5. Source inspection confirms a persistent Rust engine and Python client with window discovery/attachment, raw image observations, UIA inspection/actions, and a click entry point. OCR and a Continue workflow are also present.

README and implementation-results.md claim phases 0–6 are verified. That conflicts with the user's reported status. The roadmap author inspected source but did not rerun desktop tests. Treat guarded input and workflow completion as awaiting reconciliation with the active implementation agent and its evidence. Code presence is not a verified milestone.

A first vision-model session is now demonstrated against the native fixture: the model chose a click from a screenshot, the guarded engine dispatched it, a later screenshot led the model to report success, and the fixture counter independently read 1. Moving the window after observation stopped the click. The evidence is in `research/model-controlled-interaction-results.md`.

The generic action set is now complete: `click` (left/right, single/double), `type_text`, `press_key`, `scroll`, `hover`, `drag`, and `focus_window`. Every action runs the same pre-dispatch guard, decomposed from click's original inline checks into one reusable implementation in `engine/src/guard.rs`, so a new action cannot acquire a weaker safety check than click. All effects were confirmed by reading the application's own reported state, and every refusal was confirmed to have dispatched nothing. 27 new tests plus the 40 pre-existing tests pass, the latter unchanged, which is the regression evidence for the guard refactor. Measured dispatch is 0.65–3.8 ms for targeted actions; latency is recorded, not enforced as a threshold. Evidence and limits are in `research/desktop-action-expansion-results.md`. That closes the action-surface part of milestone 2; the model-facing tool and general agent loop are untouched by it.

## Delivery milestones

| Milestone | Deliverable | Completion evidence |
| --- | --- | --- |
| 1. Desktop engine | Persistent capture, window identities, UIA, local client | Existing phases 1–4 evidence, with limitations recorded |
| 2. Reliable interaction | Guarded click plus keyboard text/keys, scrolling, mouse movement, drag, and explicit window focus/switching | [Done] Real fixture actions with fresh observations and correct failure reporting; see `research/desktop-action-expansion-results.md` |
| 3. Model-facing tool | Stable observe/action API returning model-readable images and metadata | One model sees an image, chooses an action, receives a result, and observes the actual change |
| 4. General agent loop | Provider adapter, bounded observe/act/verify loop, cancellation, recovery, and session history | A multi-step task involving native apps and a custom-rendered interface without a bespoke OCR profile |
| 5. Provider portability and delivery | Second model adapter, installation/startup instructions, capabilities and troubleshooting | Same engine and action schema used by two vision models; repeatable setup and task traces |

## Next work in order

1. Let the phase-5 agent finish guarded clicking. Reconcile existing phase-5/6 code and report claims before duplicating work. Preserve foreground, geometry, and observation checks.
2. Demonstrate the smallest real model loop immediately: capture -> encode an image in memory -> model chooses a click -> dispatch -> fresh image -> model verifies the result. Use a controlled desktop fixture. OCR must be optional.
3. [Completed - Issue #4] Generic actions for actual desktop tasks: type text, key chords, scroll, right/double click, move/hover, drag, window discovery, and explicit focus/switch, all sharing one guard. Not yet extended to cross-window dialogs and menus, which remains open.
4. [Completed - Issue #3] Desktop/monitor overview capture: monitor enumeration, Windows Graphics Capture for monitors, coordinate scaling/inversion transforms, guarded overview clicks with hit-testing, and window focusing contract implemented and tested.
5. Cross-window dialogs and menus: the action set is per-attached-window, and a dialog owned by a different top-level window is not yet a first-class target. Decide whether dialogs become attachable targets or require an explicit switch first.
6. Exercise the `inject()` release path. It releases held buttons and modifiers on a partial `SendInput` failure and never retries, but provoking a genuine partial failure safely is unresolved, so it is implemented and reasoned about rather than tested.
7. Run cross-application tasks, then plug in a second model provider without changing the Rust engine. Implement a host adapter such as MCP when the intended host supports it; keep the core API usable through ordinary model API calls too.

## Contracts that the model layer must resolve

- Images: encode PNG/JPEG in memory only when requested by the model. Preserve raw frames internally. Record scaling/cropping and map model coordinates back to the original observation.
- Actions: validate structured JSON before execution; distinguish dispatched, failed, and verified outcomes. A provider without function calling can use a validated JSON response adapter.
- Freshness: the client's current 500 ms click age default is shorter than many vision-model turns. Obtain a fresh observation before execution and compare the relevant target/window state. On meaningful change, return updated evidence for a new decision. Merely raising the timeout or assigning the old target a new timestamp is insufficient.
- Focus: an occluded window can be captured while another app receives global input. Explicitly focus and re-observe before injecting input. Switching windows must invalidate old coordinates.
- Model independence: the model supplies decisions; the engine supplies desktop operations. Model-specific prompts and response parsing live in small provider adapters.
- Scope: recognition, task planning, and correct interpretation of arbitrary apps depend on model ability. The tool's responsibility is faithful observations, well-defined actions, and honest errors.

## Role of Football Manager

Football Manager exercises an interface with little useful accessibility data. Its Continue loop is one optional regression scenario, not the product's central architecture. A hard-coded Continue rule alone does not demonstrate model-driven computer use. General screenshot-based interaction should work before further game-specific optimization.

## Progress reporting rule

Every future handoff should state: product milestone advanced, user-visible capability added, evidence observed, remaining limitations, and the next milestone. Keep code status, test status, and product readiness separate. Prefer proving the general model loop over further capture/OCR optimization until an actual task trace identifies a bottleneck.
