# Product roadmap: a desktop tool for vision models

Updated 29 September 2026. Read this first when choosing the next implementation task. Use IMPLEMENTATION-PLAN.md for engine details; this document defines the product destination.

## Destination

A user supplies a task to a vision-capable model. The model receives an image of the desktop or selected window, requests mouse/keyboard or semantic actions, sees the result, and continues across applications. The Windows engine is independent of the model provider. Provider adapters translate image and action formats without changing the engine.

The tool should attempt ordinary Windows desktop applications through general input and screenshots, with UI Automation where available. Application-specific OCR profiles are optional accelerators. They must not be required for general use. Protected desktops, capture restrictions, elevated targets, and applications rejecting input remain explicit capability limits; universal successful task completion is not a promise.

## Position today

The user reports phases 1–4 complete and another agent planning phase 5. Source inspection confirms a persistent Rust engine and Python client with window discovery/attachment, raw image observations, UIA inspection/actions, and a click entry point. OCR and a Continue workflow are also present.

README and implementation-results.md claim phases 0–6 are verified. That conflicts with the user's reported status. The roadmap author inspected source but did not rerun desktop tests. Treat guarded input and workflow completion as awaiting reconciliation with the active implementation agent and its evidence. Code presence is not a verified milestone.

A first vision-model session is now demonstrated against the native fixture: the model chose a click from a screenshot, the guarded engine dispatched it, a later screenshot led the model to report success, and the fixture counter independently read 1. Moving the window after observation stopped the click. The evidence is in `research/model-controlled-interaction-results.md`. The current Python client still lacks the complete generic keyboard, scroll, drag, and application-switching action set.

## Delivery milestones

| Milestone | Deliverable | Completion evidence |
| --- | --- | --- |
| 1. Desktop engine | Persistent capture, window identities, UIA, local client | Existing phases 1–4 evidence, with limitations recorded |
| 2. Reliable interaction | Guarded click plus keyboard text/keys, scrolling, mouse movement, drag, and explicit window focus/switching | Real fixture/app actions with fresh observations and correct failure reporting |
| 3. Model-facing tool | Stable observe/action API returning model-readable images and metadata | One model sees an image, chooses an action, receives a result, and observes the actual change |
| 4. General agent loop | Provider adapter, bounded observe/act/verify loop, cancellation, recovery, and session history | A multi-step task involving native apps and a custom-rendered interface without a bespoke OCR profile |
| 5. Provider portability and delivery | Second model adapter, installation/startup instructions, capabilities and troubleshooting | Same engine and action schema used by two vision models; repeatable setup and task traces |

## Next work in order

1. Let the phase-5 agent finish guarded clicking. Reconcile existing phase-5/6 code and report claims before duplicating work. Preserve foreground, geometry, and observation checks.
2. Demonstrate the smallest real model loop immediately: capture -> encode an image in memory -> model chooses a click -> dispatch -> fresh image -> model verifies the result. Use a controlled desktop fixture. OCR must be optional.
3. Expand the generic actions for actual desktop tasks: type text, key chords, scroll, right/double click, move/hover, drag, window discovery, and explicit focus/switch. Support cross-window dialogs and menus. Keep actions tied to observation/window identity.
4. Add desktop/monitor overview capture so the model can locate apps, taskbar, and popups outside the selected window. Define physical coordinates, crop/resize transforms, monitor identity, and observation IDs in the tool contract.
5. Run cross-application tasks, then plug in a second model provider without changing the Rust engine. Implement a host adapter such as MCP when the intended host supports it; keep the core API usable through ordinary model API calls too.

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
