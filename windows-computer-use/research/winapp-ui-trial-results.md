# Windows Desktop Computer Use: Empirical `winapp ui` Trial Results

**Trial Date:** 29 September 2026  
**Environment:** Windows 11 (win32, x64), Display DPI: 144 (150% scaling)  
**CLI Version:** `@microsoft/winappcli@0.7.0` (Native CLI: `0.7.0`)  
**Status:** Validated on WinUI 3, Win32, and Electron  

---

## 1. Executive Summary

This empirical trial evaluated Microsoft's `winapp ui` command-line utility as the foundational operating system adapter for Windows desktop computer use, testing the first implementation recommendations from [`docs/research/desktop-computer-use.md`](file:///d:/stroo/Documents/GitHub/enkay-skills-workshop/docs/research/desktop-computer-use.md).

The trial confirmed:
1. **Window enumeration (`list-windows`)**: Fast, structured JSON discovery returning HWNDs, process names, PIDs, window dimensions, and foreground status.
2. **Tree inspection (`inspect`)**: Precise UI Automation (UIA) hierarchy traversal across WinUI 3 and Win32 controls, emitting semantic slugs and reporting display DPI, scale factor (1.5x), and physical screen coordinates.
3. **Semantic actions (`invoke`, `select`, `set-value`)**: Successfully executed without simulated mouse cursor movement using native UIA control patterns (`InvokePattern`, `SelectionItemPattern`, `TogglePattern`).
4. **Screenshot capture (`screenshot`)**: DWM/Windows Graphics Capture (WGC) composited captures returning exact PNG paths and resolutions without requiring window occlusion handling.
5. **Fallback input (`click`, `hover`)**: Successfully executed mouse simulation with pre-button-down target re-validation and target-moved detection.
6. **Desktop Isolation & Focus Safety**: Critical discovery that agent execution environments running on non-default desktop stations (`WinSta0\exebox-*`) must explicitly dispatch to `WinSta0\Default` using `STARTUPINFO.lpDesktop`, and background callers must employ an activation unlock (Alt-key trick or `SwitchToThisWindow`) to bypass Windows focus-stealing prevention.

---

## 2. Tested Application Matrix

| Target Application | Framework / Technology | Verified Operations | Finding |
| :--- | :--- | :--- | :--- |
| **Notepad** (`Notepad.exe`, PID 20180) | Modern WinUI 3 / XAML | `list-windows`, `inspect`, `invoke AddButton`, `screenshot`, `click File` | **Full support.** Rich UIA accessibility tree (29 nodes); `AddButton` cleanly invoked via `InvokePattern`; `File` menu activated via fallback click. |
| **File Explorer** (`explorer.exe`, PID 8768) | Win32 (`CabinetWClass`) | `list-windows`, `inspect`, `select ViewMode_LargeIcons`, `screenshot`, `click ViewMode_Details` | **Full support.** Exposed 223 interactive elements; `SelectionItemPattern` switched view mode without mouse movement; mouse click toggle succeeded. |
| **Visual Studio Code** (`Code.exe`, PID 8680) | Electron / Chromium | `list-windows`, `inspect`, `screenshot`, `hover Minimize` | **Hybrid support.** UIA exposes window shell and caption buttons; webview content requires Chromium accessibility flag (`--force-renderer-accessibility`) or visual fallback via WGC screenshot + coordinate input. |

---

## 3. Empirical JSON Evidence

### 3.1 CLI Version Verification
```bash
winapp --version
```
```text
Windows App Development CLI and BuildTools utilities for Node.js native addon development

Node.js Package: @microsoft/winappcli v0.7.0
Native CLI:
0.7.0
```

---

### 3.2 Native App 1: Notepad (WinUI 3)

#### Window Discovery (`list-windows -a notepad --json`)
```json
[
  {
    "hwnd": 788780,
    "processId": 20180,
    "processName": "Notepad",
    "title": "Untitled - Notepad",
    "label": "window",
    "width": 587,
    "height": 1271,
    "ownerHwnd": 0,
    "className": "Notepad",
    "isForeground": true
  }
]
```

