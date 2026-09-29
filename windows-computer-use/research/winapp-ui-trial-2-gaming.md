# Windows Desktop Computer Use: Empirical Trial 2 Results (Steam & DirectX Full-Screen Gaming)

**Trial Date:** 29 September 2026  
**Environment:** Windows 11 Pro (win32, x64), Display Resolution: 2520x1680, DPI: 144 (150% scaling)  
**CLI Version:** `@microsoft/winappcli@0.7.0` (Native CLI: `0.7.0`)  
**Target Suite:** Non-Native Client (Steam) and Full-Screen DirectX Application (Football Manager 2024, AppID: 2252570)  
**Status:** Completed & Empirically Validated  

---

## 1. Executive Summary

This empirical trial evaluated Microsoft's `winapp ui` CLI against non-native application runtimes and full-screen GPU-accelerated graphics pipelines, specifically:
1. **Steam**: A hybrid Chromium Embedded Framework (CEF) and VGUI client application.
2. **Football Manager 2024**: A complex, full-screen DirectX application (`siguiapp` engine) running at native 2520x1680 resolution.

The trial yielded definitive empirical answers to the limits of UI Automation (UIA) and established the operational boundaries between **Semantic Inspection** and **Visual Fallback (VLM / Coordinate Injection)**.

### Key Empirical Findings:
1. **DirectX UIA Blindness (0 Invokable Elements):**  
   `winapp ui inspect` successfully discovered the top-level game window (`HWND 198564`, class `siguiapp`, 2520x1680), but returned **0 child elements** and **0 invokable controls**. Because raw DirectX/Vulkan game loops present directly to the GPU swapchain backbuffer, standard OS accessibility trees do not exist inside the canvas.
2. **High-Fidelity DirectX Screen Capture:**  
   `winapp ui screenshot` successfully captured the full-screen DirectX backbuffer at native 2520x1680 resolution without occlusion artifacts or black screens. DWM and Windows Graphics Capture (WGC) handle hardware-accelerated swapchains transparently.
3. **Keyboard Injection via OS Input Queue (`--via send-input`):**  
   Synthetic keystroke injection via `winapp ui send-keys space -w <hwnd> --via send-input` successfully dispatched into the DirectX message pump, confirming that keyboard automation functions without UIA element handles.
4. **Coordinate Click Boundary in `winapp ui click`:**  
   `winapp ui click` strictly requires a semantic slug or text search label and rejects raw screen coordinates (`element_not_found`). To execute coordinate-based clicking on game surfaces, agents must use `winapp ui drag <x,y> <x,y>` (press-and-release), `winapp ui touch --at <x,y> -g tap`, or adapter-level Win32 `SendInput(MOUSEINPUT)`.
5. **Launcher Process Lifecycle & Steam Applaunch:**  
   Modern DRM-protected games cannot be launched by direct executable invocation (`fm.exe` exits with code 1 via `SteamAPI_RestartAppIfNecessary`). Automation agents must launch the registered protocol URI or invocation switch: `D:\Apps\Steam\steam.exe -applaunch 2252570` with `cwd = "D:\Apps\Steam"`.

---

## 2. Empirical Trial Execution & Raw Telemetry

All operations were executed programmatically via `run_trial_gaming.py` targeting `WinSta0\Default`.

### 2.1 Steam Client Discovery & Lifecycle

When Steam was invoked, `winapp ui list-windows` captured its initial bootstrap updater interface:

```json
{
  "hwnd": 330972,
  "processId": 27868,
  "processName": "steam",
  "title": "Steam",
  "label": "window",
  "width": 600,
  "height": 194,
  "ownerHwnd": 0,
  "className": "BootstrapUpdateUIClass",
  "isForeground": true
}
```

*Finding:* Steam's client lifecycle begins with a transient Win32 native dialog (`BootstrapUpdateUIClass`). Once the update handshake completes, the process migrates rendering to CEF subprocesses (`steamwebhelper.exe`) or minimizes directly to the system notification area unless an explicit main client URI (`steam://open/main`) is supplied.

---

### 2.2 Football Manager 2024: DirectX Window Detection

The game was launched via `steam.exe -applaunch 2252570`. The DirectX window was detected in 2.5 seconds:

