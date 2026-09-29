# Desktop action expansion: typing, keys, scrolling, hover, drag, switching

Date: 30 September 2026. Target: the controlled native WinForms fixture, extended
with a second window. Covers issue #4. The specification is
[`../specs/desktop-action-expansion.md`](../specs/desktop-action-expansion.md).

All figures below are measurements from live runs on this machine, not estimates.
The verification suite is [`../tests/test_phase7_desktop_actions.py`](../tests/test_phase7_desktop_actions.py):
27 tests, all passing. The 40 pre-existing tests pass unchanged, which is the
regression evidence for the guard refactor described below.

## What changed

Desktop semantics are owned entirely by the Rust engine. The Python client
remains transport-only: each new action is a thin method that forwards arguments
and returns the engine's result. The client does not interpret, retry, or adjust
any action.

Click's inline safety checks were decomposed into individually reusable
functions in `engine/src/guard.rs`. Every action, including the pre-existing
click, now composes those same checks. There is one guard, not one per action.

Plain left-click behaviour is preserved exactly. `target_bbox_frame_px` remains
accepted alongside the new uniform `target` argument, and the guard runs before
the `dry_run` early return, as it did before the refactor.

## Actions and their verified effects

Every row was confirmed by reading the application's own reported state through
UI Automation, not by trusting the engine's return value.

| Action | Engine result | Independent fixture state |
| --- | --- | --- |
| `type_text` ASCII | `typed`, 16 chars, 32 events | Field read `Hello Desktop 42` |
| `type_text` off-layout | `typed`, 8 chars, 16 events | Field read `café 日本語` appended |
| `type_text` non-BMP | `typed`, 1 char, **2** code units, 4 events | Field read `😀` appended |
| `press_key` chord | `key_sent`, 6 events, 2 modifiers | Readout `Keys: Ctrl+Shift+S, Shift, Control` |
| `press_key` single | `key_sent` | Readout `Keys: End` |
| `scroll` vertical | `scrolled`, 6 events | `Scroll: v=6 h=0` |
| `scroll` horizontal right | `scrolled`, 5 events | `h=0` to `h=6` |
| `scroll` horizontal left | `scrolled` | `h=6` back to `h=3` |
| `scroll` untargeted | `scrolled`, 3 events, `hit_test_applied: false` | Pointer not moved |
| `hover` | `hovered`, 1 event | `Hover: entered`, then `Hover: left` on exit |
| `hover` timed | `hovered`, 15 events, `moves: 15` | Sustained hover observed |
| `drag` 24 steps | `dragged`, 27 events | `Drop: 214, 0 moves=23` |
| `click` right button | `clicked`, 3 events | `RightClick: 1` |
| `click` double | `clicked`, 5 events, `click_count: 2` | `DoubleClick: 1` |
| `focus_window` | `focused`, `verified: true` | Foreground read back independently |

The non-BMP row is the one worth reading twice. A single emoji character
requires a UTF-16 surrogate pair, so the engine reports `characters_sent: 1` and
`code_units_sent: 2`. The application received the correct character, verified
by reading the field back.

Horizontal scrolling is tested in both directions, not merely for "it moved",
because a single-direction test cannot distinguish a working implementation from
one with the sign inverted.

## Refusals, and proof that nothing was dispatched

Each refusal was checked twice: the error code was asserted, and the
application's state was read afterwards to confirm it had not changed.

| Refusal | Code | Proof of no dispatch |
| --- | --- | --- |
| Window moved after observing | `geometry_changed` | Field text unchanged |
| Window moved, chord | `geometry_changed` | Field text unchanged |
| Window moved, scroll | `geometry_changed` | Readout unchanged |
| Expired or mismatched observation | `invalid_request` | No action followed |
| Target outside the frame | `invalid_request` | No action followed |
| Drag endpoint outside the frame | `invalid_request` | No action followed |
| Target occluded, hover | `target_occluded` | Hover readout unchanged |
| Target occluded, drag | `target_occluded` | Drop readout unchanged |
| Unknown key in chord | `invalid_request` | No keys reached the field |
| Text over 4096 characters | `invalid_request` | Field text unchanged |
| Drag steps over 120 | `invalid_request` | No drag occurred |
| Focus refused | `focus_refused` | Foreground unchanged |

