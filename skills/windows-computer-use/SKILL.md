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

Targets are expressed in the pixels of the observation you saw — a point
`X,Y` or a box `X,Y,W,H` — never bare screen coordinates. `wcu observe`
returns the frame dimensions and the window's physical bounds; `wcu inspect`
returns element bounds in screen pixels. Map between them with the capture
bounds from the observation.

## Acting

- A clear screenshot is enough to choose a coordinate target. Act.
- `act` revalidates freshness, window identity, geometry, foreground, and
  hit-test before dispatching. A changed target returns `refused` with new
  evidence — reconsider, never replay the old target.
- Add `--dry-run` to validate a proposal without dispatching input.
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
