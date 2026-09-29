## Problem Statement

The Windows desktop engine can capture a window and execute guarded actions, but a vision model has not yet completed an observe–decide–act–verify cycle through it. A user cannot give a vision-capable model a desktop task and see that model choose and verify an action. The existing Football Manager Continue rules demonstrate application-specific recognition, not general model-controlled computer use. The user's goal is a provider-independent desktop tool for native Windows applications, custom-rendered apps, and cross-application tasks.

## Solution

Add one model-facing session around the existing Python client and Rust engine. In the first slice, a configured vision-capable model sees a screenshot of one attached controlled desktop window, chooses one click based on the image, and inspects a new screenshot to report success, failure, or uncertainty. The host sends image content and a small structured action contract to the model over the configured local HTTP model proxy. The same session object owns the window identity, observation metadata, action budget, and result history. A generic model adapter boundary keeps provider-specific request and response formats outside the desktop engine.

The first live demonstration uses a controlled native desktop fixture with an unknown-to-the-model button location and a visible state change. OCR profiles and hard-coded coordinates are absent from the decision path. Later work can add keyboard, scrolling, desktop overview, app switching, multiple models, and MCP exposure without changing the meaning of an observation or action.

## User Stories

1. As a user, I want to describe a desktop task in ordinary language, so that I do not have to supply pixel coordinates.
2. As a user, I want the tool to attach to a specific visible window, so that I know which application the model is controlling.
3. As a user, I want the model to see the actual window image, so that it can decide based on visible state.
4. As a user, I want the model to see the screenshot dimensions, so that its chosen target maps to the right pixels.
5. As a user, I want a one-action limit by default, so that the first experiment cannot run away.
6. As a user, I want a dry-run mode, so that I can inspect the model's proposed action without input being sent.
7. As a user, I want the tool to reject a target after the window moves or changes, so that an old coordinate is not clicked.
8. As a user, I want the tool to report when focus changed, so that input does not reach the wrong app.
9. As a user, I want an uncertain model response to stop the interaction, so that guessing does not become an action.
10. As a user, I want to stop the session at any time, so that I retain control of my desktop.
11. As a user, I want to see the image after a click, so that I can judge the actual result.
12. As a user, I want the model to report whether the requested change happened, so that a dispatched click is not mistaken for completed work.
13. As a user, I want a clear error when the desktop is locked or capture fails, so that I can resolve the environment issue.
14. As a user, I want screenshots and model prompts kept out of routine logs, so that incidental desktop content is not persisted.
15. As an implementing agent, I want a single end-to-end test through the existing client, so that the visible behavior is exercised without duplicating the engine.
16. As an implementing agent, I want machine-readable model responses, so that invalid actions can be rejected before reaching Windows input.
17. As an implementing agent, I want the observation ID carried through a model turn, so that execution is tied to the image shown to the model.
18. As an implementing agent, I want explicit image scaling metadata, so that model coordinates can be converted to capture coordinates.
19. As an implementing agent, I want model, network, and engine deadlines, so that a stalled call ends with a reason.
20. As an implementing agent, I want the provider request and response parsing isolated, so that another vision model can be added without altering capture or input.
21. As a future user, I want the same tool contract to extend to keyboard and multiple windows, so that this first slice remains a useful foundation.
22. As a future user, I want model-driven interaction with native and custom-rendered applications, so that browser-only automation is not the limit.

## Implementation Decisions

