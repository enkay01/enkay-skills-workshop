---
name: windows-computer-use
description: Direct Windows desktop automation via Cua Driver — observe windows and accessibility trees, click buttons, type text, and verify postconditions without stealing focus or moving the user's cursor. Use when an agent needs to control Windows applications, inspect UI state, or verify on-screen results on Windows.
---

# Windows computer use

Control native Windows applications and desktop state through Cua Driver. The agent plans and selects targets; Cua Driver executes actions via UI Automation (UIA) and native events in the background, returning structured accessibility trees, coordinates, and deterministic verification results.

## Foundation & Fork Reference

This skill builds on the open-source Cua Driver foundation, maintained in our fork:
- **Fork repository**: `https://github.com/enkay01/cua` (forked from `https://github.com/trycua/cua`)
- **Pinned release**: `cua-driver-rs-v0.32.0` (Cua Driver 0.32.0 / `cua-core` 0.3.2 / `cua-sdk` 0.2.0)
- **Architecture**: This repository maintains agent skills and tests; the desktop automation engine and SDK code live in the fork.

### MIT-Licensed Tier Boundary

We build strictly on the **MIT-licensed tier** of Cua:
- **Cua Driver** (`libs/cua-driver`): Native Rust automation engine, CLI (`cua-driver.exe`), and stdio MCP server — licensed under MIT.
- **Cua SDK & Core** (`libs/cua`, `libs/python/core`, `libs/python/agent`): Core libraries and agent interfaces — licensed under MIT.
- **Excluded tiers**: We do not vendor, link, or depend on:
  - `cua-som` (AGPL-3.0)
  - `cua-perception` (proprietary extension)
  - Cua Spaces components (`cua-spacesd`, Spaces apps, Keyvault, teleport, streaming) (FSL-1.1-MIT)

Upstream CI enforces that MIT components never depend on FSL or AGPL code, ensuring clean redistribution without copyleft or commercial field-of-use restrictions.

### Upstream Sync Routine & Patch Policy

To prevent fork drift while keeping contributions upstreamable:
1. **Patch hygiene**: Carry any custom SDK or driver adjustments as small, reviewable commits in the fork formatted to upstream coding standards (`cargo fmt`, `ruff`, `mypy`). Upstream acceptance is the criteria for any patch.
2. **Rebase cadence**: Sync the fork against upstream `trycua/cua:main` upon each stable upstream release tag (`cua-driver-rs-v*`).
   ```bash
   git remote add upstream https://github.com/trycua/cua.git
   git fetch upstream --tags
   git checkout main
   git merge upstream/main --ff-only
   ```
3. **Verification gate**: Before bumping pinned versions in this skill, execute the upstream test suite (`cargo test` under `libs/cua-driver/rust`) and the local Calculator smoke check (`tests/test_calculator_smoke.py`).

---

## 1. First-Run Windows Setup Path

A fresh Windows machine reaches a working desktop automation session in one pass:

### Step 1: Install Cua Driver

Run the official PowerShell installer from an interactive terminal (PowerShell 5.1 or 7+):

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://cua.ai/install.ps1 | iex"
```

*Alternative direct installer from the repository:*
```powershell
irm https://raw.githubusercontent.com/trycua/cua/main/libs/cua-driver/scripts/install.ps1 | iex
```

### Step 2: Verify Installation & Diagnostics

Confirm the binary is on PATH and runtime prerequisites (UIA, interactive desktop) are met:

```powershell
cua-driver --version
cua-driver doctor
```

`cua-driver doctor` checks the binary location, UI Automation COM instantiation (`CoCreateInstance(CUIAutomation)`), display scaling, and desktop session reachability.

### Step 3: Start the Daemon

Cua Driver operates as a local background daemon communicating over a named pipe (`\\.\pipe\cua-driver`):

```powershell
# Option A: Register automatic startup at Windows user logon (recommended)
cua-driver autostart enable
cua-driver autostart kick

