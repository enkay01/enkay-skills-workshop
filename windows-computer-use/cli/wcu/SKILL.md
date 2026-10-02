---
name: windows-computer-use
description: Direct Windows desktop automation for agent hosts — observe the screen, click buttons, type text, press chords, scroll, and switch windows through a persistent `wcu` CLI backed by a guarded input engine. Use when an agent needs to control the Windows desktop, complete a GUI task, fill a form, click through a dialog, or verify an on-screen outcome, without a model endpoint or proxy.
---

# Windows computer use

Use the computer directly. One persistent session, many commands. The agent
owns planning and target selection; the CLI returns real screenshots and
honest action outcomes. No model call, no proxy, no second agent.

## Prerequisites

- Windows 10/11, interactive (unlocked) desktop session.
- Engine built: `cargo build --release` in `windows-computer-use/engine`.
- CLI installed: `pip install -e windows-computer-use/cli`.

Check once with `wcu session start` (reports capabilities) and `wcu capabilities`.

## The loop

```
wcu session start                      # once per desktop
wcu windows [--filter TEXT]            # find the target window
wcu switch <hwnd>                      # focus + attach + fresh screenshot
wcu act click --observation-id N --point X,Y
wcu observe                            # evidence after the act
```

Repeat observe → act until the requested outcome is established. Each `act`
revalidates its proposal against current evidence and either dispatches or
returns a `refused` result with a fresh screenshot for reconsideration.

## Commands

Run `wcu help` for the full list and `wcu <command> --help` for syntax. The
stable set:

- **Session**: `session start|status|stop`, `cancel`
- **Discover**: `windows`, `monitors`, `capabilities`
- **Target**: `attach <hwnd>`, `focus <hwnd>`, `switch <hwnd>`
- **Observe**: `observe` (PNG screenshot + frame metadata), `inspect`
- **Act**: `act click|type|press|scroll|hover|drag|focus|invoke|set-value`
- **Control**: `open <program>`, `history`

## Coordinates

Observation metadata includes a `geometry` block (`dpi`, `scale_factor`, `physical_bounds`,
`logical_bounds`, `image_dimensions`). Targets in `wcu act` (`click`, `hover`, `drag`, `scroll`)
and grounding commands (`ocr --region`, `find --region`) support `--coord-space`:
- `physical` (default): Exact raw canvas pixels matching observation image dimensions.
- `logical`: DIP / logical points scaled by `scale_factor = dpi / 96.0` (e.g. 150% scaling).
- `normalized`: Normalized 0.0..1.0 unit fractions or 0..1000 grid integers (e.g. `--point 0.5 0.5` or `--point 500 500`).
The result envelope includes `resolved_physical_point` reporting the exact physical injection pixels.

## Acting

- A clear screenshot is enough to choose a coordinate target. Act.
- Both pointer and keyboard actions default to 30,000 ms (`--max-age-ms 30000`).
- Keyboard actions without `--observation-id` validate window identity, PID, and foreground state without enforcing timestamp staleness.
- Add `--auto-refresh` (or `--retry-if-stale`) to `act` commands to automatically revalidate and re-dispatch on transient staleness (up to 3 retries) without returning a refusal error, provided the window has not moved, resized, or lost foreground.
- `act` revalidates freshness, window identity, geometry, foreground, and
  hit-test before dispatching. A changed target returns `refused` with new
  evidence — reconsider, never replay the old target.
- Add `--dry-run` to validate a proposal without dispatching input.
- Use `act type --method commit --text "..."` for text editors, including
  modern Notepad. Commit sends one undoable edit message to the focused
  editor and confirms by reading the document back
  (`verification: "matched"`). It touches neither the clipboard nor the
  input stream. It needs a focused editor with one selection or caret and
  documents up to 65536 UTF-16 units; editors without TextPattern can only
  verify insertion into an empty document. See the CLI README's
  "Known limitations" for the exact preconditions.
- `act type --method paste --text "..."` is the fallback when the editor
  ignores edit messages. Paste reads the focused editor's full text and
  selection and confirms the expected result. It preserves a plain-text
  clipboard; other clipboard formats or unreadable editor selections are
  refused before dispatch. Check the returned `clipboard` field.
- The default Unicode method reports `verification: "unavailable"`. Read text
  back with `inspect` or `observe` before reporting success. A `typed` status
  alone proves dispatch. Repeating corrupted input can duplicate content.
- `inspect` answers a named-control or region question when the screenshot is
  ambiguous. It is not a routine confidence check after visual identification.

## Stopping

Stop when observed evidence establishes the user's requested outcome. Another
screenshot or inspection needs a specific unresolved question that could
change that conclusion. A dispatched action is not the same as a completed
task — verify from later evidence.

## Safety

- One session per desktop; a second `session start` is refused.
- The engine refuses stale, moved, backgrounded, or occluded targets before
  any input is dispatched.
- `wcu cancel` stops a pending operation. Already-dispatched input is never
  replayed.
- Screenshots live under the session directory and are deleted on
  `session stop`. History records operations and outcomes, never typed text.

## Troubleshooting

For a concrete failure, run the command with `--help` and check the error code
in the result envelope. Common codes: `no_session` (run `session start`),
`engine_not_found` (build the engine), `foreground_changed` (the target is not
in front — `switch` to it), `stale_observation` (re-observe and act promptly).

For corrupted Unicode typing, inspect the editor before making another edit.
Use explicit commit mode for subsequent text entry. A commit `text_mismatch`
means the expected document was not confirmed; check the editor before acting
again. A paste `text_mismatch` means the same; additionally check the clipboard
outcome. An unsupported verification or clipboard error before
dispatch leaves editor content unchanged.
