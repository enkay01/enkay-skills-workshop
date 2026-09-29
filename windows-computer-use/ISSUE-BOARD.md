# Windows Computer Use: Issue & Limitation Board

**Repository:** `enkay-skills-workshop/windows-computer-use`  
**Status:** Active  
**Last Updated:** 29 September 2026  

---

## Overview

This board tracks all technical limitations, architectural constraints, security boundaries, and edge cases discovered during empirical trials of Microsoft's `winapp ui` CLI and native Windows automation across WinUI 3, Win32, Electron, Steam (CEF), and full-screen DirectX applications.

---

## Issue Status Matrix

| ID | Issue Title | Severity | Category | Status | Target Phase |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **WCU-001** | [DirectX & GPU Swapchain UIA Blindness (0 Elements)](#wcu-001-directx--gpu-swapchain-uia-blindness-0-elements) | **Critical** | Accessibility / UIA | **Architectural Bound** | Phase 2 (VLM Fallback) |
| **WCU-002** | [`winapp ui click` Rejection of Raw Coordinate Selectors](#wcu-002-winapp-ui-click-rejection-of-raw-coordinate-selectors) | **High** | CLI / Input | **Mitigated** | Phase 1 (Adapter Layer) |
| **WCU-003** | [Headless Desktop Input Failure (`no_interactive_desktop`)](#wcu-003-headless-desktop-input-failure-no_interactive_desktop) | **High** | OS / Window Stations | **Architectural Bound** | Phase 2 (Station Routing) |
| **WCU-004** | [Foreground Lock & Focus-Stealing Restriction (`foreground_not_target`)](#wcu-004-foreground-lock--focus-stealing-restriction-foreground_not_target) | **High** | OS / Security | **Resolved (Strict Verification)** | Phase 5 (Guarded Action) |
| **WCU-005** | [Process Lifecycle & DRM / Launcher Entanglement](#wcu-005-process-lifecycle--drm--launcher-entanglement) | **Medium** | Process Management | **Mitigated** | Phase 1 (Adapter Layer) |
| **WCU-006** | [CLI Subprocess Tax & Spawning Latency (~150ms/call)](#wcu-006-cli-subprocess-tax--spawning-latency-150mscall) | **Medium** | Performance / Latency | **Resolved** | Phase 1 (`wcu-engine` IPC) |
| **WCU-007** | [Electron Shell vs Webview Accessibility Gaps](#wcu-007-electron-shell-vs-webview-accessibility-gaps) | **Medium** | Framework Integration | **Mitigated** | Phase 1 (Flags / Visual) |

---

## Detailed Issue Tracking

### WCU-001: DirectX & GPU Swapchain UIA Blindness (0 Elements)
- **Severity:** Critical (Blocks semantic automation on games and 3D applications)
- **Component:** UI Automation Provider Interface
- **Status:** Architectural Boundary (Wontfix via UIA)
- **Observed In:** Football Manager 2024 (`fm.exe`, HWND 198564, class `siguiapp`, 2520x1680)
- **Root Cause:** Modern games (DirectX 11/12, Vulkan, OpenGL), 3D viewports (Blender, Unreal/Unity Editor viewports), and custom GPU canvas apps present directly to GPU swapchains. They do not register `IRawElementProviderSimple` COM interfaces with Windows UI Automation. `winapp ui inspect` discovers only the outer window shell (`elementCount: 0`).
- **Mitigation / Solution:**
  - Route all canvas/game interactions through **Mode B (Visual Fallback)**.
  - Rely on Windows Graphics Capture (WGC) for frame acquisition and local CV/OCR or VLM grounding to generate click targets.
- **Reference:** [`research/winapp-ui-trial-2-gaming.md`](file:///d:/stroo/Documents/GitHub/enkay-skills-workshop/windows-computer-use/research/winapp-ui-trial-2-gaming.md)

---

### WCU-002: `winapp ui click` Rejection of Raw Coordinate Selectors
- **Severity:** High (Prevents direct coordinate fallback using default click command)
- **Component:** `winapp ui` CLI (`click` subcommand)
- **Status:** Mitigated (In `winapp_adapter.py`)
- **Observed In:** Trial 2 coordinate click simulation on DirectX game surface
- **Root Cause:** `winapp ui click <selector>` parses the selector strictly as an automation slug (e.g., `btn-save-1a2b`) or accessible name string. Passing `"1260,840"` causes the CLI to search for an element named `"1260,840"`, failing with `element_not_found`.
- **Mitigation / Solution:**
  - Updated `winapp_adapter.py` `act()` method to inspect selector strings.
  - If selector matches coordinate pattern `^\d+,\d+$`, the adapter automatically translates the call to `winapp ui drag <x,y> <x,y>` (press-and-release mouse simulation) or `winapp ui touch --at <x,y> -g tap`.
- **Reference:** [`prototypes/winapp_adapter.py`](file:///d:/stroo/Documents/GitHub/enkay-skills-workshop/windows-computer-use/prototypes/winapp_adapter.py#L228-L248)

---

### WCU-003: Headless Desktop Input Failure (`no_interactive_desktop`)
- **Severity:** High (Restricts mouse simulation on isolated agent desktops)
- **Component:** Win32 User Subsystem / Desktop Stations
- **Status:** Architectural Boundary
- **Observed In:** Trial 1 background agent subshell on `WinSta0\exebox-*`
- **Root Cause:** Windows hardware mouse simulation (`mouse_event`, `SendInput` with `INPUT_MOUSE`) requires an attached physical display session on an interactive desktop. Calling `winapp ui click` or `hover` on an isolated desktop station returns `no_interactive_desktop`.
- **Mitigation / Solution:**
  - **Headless Semantic Path:** Semantic UIA patterns (`invoke`, `set-value`, `select`, `toggle`) and `send-keys --via post-message` do **not** require an interactive desktop and execute perfectly in the dark.
  - **Interactive Visual Path:** When coordinate mouse simulation is required, the target window must be launched on `WinSta0\Default` using `STARTUPINFO.lpDesktop = "WinSta0\\Default"`.
- **Reference:** [`research/winapp-ui-trial-results.md`](file:///d:/stroo/Documents/GitHub/enkay-skills-workshop/windows-computer-use/research/winapp-ui-trial-results.md) Section 4.4

---

### WCU-004: Foreground Lock & Focus-Stealing Restriction (`foreground_not_target`)
- **Severity:** High (Causes flaky input simulation from background agent scripts)
- **Component:** Win32 `SetForegroundWindow` Policy
- **Status:** Resolved (Strict Verification, 29 Sep 2026)
- **Observed In:** Trial 1 & Trial 2 background execution
- **Root Cause:** Windows enforces strict focus-stealing prevention. Background processes cannot unilaterally bring a window to the foreground. If `winapp ui click` runs on a non-foreground window, it aborts with `foreground_not_target` to prevent misclicking whatever the user is currently looking at.
- **Resolution (Phase 5):** Replaced simulated focus-stealing hacks (`keybd_event(VK_MENU)`) with honest verification. The Rust engine polls foreground window state and verifies hit-test ownership immediately before dispatching `SendInput`. If the window is occluded or not in the foreground, it returns `foreground_changed` or `target_occluded`, preventing misclicks safely.
- **Reference:** [`engine/src/input.rs`](file:///d:/stroo/Documents/GitHub/enkay-skills-workshop/windows-computer-use/engine/src/input.rs)

---

### WCU-005: Process Lifecycle & DRM / Launcher Entanglement
- **Severity:** Medium (Fails direct executable execution)
- **Component:** Process Creation / Steam / DRM Handshake
- **Status:** Mitigated (In `winapp_adapter.py` & `run_trial_gaming.py`)
- **Observed In:** Trial 2 Steam and Football Manager 2024 initialization
- **Root Cause:** Launching `fm.exe` directly exits immediately (code 1) because games instrumented with `SteamAPI_RestartAppIfNecessary(AppID)` require Steam as the parent process. Furthermore, Steam requires its working directory set to its root directory to load VGUI/CEF DLLs.
- **Mitigation / Solution:**
  - Added optional `cwd` parameter to `WindowsAdapter.launch_process(command, cwd)`.
  - Dispatched game launches via the registered URI or launcher switch: `steam.exe -applaunch 2252570` with `cwd = "D:\Apps\Steam"`.
- **Reference:** [`prototypes/run_trial_gaming.py`](file:///d:/stroo/Documents/GitHub/enkay-skills-workshop/windows-computer-use/prototypes/run_trial_gaming.py#L76-L80)

---

### WCU-006: CLI Subprocess Tax & Spawning Latency (~150ms/call)
- **Severity:** Medium (Latency bottleneck for rapid interactive loops)
- **Component:** Process Execution / Node.js & CLI Packaging
- **Status:** Resolved (29 Sep 2026)
- **Observed In:** All operations executed via `winapp ui <cmd>`
- **Root Cause:** Each CLI call creates a new process (`cmd.exe /c winapp ...`), loads Node/native dependencies, initializes COM and UIA, parses JSON arguments, traverses the tree, writes JSON to stdout, and exits. This imposes a fixed tax of ~100–250ms per action.
- **Resolution (Phase 1):** Built `wcu-engine.exe` in Rust providing a persistent user-session daemon over binary framed stdio pipe. Latency per request dropped from ~150ms to **2.8ms**.
- **Reference:** [`engine/src/main.rs`](file:///d:/stroo/Documents/GitHub/enkay-skills-workshop/windows-computer-use/engine/src/main.rs)

---

### WCU-007: Electron Shell vs Webview Accessibility Gaps
- **Severity:** Medium (Incomplete tree on default Chromium runtimes)
- **Component:** Chromium Accessibility Engine
- **Status:** Mitigated
- **Observed In:** Visual Studio Code (Trial 1)
- **Root Cause:** Chromium optimizes performance by disabling accessibility rendering until an active screen reader or UIA listener signals need. By default, `winapp ui inspect` only sees the outer Win32 frame and caption buttons.
- **Mitigation / Solution:**
  - Launch Electron apps with `--force-renderer-accessibility` flag, or
  - Dispatch a focus/click event into the webview body to trigger Chromium's accessibility initialization, or
  - Fall back to WGC screenshot + coordinate injection.
- **Reference:** [`research/winapp-ui-trial-results.md`](file:///d:/stroo/Documents/GitHub/enkay-skills-workshop/windows-computer-use/research/winapp-ui-trial-results.md) Section 3.4