# Option B: Run the daemon interactively in a background terminal
cua-driver serve
```

Verify daemon readiness:
```powershell
cua-driver status
```

### Step 4: Connect Your Agent

Agents interact with Cua Driver via stdio MCP or the CLI tool dispatcher.

**Stdio MCP configuration** (for Claude Code, Codex, Antigravity, OpenClaw):
Add to your agent's MCP configuration (`mcp_servers` / `mcpServers`):
```json
{
  "cua-driver": {
    "command": "cua-driver",
    "args": ["mcp"]
  }
}
```

**Direct CLI execution**:
Single tools can be called through the running daemon via `cua-driver call`:
```powershell
cua-driver call <tool_name> '<json_arguments>'
```
*(Tip: On PowerShell 5.1, pipe JSON to avoid quote stripping: `'{"app_name":"Calculator"}' | cua-driver call launch_app`)*

---

## 2. The Observe → Act → Verify Loop

Every desktop interaction must follow this structured cycle:

```text
1. Discover & Target -> list_windows / launch_app
2. Observe State     -> get_window_state (structured UIA tree + element tokens)
3. Act               -> click / type_text / press_key / hotkey (background delivery)
4. Verify Outcome    -> verify_state (predicates) or fresh get_window_state
```

### Phase 1: Discover & Target

Find or launch the target application window:

```bash
# List all running windows with bounds, pid, and HWND
cua-driver call list_windows '{}'

# Or launch an application without bringing it to foreground (SW_SHOWNOACTIVATE)
cua-driver call launch_app '{"name": "Calculator"}'
```

Returns window metadata: `pid`, `window_id` (HWND), title, and bounds.

### Phase 2: Observe State

Capture the window's UI Automation tree:

```bash
cua-driver call get_window_state '{"pid": 9524, "window_id": 10617982}'
```

Returns:
- `elements`: Structured list of UI elements with `element_token` (e.g. `"s00000001:42"`), `label`, `role`, and `frame`.
- `snapshot_id`: Token scope identifier.
- Optional window screenshot if requested.

*For full-screen / desktop operations where window UIA is unavailable:*
```bash
cua-driver call get_desktop_state '{}'
```

### Phase 3: Act

Dispatch input using the fresh `element_token` returned by `get_window_state`:

```bash
# Click a button using its element token (default background delivery)
cua-driver call click '{"pid": 9524, "window_id": 10617982, "element_token": "s00000001:49"}'

# Or click window-local coordinates (x, y)
cua-driver call click '{"pid": 9524, "window_id": 10617982, "x": 150, "y": 200}'

# Type text into the target window
cua-driver call type_text '{"pid": 9524, "text": "42"}'

