# Windows Computer Use: Issue & Limitation Board

**Repository:** `enkay-skills-workshop/windows-computer-use`  
**Status:** Active  
**Last Updated:** 30 September 2026  

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
| **WCU-008** | [Input-Derived Foreground Eligibility Defeats a Foreground Lock](#wcu-008-input-derived-foreground-eligibility-defeats-a-foreground-lock) | **High** | OS / Focus Policy | **Mitigated (Test-Side)** | Phase 7 (Desktop Actions) |
| **WCU-009** | [Release-on-Partial-Input-Failure Is Untested](#wcu-009-release-on-partial-input-failure-is-untested) | **Medium** | Input / Correctness | **Open (Untested)** | Phase 7 (Desktop Actions) |
| **WCU-010** | [DPI Awareness Cannot Be Forced From The Test Runner](#wcu-010-dpi-awareness-cannot-be-forced-from-the-test-runner) | **Medium** | Test Harness / Coordinates | **Mitigated (Measure, Don't Assume)** | Phase 7 (Desktop Actions) |
| **WCU-011** | [Cross-Window Targets Are Not Expressible](#wcu-011-cross-window-targets-are-not-expressible) | **Medium** | Scope / Architecture | **Open (By Design)** | Post-Phase 7 |
| **WCU-012** | [The Desktop-Inaccessibility Check Cannot Be Provoked On An Interactive Session](#wcu-012-the-desktop-inaccessibility-check-cannot-be-provoked-on-an-interactive-session) | **Medium** | OS / Test Harness | **Partially Mitigated (Test Valve)** | Phase 7 (Desktop Actions) |

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

### WCU-008: Input-Derived Foreground Eligibility Defeats a Foreground Lock
- **Severity:** High (silently invalidates any focus-refusal precondition built on `LockSetForegroundWindow`)
- **Component:** OS / Focus Policy
- **Status:** Mitigated on the test side; the underlying behaviour is not a defect and must not be worked around in the engine
- **Observed In:** Phase 7 `test_27_refused_focus_names_the_window_that_holds_the_foreground`, full-suite runs only
- **Root Cause:** `SetForegroundWindow` succeeds when the calling process injected the most recent input. That eligibility is independent of a foreground lock, which blocks the request but does not remove the eligibility. The engine injects input throughout the suite, so a `LSFW_LOCK` taken by the foreground-owning process was not enough: the engine still took the foreground, the holder was deactivated, and losing the foreground releases the lock. The fixture's own record showed the sequence directly — `lock=True`, then `lost-foreground`, then `unlock=False`.
- **Why the engine is not at fault:** the engine's focus path is the documented one, `SetForegroundWindow` followed by verification against the real foreground. It reports `focus_refused` correctly whenever the request genuinely fails.
- **Mitigation:** the foreground fixture sends one relative mouse movement of zero distance immediately before taking the lock, so it rather than the engine is the most recent input receiver. This must originate in the foreground-owning process; input from the test runner would make the runner eligible and invalidate the test's own precheck. Waiting does not clear the eligibility — a 20-second wait loop was tried and removed.
- **Also ruled out during investigation, recorded so they are not retried:** an off-desktop window fails as `window_gone` before any focus attempt; `WS_EX_NOACTIVATE` does not prevent activation through `SetForegroundWindow`; and `AllowSetForegroundWindow(ASFW_ANY)` is not documented to override a lock, so the earlier conclusion that it did was based on an invalid experiment in which the locking process did not own the foreground.
- **Reference:** [`research/desktop-action-expansion-results.md`](research/desktop-action-expansion-results.md)

### WCU-009: Release-on-Partial-Input-Failure Is Untested
- **Severity:** Medium (a stuck button or modifier would be a real user-visible fault)
- **Component:** `engine/src/input.rs` shared injection helper
- **Status:** Open — implemented and reasoned about, not verified
- **Root Cause / Gap:** the helper checks how many events `SendInput` actually accepted, releases whatever is still held on a partial failure, and never retries. This is the single place input reaches the operating system, so an untested path here is untested everywhere.
- **Why it is untested:** provoking a genuine partial failure needs a desktop that is tearing down or refusing input, which cannot be done reliably without destabilising the interactive session the suite itself runs in. The suite verifies that all events were accepted; it does not exercise the cleanup.
- **Mitigation:** recorded as a limit rather than presented as tested. Closing this needs an isolated session or a test seam that can force a short write, neither of which exists yet.

### WCU-010: DPI Awareness Cannot Be Forced From The Test Runner
- **Severity:** Medium (coordinate mismatches present as convincing false engine bugs)
- **Component:** Test harness coordinate handling
- **Status:** Mitigated by measurement rather than configuration
- **Root Cause:** Python on this machine is system-DPI-aware through a manifest, so both `SetProcessDpiAwarenessContext` and `SetThreadDpiAwarenessContext` fail silently. Raw window calls are therefore virtualised while the engine's accessibility-derived bounds are physical, and the two disagree by the scale factor (measured 0.6662 at 150% scaling). Two separate false diagnoses came from mixing the two coordinate spaces.
- **Mitigation:** the suite measures the scale it actually gets, by comparing `GetWindowRect` against the engine's per-monitor-aware bounds, and converts explicitly. It reports the measured scale at startup. Attempting to force awareness would be misleading rather than protective, since the calls do not fail loudly.
- **Reference:** [`tests/test_phase7_desktop_actions.py`](tests/test_phase7_desktop_actions.py), `_measure_coordinate_scale`

### WCU-011: Cross-Window Targets Are Not Expressible
- **Severity:** Medium (blocks whole task shapes, not just edge cases)
- **Component:** Action target contract
- **Status:** Open by design
- **Root Cause:** a pointer target is expressed in the pixels of the observation the caller saw, and both drag endpoints must lie inside the attached window's observed frame. A drag that must leave the attached window — dropping a file onto another application, or selecting across a dialog owned by a different top-level window — is therefore not expressible.
- **Mitigation:** none yet. This is the documented boundary of the action contract rather than a defect: a bare screen coordinate is deliberately not accepted for any pointer action, which is what keeps actions tied to the window the decision was made against. Cross-window work needs either desktop-wide observation or an explicit first-class concept of a second attachable target.

### WCU-012: The Desktop-Inaccessibility Check Cannot Be Provoked On An Interactive Session
- **Severity:** Medium (the guard works; the risk is in believing it was never exercised)
- **Component:** Win32 User Subsystem / Desktop Stations, and the test harness
- **Status:** Partially mitigated. The check was wired into six actions with no test at all; it is now covered as described below, and one branch remains unverified.
- **Root Cause / Gap:** `require_interactive_desktop` refuses with `desktop_inaccessible` when the session has no usable interactive desktop. Before this work it had zero coverage — and the gap had been described as "not attempted", which reads as mere inconvenience. It is not. Three approaches were implemented and measured, and all three fail:
  - **`CreateDesktop` + `SetThreadDesktop`** does not work, because `OpenInputDesktop` reports the **window station's** input desktop, not the calling thread's. A thread attached to a private desktop still reads `Default` back. The name suggests otherwise and the intuition is wrong; the handle set, the desktop landed on, and the name the guard reads are three different things.
  - **Launching the engine on a private desktop** via `STARTUPINFO.lpDesktop` does not work, for the same reason, and the engine additionally re-attaches itself to the input desktop during startup.
  - **`CreateWindowStation`** fails with `ERROR_ACCESS_DENIED` (err 5) under every access mask tried, because it requires `SE_CREATE_WINDOW_STATION`, which an interactive user does not hold.
  - The only routes left are locking the session or `SwitchDesktop`, both of which destroy the desktop the suite runs in.
- **What the check is really for:** verifying the window station's input desktop is the interactive one. That is exactly right for detecting a non-interactive deployment, and it is what the guard now documents itself as doing. It is simply not something thread-level desktop control can influence. This is the same boundary [WCU-003](#wcu-003-headless-desktop-input-failure-no_interactive_desktop) records for `winapp ui`, expressed with the engine's own error code.
- **Resolution (Phase 7 follow-up):** the Win32 query was reduced to the facts it gathers and the decision split into a pure verdict, so the decision is tested directly against the names Windows actually uses. The live query's passing branch is tested against the real API. A one-way test valve then lets the integration suite prove all six input-dispatching actions refuse and dispatch nothing, reading the application's own state afterwards.
- **Why the valve is acceptable:** it can only ever force the refusing verdict. No value and no code path turns a failing check into a passing one, so it cannot be used to weaken the guard. A valve leaked into production makes every action fail loudly rather than letting input through. It is honoured in release builds deliberately, because a valve restricted to debug builds would let a suite run against a release binary pass while asserting nothing.
- **Still unverified:** the branch where the operating system refuses to hand over the input desktop at all, i.e. a genuinely locked session. That is the one case this check exists for, and it cannot be exercised without changing the state of the live session. Closing it needs an isolated session or a separate host, neither of which exists here.
- **Also recorded:** a window focus request composes no desktop check. That is a decision, not an omission — focusing dispatches no input, so the harm this check prevents cannot arise from it, and it is pinned by a test so it reads as deliberate.
- **Reference:** [`specs/desktop-inaccessibility-guard-testing.md`](specs/desktop-inaccessibility-guard-testing.md), [`engine/src/win_utils.rs`](engine/src/win_utils.rs), [`tests/test_desktop_inaccessible_guard.py`](tests/test_desktop_inaccessible_guard.py)