```json
{
  "hwnd": 198564,
  "processId": 26468,
  "processName": "fm",
  "label": "window",
  "width": 2520,
  "height": 1680,
  "ownerHwnd": 0,
  "className": "siguiapp",
  "isForeground": true
}
```

- **Process:** `fm.exe` (PID: 26468)
- **Window Class:** `siguiapp` (Sports Interactive GUI Application)
- **Resolution:** 2520 x 1680 (Full display resolution)
- **DPI Scaling:** Per-monitor aware, scale factor 1.5 (144 DPI)

---

### 2.3 DirectX UIA Tree Inspection: The Semantic Limit

Executing `winapp ui inspect -w 198564 --depth 4 --json` yielded:

```json
{
  "depth": 4,
  "interactive": false,
  "hideDisabled": false,
  "hideOffscreen": false,
  "windows": [
    {
      "hwnd": 198564,
      "windowDpi": 144,
      "scale": 1.5,
      "dpiAwareness": "per-monitor-aware",
      "coordinateSpace": "physical-screen-pixels",
      "elementCount": 1,
      "elements": [
        {
          "type": "Window",
          "className": "siguiapp",
          "isEnabled": true,
          "isOffscreen": false,
          "x": 0,
          "y": 0,
          "width": 2520,
          "height": 1680,
          "selector": "win-4c8b",
          "isInvokable": false
        }
      ]
    }
  ]
}
```

Running interactive filter (`winapp ui inspect -w 198564 -i --json`) returned:
```json
{
  "elementCount": 0,
  "elements": []
}
```

#### Analytical Insight:
The entire game viewport is treated as a single monolithic `Window` element. The internal UI (buttons, menus, tactical sliders, roster lists) exists purely as GPU-rendered polygons and textures in the DirectX swapchain. **No accessibility provider interfaces (`IRawElementProviderSimple`) are implemented by the engine.** 

Therefore, any agent system attempting to automate modern games or custom canvas graphics through accessibility trees alone will be completely immobilized.

---

### 2.4 High-Resolution DirectX Screenshot Capture

Executing `winapp ui screenshot -w 198564 -o football_manager_trial.png --json` returned:

```json
{
  "filePath": "D:\\stroo\\Documents\\GitHub\\enkay-skills-workshop\\tools\\winapp-use\\football_manager_trial.png",
  "width": 2520,
  "height": 1680,
  "processId": 26468,
  "hwnd": 198564
}
```

**Visual Validation:**  
The resulting PNG artifact confirmed full-fidelity capture of the game's initial intro frame ("Kick It Out" anti-discrimination logo centered on black background).

