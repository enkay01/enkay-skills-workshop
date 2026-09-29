# Desktop computer use: macOS reference and Windows options

Research date: 27 September 2026. The macOS reference is pinned as the `tools/macos-use-sdk` submodule at commit `a2d7866355bca07faf476c5d180c8664b1992f8c`.

## Scope

Here, **computer use means inspecting and controlling the whole desktop, including native apps and their windows**. A tool that only drives Chrome or a browser tab does not meet that requirement. The research below covers the operating system accessibility tree, application and window targeting, screenshots, and input.

## What MacosUseSDK actually provides

The package is a Swift 6 library plus six command-line tools for macOS 12 and later. It links AppKit and ApplicationServices and declares no external packages. Its CLI tools cover app opening, accessibility traversal, input, optional visual highlighting, and combined actions. There is no server or model loop in the package; an agent would call these tools and decide what to do next. [Package manifest](https://github.com/mediar-ai/MacosUseSDK/blob/a2d7866355bca07faf476c5d180c8664b1992f8c/Package.swift), [README](https://github.com/mediar-ai/MacosUseSDK/blob/a2d7866355bca07faf476c5d180c8664b1992f8c/README.md).

Traversal uses macOS Accessibility APIs to inspect an app by PID. It starts from the application element, visits window and child elements, and emits compact data such as role, text, position, and size. It checks Accessibility trust and can prompt the user to grant it. Traversal activates the target app when needed. Its hard limits are depth 100, 2,000 collected elements, five seconds, and 200 children per container. The `visible-only` option checks for geometry; it does not establish that a control is onscreen and unobscured. [Traversal source](https://github.com/mediar-ai/MacosUseSDK/blob/a2d7866355bca07faf476c5d180c8664b1992f8c/Sources/MacosUseSDK/AccessibilityTraversal.swift).

Input uses CoreGraphics events for clicks, mouse movement, scrolling, key chords, and Unicode text. The source also exposes semantic Accessibility actions to press an element, set its value, or change selection, located by PID and screen point. Combined actions can traverse before and after an action and return a diff. This is useful for an observe, act, verify loop, although a point is a fragile element identifier when layouts change. [Input source](https://github.com/mediar-ai/MacosUseSDK/blob/a2d7866355bca07faf476c5d180c8664b1992f8c/Sources/MacosUseSDK/InputController.swift), [Accessibility actions](https://github.com/mediar-ai/MacosUseSDK/blob/a2d7866355bca07faf476c5d180c8664b1992f8c/Sources/MacosUseSDK/AccessibilityActions.swift), [combined actions](https://github.com/mediar-ai/MacosUseSDK/blob/a2d7866355bca07faf476c5d180c8664b1992f8c/Sources/MacosUseSDK/CombinedActions.swift).

The checked source does **not** contain screenshot capture, an Accessibility notification watcher, or a standalone API to enumerate and select a particular window. These are gaps for a complete desktop tool, even though the README suggests listening for changes. The repository's [companion MCP server](https://github.com/mediar-ai/mcp-server-macos-use/blob/main/Sources/MCPServer/main.swift) is a useful separate reference for window handling and agent-facing tool design. The SDK cannot be built or exercised on this Windows host.

## Windows options

| Option | Fit for desktop and native apps | Finding |
| --- | --- | --- |
| [Microsoft `winapp ui`](https://learn.microsoft.com/en-us/windows/apps/dev-tools/winapp-cli/ui-automation) | Strong first prototype | Its CLI inspects and acts on UI Automation trees across Win32, WinForms, WPF, Electron, and WinUI 3 apps. It supports app/window targeting, structured inspection, semantic actions, screenshots, and injected mouse or keyboard input when needed. The [CLI is in public preview](https://learn.microsoft.com/en-us/windows/apps/dev-tools/winapp-cli/), so pin and test a version before building on its output. |
| [Windows UI Automation](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-uiautomationoverview) | Stable underlying API | The desktop is the root of a tree of app windows and controls. Properties and control patterns let a client inspect and act on native UI. This is the direct implementation route if the preview CLI lacks a needed feature. |
| [pywinauto](https://pywinauto.readthedocs.io/en/latest/) | Python prototype option | Supports UIA and Win32 backends. Its [release history](https://github.com/pywinauto/pywinauto/releases) warrants a maintenance check before making it the sole backend. |
| [Power Automate Desktop](https://learn.microsoft.com/en-us/power-automate/desktop-flows/desktop-automation) | Recorded workflow option | Automates desktop windows and UI elements, but its flow model is a less direct fit for an agent's repeated inspect and act calls. |
| [Appium Windows Driver](https://github.com/appium/appium-windows-driver) and [WinAppDriver](https://github.com/microsoft/WinAppDriver) | Only if WebDriver compatibility is required | Appium's driver depends on WinAppDriver. Its [documentation](https://github.com/appium/appium-windows-driver#readme) notes that the Microsoft server has not been regularly maintained. |

For controls missing from UI Automation, add a visual fallback: capture a window or display with [Windows Graphics Capture](https://learn.microsoft.com/en-us/windows/uwp/audio-video-camera/screen-capture) or [MSS](https://github.com/BoboTiG/python-mss), then use mouse and keyboard input against the confirmed foreground window. [PyAutoGUI](https://pyautogui.readthedocs.io/en/latest/) is a possible high-level input layer. Coordinate actions need fresh screenshots because window moves, scaling, and multiple monitors change their meaning. Windows [SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput) is constrained by integrity levels, so actions against elevated apps can fail from an ordinary process.

## Suggested first implementation

1. On this Windows machine, trial `winapp ui` against two native apps, such as Notepad and File Explorer, plus one Electron app. Confirm window enumeration, tree inspection, a semantic action, screenshot capture, and a fallback click. Record exact CLI version and JSON output.
2. Wrap those operations in a small tool contract: `list_windows`, `inspect`, `act`, `screenshot`, and `wait_for_change`. Require app/window identity on every action and inspect again after a change. Prefer a UIA selector or control pattern; use coordinates only with a recent screenshot and a confirmed foreground window.
3. Keep the agent loop separate from the operating system adapter. The macOS SDK's before/after traversal is a good pattern, but its missing screenshot and window APIs show why an adapter needs explicit capabilities and clear failure results.

### Trial 1 Status: Native & Electron Core (Completed 29 September 2026)

The first implementation trial was executed against Notepad (WinUI 3), File Explorer (Win32), and Visual Studio Code (Electron) using `winapp` CLI v0.7.0. All five core operations (`list_windows`, `inspect`, `act`, `screenshot`, fallback `click`/`hover`) were empirically validated.
* **Isolated Desktop Discovery:** Agents running on custom window station desktops (`WinSta0\exebox-*`) can launch their own private GUI app instances completely invisibly to the human user (no window on monitors, no taskbar, no Alt+Tab, no focus stealing) and drive them headlessly via UIA patterns, `post-message` key events, and frame screenshots.
* Detailed results, JSON outputs, and the adapter module are documented in [winapp-ui-trial-results.md](winapp-ui-trial-results.md) and [tools/winapp-use/](../../tools/winapp-use/).

### Trial 2 Status: Non-Native & Full-Screen Gaming Applications (Completed 29 September 2026)

Trial 2 evaluated Microsoft's `winapp ui` CLI against non-native runtimes and raw GPU graphics pipelines using Steam (CEF/VGUI hybrid) and Football Manager 2024 (DirectX full-screen at 2520x1680, AppID: 2252570).
* **DirectX UIA Blindness:** Full-screen games present directly to GPU swapchain backbuffers and implement 0 accessibility providers (`elementCount: 0` inside game canvas). Semantic UIA automation cannot interact with internal game controls.
* **Flawless WGC Screen Capture:** `winapp ui screenshot` successfully captured the full-screen DirectX backbuffer at native 2520x1680 without GDI black screens or occlusion artifacts.
* **Input Injection Boundaries:** Synthetic keyboard events via `send-keys --via send-input` successfully penetrated the DirectX game loop. `winapp ui click` strictly requires UIA semantic slugs, failing on game canvases; coordinate clicks must route through `winapp ui drag <x,y> <x,y>` (press-and-release), `winapp ui touch --at <x,y>`, or adapter-level Win32 `SendInput`.
* **Dual-Mode Adapter Model:** Confirms the necessity of a two-tier architecture: **Mode A (Semantic UIA)** for enterprise/native desktop software, and **Mode B (Visual VLM + Coordinate Injection)** for games, 3D viewports, and non-accessible canvases.
* Detailed results, JSON outputs, and the game screenshot artifact are documented in [winapp-ui-trial-2-gaming.md](winapp-ui-trial-2-gaming.md).