Bounds are enforced by the engine and reported back rather than silently applied.
A scroll of 100 notches returned `clamped: true` with `events_injected: 0`.

## Latency

Measured with [`../measure_latency.py`](../measure_latency.py); raw medians in
[`phase7-latency.json`](phase7-latency.json). Setup is performed outside the
timed region, so each figure is the action's own cost rather than the cost of the
observation a caller would have made anyway. These are measurements, not
thresholds: nothing in the suite fails on a latency number.

| Action | Median | Min | p95 |
| --- | --- | --- | --- |
| `hover` targeted | 0.65 ms | 0.61 | 0.79 |
| `scroll` targeted | 0.95 ms | 0.90 | 1.15 |
| `scroll` untargeted | 0.96 ms | 0.85 | 1.13 |
| `click` targeted | 1.18 ms | 1.08 | 13.35 |
| `press_key` | 1.34 ms | 1.03 | 1.53 |
| `type_text`, 5 characters | 2.38 ms | 1.90 | 2.87 |
| `drag`, 24 steps | 3.81 ms | 3.42 | 4.31 |
| `observe` | 2.88 ms | 2.39 | 3.34 |
| `focus_window` | 121.40 ms | 121.08 | 121.91 |

Two entries deserve comment rather than a reader having to guess.

`focus_window` is dominated by a deliberate 120 ms sleep that the engine takes
after `SetForegroundWindow` and before verifying, so the window manager has time
to settle. It is a fixed wait, not work proportional to the window.

`click` has a p95 of 13.35 ms against a 1.18 ms median. The tail is the engine's
own observation-age handling, not the click. A median that hides its own tail
would be worth less than the p95 here, so both are recorded.

## Known limits

`inject()` in `engine/src/input.rs` is the single place input reaches the
operating system. On a partial `SendInput` failure it releases whatever is still
held, so a stuck button or modifier does not silently remain down, and reports
how many events were actually injected. No action retries after a partial or
ambiguous dispatch.

This release path is reasoned about and implemented, but it is **not** exercised
by the test suite. Provoking a genuine partial `SendInput` failure requires
injecting against a desktop that is tearing down or otherwise refusing input,
which cannot be done reliably without destabilising the session the suite itself
runs in. The suite verifies that all events were accepted; it does not verify
the cleanup path. This is stated as a limit rather than presented as tested.

`focus_window` verified its refusal by comparing `GetForegroundWindow` against
the requested window. It does not test every internal Windows eligibility rule,
only that the outcome was verified and honestly reported.

DPI: Python on this machine is system-DPI-aware through a manifest, so both
`SetProcessDpiAwarenessContext` and `SetThreadDpiAwarenessContext` fail silently.
The suite therefore measures the coordinate scale it actually gets, by comparing
`GetWindowRect` against the engine's per-monitor-aware bounds, and converts
explicitly. It reports the measured scale at startup; expect roughly 0.6662 at
150% scaling. Attempting to force awareness would be misleading rather than
protective.

Wheel routing: `WM_MOUSEWHEEL` is delivered to the focused control, not to the
window under the cursor, so the fixture observes wheel input through a low-level
mouse hook and posts the work to the form. This is fixture-side only.

## Reproducing

From `windows-computer-use/`:

```powershell
Get-Process fixture_app -EA SilentlyContinue | ForEach-Object { $_.Kill() }
dotnet build tests/fixture_app/fixture_app.csproj
python -m pytest tests/ --timeout=300 -q
```

The full suite is 67 tests. It needs a real interactive desktop; there is no
headless path, and the results above were taken on an interactive session at
1680x1120, 144 DPI.

Timing note for anyone re-running: set `PYTHONIOENCODING=utf-8`. The console
defaults to cp1252, and the suite prints non-ASCII fixture text, so its own
progress output can raise `UnicodeEncodeError` and look like a test failure.
