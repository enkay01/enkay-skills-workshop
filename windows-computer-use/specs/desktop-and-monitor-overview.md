## Problem Statement

The Windows Computer Use engine and model session currently operate exclusively in a single-window attached mode. Capture requires targeting an explicit window by its window handle (`HWND`) and process identifier (`PID`), and all subsequent observations and actions are confined to that window's bounding rectangle.

As a result, a vision model running desktop tasks is blind to the rest of the desktop:

1. **Popups and Secondary Dialogs**: If an application or the operating system spawns an auxiliary window (such as a file picker, save dialog, modal confirmation, authentication prompt, or system toast) outside the parent window frame, the model cannot see it.
2. **System Shell & Taskbar**: The model cannot see the Windows taskbar, Start menu, system tray, notification center, or desktop shortcuts, making it impossible to switch between applications, launch new software, or verify application launch status.
3. **Multi-Window Workflows**: The model cannot coordinate tasks spanning multiple windows (e.g., cross-referencing information between an editor and a browser, or managing tiled windows).
4. **Desktop Context and Recovery**: If an attached window closes, crashes, or is obscured by an unexpected window, the session halts with an error (`window_gone` or `foreground_changed`), leaving the model without spatial context to understand what happened or recover.
5. **High-Resolution and Multi-Monitor Scaling**: Modern displays and multi-monitor setups produce very large pixel extents (e.g., 4K or dual displays) that exceed model input constraints. Without an explicit contract defining monitor identity, physical desktop coordinates, and image crop/resize transformations, model-space bounding boxes cannot be accurately mapped back to physical screen pixels.

## Solution

Introduce a **desktop and monitor overview** capability across the persistent Rust engine, IPC protocol, Python client, and model session:

1. **Monitor Enumeration & Identity**: The engine enumerates active display monitors, exposing stable monitor identifiers, Windows display device paths (e.g., `\\.\DISPLAY1`), physical bounding rectangles in virtual desktop coordinates, DPI scale factors, and primary monitor designations.
2. **Overview Capture via Windows Graphics Capture**: Add monitor-level capture using `IGraphicsCaptureItemInterop::CreateForMonitor`, allowing the engine to capture a selected monitor or the primary desktop surface without requiring an attached application window.
3. **Overview Observation Contract**: Establish a structured observation schema for overview frames that provides observation IDs, frame IDs, monitor identity, physical bounds, capture dimensions, stride, and timestamp metadata.
4. **Image Transform & Coordinate Inversion Contract**: Define a deterministic coordinate transformation contract at the client/adapter boundary that records scaling, aspect ratio preservation, and crop offsets applied when formatting screenshots for vision models, with mathematically exact inversion to physical virtual desktop coordinates.
5. **Overview-Grounded Hit-Testing & Safe Input**: Support guarded interactions from overview observations. Clicks dispatched in overview mode map model-selected coordinates back to virtual desktop space, perform hit-testing (`WindowFromPoint`) to identify the window and control under the cursor, report the target window's identity, and support window activation/focusing to transition smoothly between overview orientation and window-level precision.

## User Stories

1. As a user, I want the model to see my entire monitor, so that it can locate applications that are not currently focused or attached.
2. As a user, I want the model to see the Windows taskbar, so that it can launch apps, switch between running programs, or check system status.
3. As a user, I want the model to see floating dialogs and file pickers, so that it does not get stuck when an app opens a separate window.
4. As a user, I want the tool to identify which monitor an application is displayed on, so that multi-monitor setups are handled correctly.
5. As a user, I want the model to see system notifications and popups, so that it can respond to alerts or dismiss interruptions.
6. As a user, I want the tool to support multiple monitors, so that secondary screens can be inspected when needed.
7. As a user, I want overview screenshots downscaled or cropped cleanly, so that the vision model does not exceed context limits or distort aspect ratios.
8. As a user, I want coordinates from downscaled screenshots to map accurately to physical desktop pixels, so that clicks land on the intended buttons.
9. As a user, I want a dry-run mode for overview interactions, so that I can preview where the model wants to click before input is dispatched.
10. As a user, I want the tool to verify what window is under the cursor before clicking in overview mode, so that clicks do not accidentally hit unexpected background windows.
11. As a user, I want the tool to report the window handle, title, and process ID hit by an overview click, so that subsequent actions can attach to that window.
12. As a user, I want the tool to refuse stale overview observations, so that transient popups that already disappeared are not clicked blindly.
13. As a user, I want the tool to report display resolution and DPI scaling for each monitor, so that scaling discrepancies are visible.
14. As a user, I want the tool to capture the primary monitor by default when no specific monitor is requested, so that standard single-monitor tasks work out of the box.
15. As a user, I want the tool to let me switch between window-focused capture and monitor overview capture, so that the model can zoom in for detail and zoom out for orientation.
16. As an implementing agent, I want a `list_monitors` protocol operation, so that available screens and their virtual desktop geometries are discovered programmatically.
17. As an implementing agent, I want an `observe_monitor` (or monitor-targeted `observe`) operation in the engine, so that monitor capture is acquired without window attachment.
18. As an implementing agent, I want monitor capture to use Windows Graphics Capture (`CreateForMonitor`), so that frame acquisition remains GPU-accelerated and in-memory with zero disk round-trips.
19. As an implementing agent, I want observation IDs and frame IDs issued for monitor overview frames, so that the existing observation-guarded input contract is preserved.
20. As an implementing agent, I want an explicit transform object containing scale factors and crop offsets, so that coordinate translation between model space and physical space is strictly verified and unit-testable.
21. As an implementing agent, I want the highest test seam to be the Python client communicating with the persistent Rust engine, so that real OS monitor enumeration and capture behavior are tested end-to-end.
22. As an implementing agent, I want synthetic transformation test cases covering multi-monitor layouts with negative virtual desktop coordinates, so that edge cases in coordinate normalization are prevented.
23. As an implementing agent, I want hit-testing via `WindowFromPoint` to identify top-level and root windows at the target coordinate, so that overview click safety is guaranteed.
24. As an implementing agent, I want a clear typed error when monitor capture is unsupported or the display session is locked, so that failures are diagnostic rather than silent.
25. As a future user, I want the model to seamlessly locate an app on the desktop, click to focus it, and transition to window-level inspection, so that cross-application workflows become autonomous.
26. As a future user, I want the overview tool contract to fit into standard Model Context Protocol (MCP) desktop tools, so that any host agent can use it.

