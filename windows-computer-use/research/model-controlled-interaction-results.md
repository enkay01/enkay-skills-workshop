# First model-controlled desktop interaction

Date: 29 September 2026. Target: the controlled native WinForms fixture. Vision model aliases: `deepseek-v4-flash-vision-exp` and `gpt-6-luna` through the local EasyCLIProxyAPI endpoint. The models received in-memory PNG screenshots and selected targets from the image. The decision path used no OCR profile, UI Automation selector, or hard-coded button coordinates.

## Observed results

| Run | Model/session output | Independent fixture result |
| --- | --- | --- |
| Dry run | Proposed click rectangle `[53, 88, 140, 43]`; no input sent | Fixture closed without an action |
| Single live action | Proposed `[54, 90, 136, 44]`; guarded engine returned `clicked`; post-action model returned `success` | UI Automation read `Counter: 1` |
| Window moved after model decision | Session returned `stale_target`; no click dispatched | UI Automation read `Counter: 0` |

The initial dry run, live click, and moved-window entries were recorded with `deepseek-v4-flash-vision-exp`. The same success and moved-window cases later passed with `gpt-6-luna`, which is now the demo default. The latter selected `[53, 86, 138, 43]` in its successful live run, reported success from labeled before/after images, and the independent counter read 1.

Commands used from the repository root, with `WCU_PROXY_TOKEN` set in the process environment:

```powershell
dotnet build windows-computer-use/tests/fixture_app/fixture_app.csproj --no-restore
python windows-computer-use/tests/live_model_demo.py
python windows-computer-use/tests/live_model_demo.py --execute
python windows-computer-use/tests/live_model_demo.py --execute --move-window-before-action
python -m unittest discover -s windows-computer-use/tests -p test_model_control.py -v
```

The focused session tests passed five cases: a single verified click, a changed target requiring a new decision, a missing newer frame stopping input, an out-of-bounds model target stopping input, and a verification failure that does not replay an executed click.

The first live attempt dispatched an action but received empty verification content because the model exhausted a 300-token completion budget on internal reasoning. The adapter now budgets 1,200 tokens. A later attempt clicked and changed the fixture counter, while the model marked the result uncertain after seeing only a post-action image. Verification now compares labeled before and after images after a brief redraw wait; the final live run returned success and the independent counter read 1. If verification fails after dispatch, the session reports `verification_unavailable` and does not replay the click.

`deepseek-v4-flash-vision-exp` also returned empty decision content in a later trial despite the larger budget. This is a model/provider reliability limit. The session rejected it without clicking. The alternative `gpt-6-luna` completed the dry run, live click, and stale-window trial.

## Scope and remaining limits

This demonstrates one model-chosen click in one attached native window and a model judgment from a later screenshot. The fixture counter independently corroborates the successful action. The window-move run shows a changed observation stops stale input.

The session currently requires a newer WGC frame before executing the model's proposed click. A completely static window may yield `no_fresh_frame`; the fixture includes a visual tick outside the target to exercise the first loop. The engine's `observe` call can otherwise issue a new observation ID for cached content, so the session explicitly checks that `frame_id` advanced. General static-window revalidation needs a separate capture/session design.

This is not yet a general desktop agent. It supports one click, one attached window, and one OpenAI-compatible vision adapter. Typing, scrolling, dragging, switching applications, desktop overview capture, multiple-action tasks, and an external tool protocol remain in the product roadmap. The model's reported verification is fallible; task-specific or independent observations should be used when stronger success evidence is needed.