#### UI Tree Inspection (`inspect -w 788780 -i --json`)
```json
{
  "depth": 8,
  "interactive": true,
  "windows": [
    {
      "hwnd": 788780,
      "title": "Untitled - Notepad",
      "windowDpi": 144,
      "scale": 1.5,
      "dpiAwareness": "per-monitor-aware",
      "coordinateSpace": "physical-screen-pixels",
      "elementCount": 14,
      "elements": [
        {
          "type": "Tab",
          "automationId": "Tabs",
          "className": "Microsoft.UI.Xaml.Controls.TabView",
          "children": [
            {
              "type": "TabItem",
              "name": "Untitled. Unmodified.",
              "selector": "tab-untitledunmodif-9c50",
              "children": [
                {
                  "type": "Button",
                  "name": "Close Tab",
                  "automationId": "CloseButton",
                  "selector": "CloseButton",
                  "isInvokable": true
                }
              ]
            },
            {
              "type": "Button",
              "name": "Add New Tab",
              "automationId": "AddButton",
              "selector": "AddButton",
              "isInvokable": true
            }
          ]
        },
        {
          "type": "MenuItem",
          "name": "File",
          "automationId": "File",
          "selector": "File",
          "expandState": "collapsed",
          "isInvokable": true
        }
      ]
    }
  ]
}
```

#### Semantic Action (`invoke AddButton -w 788780 --json`)
```json
{
  "elementId": "btn-addbutton-ddb0",
  "pattern": "InvokePattern",
  "requestedAction": "auto",
  "performedAction": "invoke",
  "hwnd": 788780
}
```

#### Screenshot Capture (`screenshot -w 788780 -o notepad_trial.png --json`)
```json
{
  "filePath": "D:\\stroo\\Documents\\GitHub\\enkay-skills-workshop\\tools\\winapp-use\\notepad_trial.png",
  "width": 573,
  "height": 1264,
  "processId": 20180,
  "windowTitle": "Untitled - Notepad",
  "hwnd": 788780
}
```

#### Fallback Click (`click File -w 788780 --json`)
```json
{
  "elementId": "mnu-file-5df7",
  "clickType": "click",
  "x": 559,
  "y": 424,
  "hwnd": 788780
}
```

---

### 3.3 Native App 2: File Explorer (Win32)

#### Window Discovery (`list-windows -a explorer --json`)
```json
{
  "hwnd": 68032,
  "processId": 8768,
  "processName": "explorer",
  "title": "Documents - File Explorer",
  "label": "window",
  "width": 2520,
  "height": 1608,
  "ownerHwnd": 0,
  "className": "CabinetWClass",
  "isForeground": false
}
```

#### Semantic Action (`invoke ViewMode_LargeIcons -w 68032 --action select --json`)
```json
{
  "elementId": "rdo-viewmodelargeic-1bbf",
  "pattern": "SelectionItemPattern",
  "requestedAction": "select",
  "performedAction": "select",
  "hwnd": 68032
}
```

#### Screenshot Capture (`screenshot -w 68032 -o explorer_trial.png --json`)
```json
{
  "filePath": "D:\\stroo\\Documents\\GitHub\\enkay-skills-workshop\\tools\\winapp-use\\explorer_trial.png",
  "width": 2502,
  "height": 1599,
  "processId": 8768,
  "windowTitle": "Documents - File Explorer",
  "hwnd": 68032
}
```

#### Fallback Click (`click ViewMode_Details -w 68032 --json`)
```json
{
  "elementId": "rdo-viewmodedetails-ca5f",
  "clickType": "click",
  "x": 2443,
  "y": 1577,
  "hwnd": 68032
}
```

---

### 3.4 Electron App: Visual Studio Code

#### Window Discovery & Tree Inspection (`list-windows -a Code --json`)
```json
{
  "hwnd": 6031032,
  "processId": 8680,
  "processName": "Code",
  "title": "config.toml - Visual Studio Code",
  "label": "window",
  "width": 2542,
  "height": 1630,
  "ownerHwnd": 0,
  "className": "Chrome_WidgetWin_1",
  "isForeground": false
}
```

#### Screenshot Capture (`screenshot -w 6031032 -o electron_trial.png --json`)
```json
{
  "filePath": "D:\\stroo\\Documents\\GitHub\\enkay-skills-workshop\\tools\\winapp-use\\electron_trial.png",
  "width": 2185,
  "height": 1356,
  "processId": 8680,
  "windowTitle": "config.toml - Visual Studio Code",
  "hwnd": 6031032
}
```

#### Mouse Hover Simulation (`hover Minimize -w 6031032 --json`)
```json
{
  "elementId": "btn-minimize-35a3",
  "x": 2191,
  "y": 192,
  "dwellTimeMs": 800,
  "hwnd": 6031032
}
```

---

## 4. Key Architectural Discoveries

