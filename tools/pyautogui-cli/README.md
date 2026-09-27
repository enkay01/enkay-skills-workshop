# PyAutoGUI macOS CLI (laptop-control)

Production Python CLI automation tool for macOS using PyAutoGUI, OpenCV, Pillow, and PyObjC with automatic Retina display coordinate scaling.

## Features

- PEP 723 inline script metadata for direct execution via `uv run`.
- Standalone executable shell wrapper at `bin/laptop-control`.
- Automatic detection of macOS CoreGraphics Retina scale factor (e.g. 2.0x).
- Dual coordinate support: logical coordinates for UI interaction and physical pixel translation for screenshots and computer vision templates.
- Multi-scale template matching via OpenCV normalized cross-correlation.
- Batch action execution with delays and dry-run validation.
- JSON output support (`--json`) across all commands.

## Installation and Requirements

The script uses PEP 723 metadata to manage dependencies on demand via `uv`:

```bash
# Verify uv is installed
uv --version
```

Dependencies declared in `laptop_control.py`:
- `pyautogui`
- `pillow`
- `opencv-python`
- `pyobjc-core`
- `pyobjc-framework-Quartz`

## Execution Methods

### 1. Via uv run

```bash
/Users/iannkwocha/.local/bin/uv run tools/pyautogui-cli/laptop_control.py size
```

### 2. Via the executable wrapper script

```bash
tools/pyautogui-cli/bin/laptop-control size
```

## Available Commands

### Display and Position

- `size [--json]`: Displays logical points, physical framebuffer pixels, and Retina scale factor.
- `position [--json]`: Displays current mouse cursor position in logical points and physical pixels.
- `self-test [--json]`: Runs self-diagnostics verifying display metrics, cursor coordinates, and OpenCV template matching.

### Mouse Control

- `move <x> <y> [--duration <sec>] [--physical] [--json]`: Moves cursor to target coordinates.
- `click <x> <y> [--button left|right|middle] [--clicks <n>] [--interval <sec>] [--physical] [--json]`: Clicks target coordinates.
- `double-click <x> <y> [--button left|right|middle] [--interval <sec>] [--physical] [--json]`: Double-clicks target coordinates.
- `drag <x1> <y1> <x2> <y2> [--duration <sec>] [--button left|right|middle] [--physical] [--json]`: Drags cursor between coordinates.
- `scroll <clicks> [--x <x>] [--y <y>] [--physical] [--json]`: Scrolls vertically (positive=up, negative=down).

Note: Coordinates default to logical points. Use `--physical` if coordinates come directly from an unscaled screenshot.

### Keyboard Control

- `type <text> [--interval <sec>] [--paste] [--json]`: Types text or pastes it via clipboard (`--paste`).
- `press <key> [--json]`: Presses a key (`enter`, `esc`, `backspace`, `tab`, `up`, `down`, `space`, etc.).
- `hotkey <keys...> [--json]`: Presses a key combination (e.g. `command a`, `cmd shift 4`).

### Vision and Capture

- `screenshot [--output <path>] [--logical] [--json]`: Captures the display. Saves physical resolution by default, or logical resolution if `--logical` is passed.
- `locate <template_image> [--confidence <0.0-1.0>] [--click] [--json]`: Locates an image template using OpenCV multi-scale matching and outputs logical bounding box and center coordinates.

### Application Control and Batch

- `open-app <app_name> [--wait <sec>] [--json]`: Launches or activates macOS application using `open -a` and AppleScript.
- `batch <steps_file.json> [--delay <sec>] [--dry-run] [--stop-on-error] [--json]`: Executes an array of actions sequentially.

## Batch File Format

Example `sample_batch.json`:

```json
[
  {"action": "open-app", "app": "Notes", "wait": 0.5},
  {"action": "move", "x": 300, "y": 200, "duration": 0.2},
  {"action": "click", "x": 300, "y": 200, "button": "left"},
  {"action": "type", "text": "Automation test\n", "interval": 0.02},
  {"action": "pause", "duration": 0.5}
]
```

## Running Tests

Run the test suite via python or uv:

```bash
/Users/iannkwocha/.local/bin/uv run --with pillow tools/pyautogui-cli/tests/test_laptop_control.py -v
```

## Next feature
Use the mac accessibiility APIs to interact rather than user's pointer.
https://www.youtube.com/watch?v=ZSQb5fzRFPw
https://www.mindstudio.ai/blog/openai-codex-computer-use-agents
https://www.buildmvpfast.com/blog/openai-codex-background-computer-use-desktop-agent-2026