- The highest test seam is the existing Python client into the persistent Rust engine. A controlled desktop fixture is the live target. Tests should exercise the full model-to-client-to-engine-to-desktop path at this seam.
- The Rust engine remains responsible for capture, window identity, observation IDs, geometry and foreground checks, and guarded input. The Python layer owns image encoding, model HTTP calls, response validation, session limits, and orchestration.
- Use the user-configured local OpenAI-compatible HTTP proxy for the first model adapter. Send HTTP directly from code. Configure the model alias, endpoint, and authorization through environment or local configuration; never commit credentials. Check that the selected model actually accepts image input before the live demonstration. Model choice must remain replaceable.
- Encode the captured image in memory as a supported image MIME type. Send screenshot dimensions and the image to the model. If the submitted image is resized or cropped, record the exact scale and offset and invert them before input. Never assume displayed-image pixels equal physical desktop pixels.
- The model response has exactly one of three intents: propose one click target, report task complete, or report cannot decide. A click target is a bounded rectangle in the image coordinates shown to the model. Parse a strict JSON response; reject malformed JSON, unknown intents, out-of-bounds coordinates, multiple actions, and unsupported actions.
- The initial task is one click followed by verification. The model must select the button from the screenshot. Do not use OCR results, fixture text selectors, or preselected coordinates to choose the action. UI Automation can provide an independent verification oracle in the fixture test, but it cannot feed the model's choice.
- Associate the proposed click with the observation shown to the model. Before execution, request a fresh observation and compare window identity, bounds, foreground, and target region with the earlier observation. If meaningful target-region change or uncertainty is detected, return the new image for another model decision within the action budget. Do not renew the age of the old coordinate or silently enlarge the existing 500 ms age limit. Pass a current observation ID to the guarded click API.
- For the first controlled fixture, target-region comparison may be conservative and reject animation or ambiguous changes. Record this as a limit of the first slice; semantic re-grounding for arbitrary animated apps is later work.
- The tool reports distinct outcomes for model decision, input dispatch, and verified result. Dispatch success is not task success. After action, send a new screenshot to the model and require a structured success, failure, or uncertain verification response. Keep the fixture's visible counter or state as an independent observable for the end-to-end test.
- Use a bounded model request timeout, engine request timeout, wall-clock session timeout, and action budget. Default to one action and offer dry-run. A timeout after a possibly completed action must stop rather than replay input.
- Retain small metadata logs: timestamps, model alias, target window identity, observation IDs, proposed rectangle, validation outcome, engine result, and model verification result. Do not save raw screenshots, full model prompts, or desktop text by default. Diagnostic artifacts require an explicit option.
- The model adapter and action schema are host-agnostic. MCP or another external tool host can wrap this session later. The first implementation is a local command or library call, not a published service.

## Testing Decisions

- Prefer a live end-to-end test through the Python client and Rust engine against the existing native fixture. It must prove a visible counter or state changes exactly once after the model chose a click from an image and that the model correctly verifies the change.
- Use a second end-to-end scenario that moves, resizes, or obscures the target between model observation and action. The tool must refuse the old click and return fresh evidence or a clear stop result. No input may reach a different window.
- Test observable error behavior for malformed model output, wrong coordinates, model refusal, model timeout, capture failure, locked desktop, and ambiguous post-action result. Mock only the external model response where needed to force rare branches; the main success path uses a real vision model and live desktop.
- Reuse the repository's existing protocol, capture, guarded-click, and fixture tests as prior art. Add tests at the session boundary rather than mirroring internal image conversion or model parser functions.
- Record the exact model alias, version if reported, prompt, fixture state, and observed outcome for the real demonstration. Report model and engine latency separately. Tests should not assert fixed performance thresholds for network/model calls.

## Out of Scope

- More than one executed action in a session, except a bounded retry when a pre-action recheck rejects a stale target.
- Typing, key chords, scrolling, dragging, application switching, desktop overview, or unrestricted multi-window navigation.
- A universal UI tree reconstructed from pixels.
- A Football Manager-specific Continue rule or OCR profile in the model decision path.
- A second provider adapter, MCP server, installer, background Windows service, or unattended desktop operation.
- Any claim that every vision model or every Windows application will work without provider and application-specific limitations.

## Further Notes

The repository's product roadmap names this model-facing interaction as the next milestone. The guarded click already accepts an observation ID and rectangle and defaults to a 500 ms observation age. A remote vision-model turn can exceed that age. Fresh observation and target comparison are central requirements, not an optional performance improvement.

The user's end goal is provider-independent computer use across the Windows desktop and native applications. This issue deliberately proves the smallest general image-to-action loop. Subsequent work should fill out desktop actions and multi-window observation without requiring application-specific OCR profiles.