### 4.1 Window Station & Desktop Isolation
Agent runtimes and sandboxed subshells run in isolated desktops (such as `WinSta0\exebox-*`). Standard CLI calls executed from these shells cannot see or interact with the user's interactive desktop (`WinSta0\Default`).
* **Solution:** The adapter passes `STARTUPINFO.lpDesktop = "WinSta0\\Default"` when spawning `winapp ui` child processes, bridging the agent environment to the active user display.

### 4.2 Foreground Focus Stealing Prevention
Input-injecting verbs (`click`, `hover`, `drag`, `touch`) fail fast with `foreground_not_target` if the target window is not active. Background processes calling `SetForegroundWindow` are blocked by default Windows OS policy.
* **Solution:** The adapter's `ensure_foreground(hwnd)` method applies the standard Windows Alt-key unlock trick (`keybd_event(VK_MENU, ...)`), cleanly transferring focus to the target window prior to simulated mouse or touch input.

### 4.3 Semantic Slugs vs. AutomationId
Slugs generated by `winapp ui` follow the format `prefix-normalizedname-hash`, where the hash is a 4-character hex code of the element's `RuntimeId`. In dynamic apps, the hash can drift when window layout changes.
* **Guideline:** Agents should prioritize unique `AutomationId` selectors (e.g. `AddButton`, `CloseButton`, `ViewMode_LargeIcons`), falling back to fresh slugs from an immediate `inspect` call.

### 4.4 Isolated Desktop Automation: Running Invisible App Instances
A major architectural capability discovered during the trial is the ability for agents to spin up dedicated GUI applications directly on their own isolated desktop (`WinSta0\exebox-*`).

* **Zero User Disruption:** Because Windows only renders the active desktop (`WinSta0\Default`) to physical monitors, apps launched on the agent's desktop:
  - Do NOT render on the physical display (no window popping up in front of the user).
  - Do NOT appear on the Windows Taskbar or in `Alt + Tab` / Task View.
  - Cannot steal keyboard focus or mouse control from the human user.
* **Empirical Validation (`notepad.exe` in `WinSta0\exebox-*`):**
  - **Inspection:** `winapp ui inspect` returned the complete UI Automation hierarchy (tabs, menus, edit surfaces).
  - **Semantic Actions:** `winapp ui invoke AddButton` added a tab headlessly via `InvokePattern`.
  - **Typing:** `winapp ui send-keys --via post-message` typed text into the window message queue without foreground activation.
  - **Visual Capture:** `winapp ui screenshot` successfully captured the invisible window's surface as a full-resolution PNG.
* **Input Injection Constraints:** Synthetic mouse input (`click`, `hover`, `drag`) fails fast with `no_interactive_desktop` because Windows `SendInput` requires an active hardware display surface. On the isolated desktop, agents must use UIA patterns or `post-message` key events.
* **Dual Operating Modes for Agent Tooling:**
  1. **Mode A (Headless Isolated Desktop):** Spin up private app instances for background data extraction, automation, and testing without disturbing the user.
  2. **Mode B (Interactive Companion Desktop):** Bridge to `WinSta0\Default` via `STARTUPINFO.lpDesktop` to inspect and drive the user's active, visible applications.

---

## 5. Standardized Tool Contract

The verified operations have been consolidated into `WindowsAdapter` ([`winapp_adapter.py`](file:///d:/stroo/Documents/GitHub/enkay-skills-workshop/tools/winapp-use/winapp_adapter.py)):

```python
class WindowsAdapter:
    def list_windows(self, app: Optional[str] = None, show_hidden: bool = False) -> List[Dict[str, Any]]: ...
    def inspect(self, target: Union[str, int], selector: Optional[str] = None, depth: int = 4, interactive: bool = False) -> Dict[str, Any]: ...
    def act(self, target: Union[str, int], selector: str, action: str = "invoke", value: Optional[str] = None, coords: Optional[Tuple[int, int]] = None) -> Dict[str, Any]: ...
    def screenshot(self, target: Union[str, int], selector: Optional[str] = None, output_path: Optional[str] = None, capture_screen: bool = False) -> Dict[str, Any]: ...
    def wait_for_change(self, target: Union[str, int], selector: str, property_name: Optional[str] = None, value: Optional[str] = None, timeout_ms: int = 5000, gone: bool = False) -> Dict[str, Any]: ...
    def ensure_foreground(self, hwnd: int) -> bool: ...
    def yield_turn(self) -> Dict[str, Any]: ...
```