![Football Manager Capture](file:///d:/stroo/Documents/GitHub/enkay-skills-workshop/tools/winapp-use/football_manager_trial.png)

*Key Architectural Takeaway:* `winapp ui screenshot` interfaces with Desktop Window Manager (DWM) composition buffers or Windows Graphics Capture (WGC). It does **not** rely on GDI `BitBlt` or `PrintWindow`, which historically returned black frames on hardware-accelerated DirectX/OpenGL surfaces.

---

### 2.5 Input Injection in DirectX Environments

Two modes of input injection were tested:

#### A. Mouse Coordinate Click Attempt via `winapp ui click`
Executing `winapp ui click 1260,840 -w 198564 --json` resulted in:
```json
{
  "error": {
    "code": "element_not_found",
    "message": "No element found matching '1260,840'",
    "selector": "1260,840"
  }
}
```
*Why this happened:* `winapp ui click` takes a selector query, searches the UIA tree, calculates the element's bounding box center, moves the cursor, and clicks. When passed coordinates, it treats them as a name query.

#### B. Synthetic Keystroke Injection (`send-keys --via send-input`)
Executing `winapp ui send-keys space -w 198564 --via send-input --json` resulted in:
```json
{
  "keys": "space",
  "via": "send-input",
  "actionCount": 1,
  "hwnd": 198564,
  "warnings": []
}
```
The spacebar keystroke was successfully posted into the Windows input queue via `SendInput`, correctly reaching the foreground DirectX window loop to advance intro cinematics.

---

## 3. The Dual-Mode Architecture for Windows Computer Use

The combined results of Trial 1 (WinUI, Win32, Electron) and Trial 2 (Steam, DirectX Full-Screen) prove that an agent cannot rely solely on a single paradigm. An autonomous Windows Computer Use agent requires a **Dual-Mode Adapter Architecture**:

```
                         +-----------------------------+
                         |      AI Agent Request       |
                         +--------------+--------------+
                                        |
                         +--------------v--------------+
                         |    winapp ui inspect -i     |
                         +--------------+--------------+
                                        |
                        /---------------+---------------\
                       |                                 |
              [Elements Found > 0]             [Elements Found == 0]
                       |                                 |
             +---------v---------+             +---------v---------+
             |      MODE A:      |             |      MODE B:      |
             |   SEMANTIC UIA    |             |  VISUAL FALLBACK  |
             +---------+---------+             +---------+---------+
                       |                                 |
         - Fast, deterministic tokens       - Full WGC Screen Capture
         - InvokePattern / ValuePattern     - Vision-Language Model (VLM)
         - Zero mouse cursor jitter           bounding box detection
         - Operates on hidden/isolated      - Coordinate Injection:
           desktops without display           * winapp ui drag x,y x,y
                                              * winapp ui touch --at x,y
                                              * send-keys --via send-input
```

### Mode Comparison Matrix

| Capability | Mode A: Semantic UIA | Mode B: Visual Grounding + Coordinates |
| :--- | :--- | :--- |
| **Applicable Targets** | WinUI 3, WPF, WinForms, Win32, Accessible Electron | DirectX/Vulkan 3D Games, Unreal Engine, Blender Viewports, Custom Canvas Apps |
| **Element Discovery** | Instant JSON hierarchy traversal via UIA | VLM prompt on full-screen WGC screenshot |
| **Interaction Mechanism** | `invoke`, `set-value`, `select`, `toggle` | `drag <x,y> <x,y>`, `touch --at <x,y>`, Win32 `SendInput` |
| **Cursor Impact** | **None.** Operates programmatically via patterns | Moves physical mouse cursor; requires window foreground focus |
| **Isolated Desktop Support** | **Yes.** Runs completely invisible on `WinSta0\exebox-*` | **Restricted.** WGC screenshot works, but mouse injection requires interactive desktop |
| **Token Efficiency** | **High.** Compact JSON string payloads | **Moderate.** Requires image token transmission to VLM |

---

## 4. Updates to `winapp_adapter.py`

To operationalize these findings, [`tools/winapp-use/winapp_adapter.py`](file:///d:/stroo/Documents/GitHub/enkay-skills-workshop/tools/winapp-use/winapp_adapter.py) was enhanced:

1. **Automatic Coordinate Routing:**  
   If `act(target, selector="1260,840", action="click")` receives a comma-separated coordinate pair, it bypasses slug search and dispatches `winapp ui drag 1260,840 1260,840` (press-and-release mouse simulation).
2. **Touch Injection Support:**  
   Added native `touch` action routing to `winapp ui touch --at <x,y> -g tap`.
3. **Working Directory Support for Launchers:**  
   Extended `launch_process(command, cwd)` so game launchers and DRM clients (Steam, Epic, EA) find required runtime DLLs.

---

## 5. Artifacts and References

- **Empirical Trial 2 JSON Data:** [`docs/research/winapp-ui-trial-2-gaming.json`](file:///d:/stroo/Documents/GitHub/enkay-skills-workshop/docs/research/winapp-ui-trial-2-gaming.json)
- **DirectX Capture Artifact:** [`tools/winapp-use/football_manager_trial.png`](file:///d:/stroo/Documents/GitHub/enkay-skills-workshop/tools/winapp-use/football_manager_trial.png)
- **Automated Runner Script:** [`tools/winapp-use/run_trial_gaming.py`](file:///d:/stroo/Documents/GitHub/enkay-skills-workshop/tools/winapp-use/run_trial_gaming.py)
- **Core Adapter Implementation:** [`tools/winapp-use/winapp_adapter.py`](file:///d:/stroo/Documents/GitHub/enkay-skills-workshop/tools/winapp-use/winapp_adapter.py)
- **Trial 1 Report (Native & Electron):** [`docs/research/winapp-ui-trial-results.md`](file:///d:/stroo/Documents/GitHub/enkay-skills-workshop/docs/research/winapp-ui-trial-results.md)
