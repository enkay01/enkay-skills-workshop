# Product roadmap: a direct-agent desktop tool

Updated 30 September 2026. Read this first when choosing the next implementation task. Use IMPLEMENTATION-PLAN.md for engine details; this document defines the product destination.

## Destination

A user asks their own agent (Antigravity, Codex, Claude, or another terminal-capable host) to use the Windows computer. That calling agent observes the desktop through `wcu` screenshots, chooses actions, dispatches guarded input through the Rust engine, and verifies the outcome from later evidence. There is no model endpoint, no proxy, no provider adapter, and no second model call. The Windows engine is independent of the calling host.

The tool attempts ordinary Windows desktop applications through general input and screenshots, with UI Automation where available. Application-specific OCR profiles are optional accelerators. They must not be required for general use. Protected desktops, capture restrictions, elevated targets, and applications rejecting input remain explicit capability limits; universal successful task completion is not a promise.

## Position today

Shipped and verified on `main`: persistent engine + client (phases 1–4), desktop overview (#3), full guarded action set (#4), and the direct-agent CLI + skill (#7). The proxy vision-model session (#2) and tool-owned agent loop (#6) are closed as superseded; their research history stays in `research/` for reference only.

The direct-agent CLI and skill are now shipped (Issue #7, merged to `main` as `3de771c`): installable `wcu` command with a persistent per-desktop session, stable result envelope, guarded `act` command, focused inspection, history, timeouts, cancellation, and cleanup, plus a companion skill. Acceptance passed on the real CLI in two steps: Notepad commit (`tests/manual/notepad_commit.py` PASS, byte-perfect, clipboard untouched, window closed) and Antigravity focus + X close. Regression: engine 2/2, Phase 7 30/30, CLI suites 14/14, phases 1–3 8/8.

The generic action set is complete (Issue #4, `d0ff75f` plus follow-ups through `c168e75`): `click` (left/right, single/double), `type_text` (unicode/paste/commit), `press_key`, `scroll`, `hover`, `drag`, and `focus_window`. Every action runs the same pre-dispatch guard in `engine/src/guard.rs`. All effects were confirmed by reading the application's own reported state, and every refusal was confirmed to have dispatched nothing. Evidence and limits are in `research/desktop-action-expansion-results.md` and `docs/windows-text-input-apis.md`.

Retired direction: Issues #2 (proxy vision-model slice) and #6 (model runner / general agent loop) are closed as superseded by #7. Earlier milestones below that assume a provider adapter or a tool-owned model loop no longer apply; the calling host agent owns planning, model calls, and task-completion judgments. The proxy experiments remain in research history only.

## Delivery milestones

| Milestone | Deliverable | Completion evidence |
| --- | --- | --- |
| 1. Desktop engine | Persistent capture, window identities, UIA, local client | Existing phases 1–4 evidence, with limitations recorded |
| 2. Reliable interaction | Guarded click plus keyboard text/keys, scrolling, mouse movement, drag, and explicit window focus/switching | [Done — Issue #4] Real fixture actions with fresh observations and correct failure reporting; see `research/desktop-action-expansion-results.md` |
| 3. Desktop overview | Monitor enumeration, Graphics Capture for monitors, transforms, overview clicks | [Done — Issue #3] Implemented and tested |
| 4. Direct-agent delivery | Installable CLI + companion skill, persistent session, guarded act, no model endpoint | [Done — Issue #7] Merged as `3de771c`; Notepad + Antigravity acceptance on the real CLI |
| 5. ~~Model-facing proxy tool~~ | ~~Stable observe/action API for a tool-owned vision model~~ | [Superseded by #7] No proxy, adapter, or second model call ships |
| 6. ~~General agent loop~~ | ~~Provider adapter, tool-owned observe/act/verify loop~~ | [Superseded by #7] Calling host agent owns planning and verification |

## Next work in order

1. [Completed - Issue #4] Generic actions for actual desktop tasks: type text, key chords, scroll, right/double click, move/hover, drag, window discovery, and explicit focus/switch, all sharing one guard. Cross-window dialogs and menus remain open (item 6).
2. [Completed - Issue #3] Desktop/monitor overview capture: monitor enumeration, Windows Graphics Capture for monitors, coordinate scaling/inversion transforms, guarded overview clicks with hit-testing, and window focusing contract implemented and tested.
3. [Completed - Issue #7] Direct-agent CLI and skill: persistent session, `act` revalidation, focused inspection, history, timeouts, cancellation, cleanup, install docs, two-step acceptance.
4. Decide the fate of `act type --method unicode` (`SendInput` + `KEYEVENTF_UNICODE`). It is byte-perfect on the WinForms fixture but reproducibly mangled by Win11 Notepad's TSF layer; commit (verified `EM_REPLACESEL`) is the documented path for editors and paste is the fallback. Options: flip the default to commit and keep unicode as explicit legacy for non-editor targets (games, terminals), or remove it. Current default is still unicode in engine/CLI/client.
5. Fix the `clipboard.rs` OLE-wrapper refusal: `snapshot()` rejects `DataObject` (49161) / `Ole Private Data` (49171) even when plain text is present, making `--method paste` flaky whenever text was copied via OLE.
6. Cross-window dialogs and menus: the action set is per-attached-window, and a dialog owned by a different top-level window is not yet a first-class target. Decide whether dialogs become attachable targets or require an explicit switch first.
7. Exercise the `inject()` release path. It releases held buttons and modifiers on a partial `SendInput` failure and never retries, but provoking a genuine partial failure safely is unresolved, so it is implemented and reasoned about rather than tested.
8. Run cross-application tasks under the direct-agent contract (calling agent drives `wcu` across two apps, no bespoke selectors). Implement a host adapter such as MCP only if an intended host requires it; keep the core CLI usable as-is.

## Contracts the calling agent must honor (tool enforces, agent decides)

- Images: `wcu observe` / `switch` return PNG on disk plus dimensions, physical bounds, and transforms. Act in the pixels of the observation you saw; a new window, monitor, or switch invalidates prior coordinates.
- Actions: one guarded `act` per decision; `refused` returns fresh evidence — reconsider, never replay. Dispatched is not verified; confirm from a later observation.
- Freshness: the default observation age stands. A new timestamp for cached content does not establish freshness; re-observe.
- Focus: the attached window must hold the foreground or the act is refused. `switch` focuses, re-attaches, and re-observes; old tokens and coordinates die with the switch.
- Scope: the calling agent supplies task planning and interpretation. The tool owes faithful observations, well-defined actions, and honest errors — nothing more.

The old provider-adapter contract below is retained for history only; it describes the superseded proxy design (#2/#6) and is not implemented.

- Images: encode PNG/JPEG in memory only when requested by the model. Preserve raw frames internally. Record scaling/cropping and map model coordinates back to the original observation.
- Actions: validate structured JSON before execution; distinguish dispatched, failed, and verified outcomes. A provider without function calling can use a validated JSON response adapter.
- Freshness: the client's current 500 ms click age default is shorter than many vision-model turns. Obtain a fresh observation before execution and compare the relevant target/window state. On meaningful change, return updated evidence for a new decision. Merely raising the timeout or assigning the old target a new timestamp is insufficient.
- Focus: an occluded window can be captured while another app receives global input. Explicitly focus and re-observe before injecting input. Switching windows must invalidate old coordinates.
- Model independence: the model supplies decisions; the engine supplies desktop operations. Model-specific prompts and response parsing live in small provider adapters.
- Scope: recognition, task planning, and correct interpretation of arbitrary apps depend on model ability. The tool's responsibility is faithful observations, well-defined actions, and honest errors.

## Role of Football Manager

Football Manager exercises an interface with little useful accessibility data. Its Continue loop is one optional regression scenario, not the product's central architecture. A hard-coded Continue rule alone does not demonstrate model-driven computer use. General screenshot-based interaction should work before further game-specific optimization.

## Progress reporting rule

Every future handoff should state: product milestone advanced, user-visible capability added, evidence observed, remaining limitations, and the next milestone. Keep code status, test status, and product readiness separate. Prefer proving cross-application task traces under the direct-agent contract over further capture/OCR optimization until an actual trace identifies a bottleneck.