# Send key combination
cua-driver call hotkey '{"pid": 9524, "keys": ["ctrl", "a"]}'
```

#### Background Delivery Contract (`delivery_mode`)

By default, every input action uses `delivery_mode: "background"`:
- **No focus stealing**: The user's active window remains frontmost and keeps keyboard focus.
- **No window flashing or restacking**: Uses UIA Invoke or background message routing.
- **No cursor warping**: The agent operates through an independent virtual cursor overlay.

### Phase 4: Verify Outcome

Confirm the requested postcondition deterministically:

```bash
cua-driver call verify_state '{"pid": 9524, "window_id": 10617982, "expect": [{"element": {"selector": {"label_contains": "42"}}}]}'
```

`verify_state` evaluates up to 8 logical AND predicates and returns `"status": "satisfied"` once the expected UI state stabilizes across consecutive samples.

---

## 3. Operating Guidance & Judgment

Preserved from hard-won field experience:

### When to Screenshot versus Trust Results
- **Trust structured state**: When an application provides a valid UIA accessibility tree, rely on `get_window_state` elements and `verify_state` predicates. Structured predicates are faster, deterministic, and immune to display resolution variances.
- **When to capture screenshots**: Take screenshots only when:
  1. Operating custom canvas, WebGL, or non-native UI with an empty or sparse accessibility tree.
  2. The user specifically requests visual artifact inspection.
  3. `verify_state` predicates report `"unknown"` due to ambiguous element labeling.
- **Do not take redundant screenshots**: An action already confirmed via `verify_state` requires no additional visual check.

### Retry & Escalation Budget
- **Transient staleness**: If an element token is invalidated by a layout update or background animation, fetch a fresh `get_window_state` snapshot and retry the action. Allow **up to 3 retries**.
- **Delivery escalation**: Always attempt `delivery_mode: "background"` first. Never pass `"foreground"` speculatively.
- Escalate to `delivery_mode: "foreground"` **only** when the driver returns a structured `background_unavailable` error. The driver will briefly activate the target, dispatch the event, and restore the previous foreground window.

### Staying Oriented in Long Tasks
- **Track target handles**: Anchor all tool calls with explicit `pid` and `window_id`.
- **Invalidated tokens**: Every new `get_window_state` call invalidates prior element tokens for that window. Never cache tokens across turns.
- **Stop on proof**: Conclude execution as soon as the observed evidence fulfills the user's acceptance criteria. Never execute speculative follow-up actions.

---

## 4. Recovery Paths

When desktop state diverges from expectations or driver errors occur:

| Symptom / Error | Root Cause | Recovery Procedure |
| :--- | :--- | :--- |
| `background_unavailable` | Target surface (Chromium content, GTK, custom canvas) drops background events or lacks UIA Invoke. | Retry that specific action with `"delivery_mode": "foreground"`. |
| `stale_snapshot` / invalid token | The window DOM or layout updated after the snapshot was taken. | Call `get_window_state` to receive a fresh snapshot ID and new element tokens; re-dispatch with the new token. |
| `pid has no on-screen window` | The target window was closed, minimized to system tray, or moved off-screen. | Call `list_windows` to inspect current top-level windows. If closed, re-launch via `launch_app`. |
| Daemon unreachable / pipe error | The `cua-driver` daemon process stopped or crashed. | Run `cua-driver status`. If stopped, execute `cua-driver autostart kick` or restart `cua-driver serve`. Verify with `cua-driver doctor`. |
| Desktop state disagrees with model | A popup dialog appeared, a loading screen intervened, or an unexpected page opened. | **Fail loudly instead of clicking blindly.** Pause, call `get_window_state` to read the actual UI hierarchy, identify any blocking dialogs, and adjust the plan. |

---

## 5. Safety Boundaries

To keep autonomous agents predictable and protect the host environment:

1. **Non-interfering background operation**: Do not call `bring_to_front` or `scope: "desktop"` unless specifically instructed or when background delivery has returned `background_unavailable`.
2. **Process termination restrictions**: Standard permission mode strictly forbids terminating arbitrary host processes. `kill_app` will refuse calls against processes not spawned by the Cua runtime.
3. **System & security dialogs**: Never click through OS UAC prompts, security warnings, or credential dialogs automatically. Escalate to the human user.
4. **Destructive actions**: Destructive filesystem operations (bulk deletions, formatting) or persistent system modifications must be confirmed before dispatch.
5. **No secret leaking**: Window titles and UI element values are observed for execution; never log private data, tokens, or credentials into repository files or commits.

---

## 6. Exact Command & Tool Surface Reference

Examples match the exact tool surface of Cua Driver 0.32.0.

### Tool: `launch_app`
```json
{
  "name": "Calculator",
  "start_minimized": false
}
```

### Tool: `list_windows`
```json
{}
```

### Tool: `get_window_state`
```json
{
  "pid": 9524,
  "window_id": 10617982
}
```

### Tool: `click`
```json
{
  "pid": 9524,
  "window_id": 10617982,
  "element_token": "s00000001:49",
  "delivery_mode": "background",
  "count": 1
}
```

### Tool: `type_text`
```json
{
  "pid": 9524,
  "text": "Hello World",
  "delivery_mode": "background"
}
```

### Tool: `verify_state`
```json
{
  "pid": 9524,
  "window_id": 10617982,
  "expect": [
    {
      "element": {
        "selector": {
          "label_contains": "42"
        }
      }
    }
  ],
  "stable_samples": 2,
  "timeout_ms": 5000
}
```