## Implementation Decisions

- **Testing Seam**: The highest test seam remains the Python client (`WcuClient`) communicating over the binary-framed IPC stdio pipe to the persistent Rust engine (`wcu-engine.exe`), exercising live OS monitor discovery, capture, and coordinate normalization. A secondary client-side test seam will test coordinate transform inversion across extreme aspect ratios and negative monitor offsets.
- **Monitor Discovery & Enumeration**: The engine implements a `list_monitors` operation using Win32 `EnumDisplayMonitors`, `GetMonitorInfoW`, and monitor DPI awareness APIs. It returns a structured list of monitors including display index, device name, monitor handle, physical bounding box in virtual desktop coordinates `(x, y, w, h)`, DPI, and a boolean indicating whether it is the primary display.
- **Monitor Capture Pipeline**: Implement monitor capture using the Windows Graphics Capture API via `IGraphicsCaptureItemInterop::CreateForMonitor(HMONITOR)`. The MTA capture worker handles both window capture and monitor capture through a unified frame pool and D3D11 staging texture readback pipeline.
- **Unified Observation Contract**: The engine's `observe` operation is extended to support monitor targets (via monitor device name, monitor handle, or index). The returned metadata specifies `target_type: "monitor"` (or `"window"`), physical capture bounds `(x, y, w, h)` in virtual desktop coordinates, observation ID, frame ID, capture dimensions, stride, pixel format, and timing metadata.
- **Client Image Transformation Contract**: High-resolution overview frames must often be scaled or cropped before being sent to vision models. The Python client implements an explicit `CoordinateTransform` model:
  - Records `original_dimensions: (width, height)`, `target_dimensions: (width, height)`, `scale_factors: (scale_x, scale_y)`, `crop_offset: (offset_x, offset_y)`, and `physical_origin: (origin_x, origin_y)`.
  - Implements deterministic `model_to_physical(box_or_point)` and `physical_to_model(box_or_point)` functions.
  - Guarantees exact pixel-center alignment and round-trip consistency across virtual desktop offsets.
- **Guarded Overview Interaction & Hit-Testing**: The engine's `click` operation is expanded to support overview observations:
  - Validates observation ID and checks observation freshness against `max_age_ms`.
  - Translates target coordinates into physical virtual desktop space `(screen_x, screen_y)`.
  - Performs hit-testing using `WindowFromPoint` and `GetAncestor(..., GA_ROOT)` to identify the exact window handle, window title, and process ID at the target point.
  - Rather than rejecting clicks that do not match an attached window, overview clicks verify that the target point falls within the interactive desktop, dispatches the click safely via virtual desktop normalized coordinates, and returns the identity of the window that received the click.
- **Window Focusing & Transition**: The client layer provides an explicit `focus_window` operation (or uses an overview click on a window/taskbar) to bring a discovered window to the foreground and transition to window-level attached capture.

## Testing Decisions

- **Good Test Criteria**: Tests must verify observable external behavior and contracts: monitor enumeration reports valid physical bounds matching OS metrics, monitor capture returns valid BGRA byte buffers with the expected dimensions, coordinate transforms invert bidirectionally without pixel drift, and overview clicks correctly hit-test live desktop windows. Tests must not assert internal helper function details or timing-sensitive network thresholds.
- **Modules Tested**:
  - Rust engine: `win_utils` (monitor enumeration), `capture` (WGC monitor capture), `protocol` (monitor request/response handling), and `input` (overview hit-testing and input dispatch).
  - Python client: `wcu_client.py` (`list_monitors`, monitor capture), `transforms.py` (coordinate inversion and scaling), and `model_control.py` (overview session handling).
- **Prior Art**:
  - `tests/test_phase1_protocol.py` for IPC framing and error recovery.
  - `tests/test_phase2_capture.py` for in-memory WGC acquisition and resource cleanup.
  - `tests/test_phase5_guarded_click.py` for virtual desktop coordinate normalization and hit-testing safety.
  - `tests/test_model_control.py` for model decision parsing and observation validation.

## Out of Scope

- Capturing protected system desktops (e.g., UAC secure desktop `WinSta0\Winlogon` or locked lock screen).
- Real-time video streaming or high-framerate desktop recording.
- Full-screen OCR processing across 4K displays (overview is strictly for visual grounding and window location; text recognition remains scoped to regions of interest).
- Emulating virtual displays or installing kernel display drivers.
- Non-Windows operating systems (macOS / Linux).

## Further Notes

This specification implements Milestone 4 ("Add desktop/monitor overview capture so the model can locate apps, taskbar, and popups outside the selected window") defined in [PRODUCT-ROADMAP.md](file:///D:/stroo/Documents/GitHub/enkay-skills-workshop/windows-computer-use/PRODUCT-ROADMAP.md). It directly resolves the visibility limitations tracked in [ISSUE-BOARD.md](file:///D:/stroo/Documents/GitHub/enkay-skills-workshop/windows-computer-use/ISSUE-BOARD.md) regarding popup dialogs and focus switching, laying the foundation for cross-application workflows and autonomous task navigation.
