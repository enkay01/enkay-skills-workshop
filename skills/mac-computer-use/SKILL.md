---
name: mac-computer-use
description: Direct OS desktop automation, mouse control, keyboard input, and screenshot capture on macOS using PyAutoGUI. Use when an agent needs to control the computer, click buttons, drag items, type text, send key combos, inspect UI elements with vision, or run desktop workflows without external services.
---

# Mac computer use skill

This skill enables any vision-enabled AI agent to control a macOS laptop or desktop directly through native mouse movements, clicks, keyboard input, screen capture, and visual template matching.

This implementation executes directly via PyAutoGUI and macOS Quartz event taps. It is completely separate from Codex computer-use and requires no external paid proxy or third-party cloud service.

---

## 1. Prerequisites and permissions

The host process running the agent (for example Antigravity, Terminal, or VS Code) requires two macOS permissions:
1. Accessibility: `System Settings > Privacy & Security > Accessibility`. Must be enabled for the application running the commands.
2. Screen Recording: `System Settings > Privacy & Security > Screen Recording`. Required for capturing screenshots of other applications.

---

## 2. Binary location

The skill bundles a self-contained runner script and a Python automation engine configured with PEP 723 dependency definitions:

```bash
# Executable wrapper:
~/.agents/skills/mac-computer-use/scripts/mac-control <command> [args]

# Direct invocation via uv:
uv run ~/.agents/skills/mac-computer-use/scripts/mac_control.py <command> [args]
```

To verify the installation and display metrics, run:

```bash
~/.agents/skills/mac-computer-use/scripts/mac-control size
```

---

## 3. Screen geometry and coordinate scaling

Retina displays maintain two coordinate systems:
- Physical pixels: The raw image raster returned by screenshots (for example, 2880 x 1800).
- Logical points: The coordinate system expected by macOS WindowServer and PyAutoGUI (for example, 1440 x 900).

On standard MacBook Retina displays, the scale factor is 2.0x:

```text
Logical X = Physical X / 2.0
Logical Y = Physical Y / 2.0
```

The CLI tool accepts logical points by default. To pass raw coordinates measured directly from a screenshot image, append the `--physical` flag to any movement or click command. The tool will divide by the detected scale factor automatically.

---

## 4. Vision-guided agent workflow

Any vision-enabled agent should follow this standard five-stage execution loop:

```text
1. Capture state     -> Take a screenshot of the active workspace.
2. Inspect visually  -> Load the screenshot and identify the target control.
3. Resolve target    -> Read physical pixel coordinates or match an image template.
4. Execute action    -> Dispatch click, type, hotkey, or batch sequence.
5. Verify outcome    -> Capture a follow-up screenshot to confirm UI change.
```

### Example workflow commands

Capture current screen:
```bash
~/.agents/skills/mac-computer-use/scripts/mac-control screenshot --output /tmp/current_screen.png
```

Read `/tmp/current_screen.png` using file viewing tools to locate the target button.

If a button center is at pixel coordinates (932, 934) on the image:
```bash
# Option A: Use the --physical flag to auto-convert
~/.agents/skills/mac-computer-use/scripts/mac-control click 932 934 --physical

# Option B: Pass computed logical points directly (932 / 2 = 466, 934 / 2 = 467)
~/.agents/skills/mac-computer-use/scripts/mac-control click 466 467
```

Confirm the result with a verification snapshot:
```bash
~/.agents/skills/mac-computer-use/scripts/mac-control screenshot --output /tmp/verify.png
```

---

## 5. Command reference

All commands accept `--json` for machine-readable output.

### Display and cursor status

- `size`: Prints screen width, height, and scale factor.
  ```bash
  mac-control size [--json]
  ```

- `position`: Prints current cursor location.
  ```bash
  mac-control position [--json]
  ```

### Mouse control

- `move <x> <y>`: Moves cursor to target coordinates.
  ```bash
  mac-control move 400 300 [--duration 0.2] [--physical]
  ```

- `click <x> <y>`: Clicks at coordinates.
  ```bash
  mac-control click 466 467 [--button left|right|middle] [--clicks 1] [--interval 0.1] [--physical]
  ```

- `double-click <x> <y>`: Double-clicks at coordinates.
  ```bash
  mac-control double-click 250 180 [--physical]
  ```

- `drag <x1> <y1> <x2> <y2>`: Drags cursor between coordinates.
  ```bash
  mac-control drag 100 200 400 200 [--duration 0.5] [--physical]
  ```

- `scroll <clicks>`: Scrolls vertically (positive for up, negative for down).
  ```bash
  mac-control scroll -5 [--x 300 --y 400]
  ```

### Keyboard control

- `type <text>`: Types string text keystroke by keystroke or pastes from clipboard.
  ```bash
  mac-control type "hello world" [--interval 0.02] [--paste]
  ```

- `press <key>`: Presses a single key. Supports key normalization (`cmd`, `esc`, `enter`, `tab`, `backspace`, `space`, `up`, `down`).
  ```bash
  mac-control press enter
  mac-control press escape
  ```

- `hotkey <keys...>`: Sends a key combination.
  ```bash
  mac-control hotkey command a
  mac-control hotkey command shift 4
  mac-control hotkey command space
  ```

### Application management

- `open-app <app_name>`: Launches or brings application to the foreground using macOS `open -a` and AppleScript.
  ```bash
  mac-control open-app "Slack" [--wait 1.0]
  mac-control open-app "Google Chrome"
  ```

### Visual template matching

- `locate <template_path>`: Searches for a small template image on the current screen across multiple scale levels using OpenCV normalized cross-correlation.
  ```bash
  mac-control locate /tmp/search_icon.png [--confidence 0.8] [--click]
  ```

### Batch execution

- `batch <steps_file.json>`: Executes a list of action steps sequentially in a single process without subshell restart overhead.
  ```bash
  mac-control batch /tmp/plan.json [--delay 0.3] [--stop-on-error] [--dry-run]
  ```

---

## 6. Batch plan schema

Batch plans use a flat JSON array format.

```json
[
  {
    "action": "open-app",
    "app": "System Settings",
    "wait": 1.0,
    "pause": 0.5
  },
  {
    "action": "click",
    "x": 150,
    "y": 200,
    "pause": 0.3
  },
  {
    "action": "hotkey",
    "keys": ["command", "a"],
    "pause": 0.1
  },
  {
    "action": "type",
    "text": "Accessibility",
    "interval": 0.03,
    "pause": 0.5
  },
  {
    "action": "press",
    "key": "enter",
    "pause": 0.5
  },
  {
    "action": "screenshot",
    "output": "/tmp/search_result.png"
  }
]
```

Supported actions in batch mode: `move`, `click`, `double-click`, `drag`, `scroll`, `type`, `press`, `hotkey`, `screenshot`, `locate`, `open-app`, `pause`.

---

## 7. Safety, fail-safe, and error recovery

- Emergency fail-safe: PyAutoGUI fail-safe is enabled. Moving the mouse cursor into any corner of the display (for example, (0, 0)) immediately raises `pyautogui.FailSafeException` and aborts execution.
- Window focus: Always invoke `open-app <app_name>` before dispatching clicks to ensure target windows are focused and frontmost.
- Input timing: Add pauses (`--delay` or `"pause": 0.5`) between clicks and keystrokes to accommodate application render times.
