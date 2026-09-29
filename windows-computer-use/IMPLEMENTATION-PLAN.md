# Windows desktop computer use implementation plan

Status: ready for implementation. Written 29 September 2026.

## Assignment and completion boundary

Build a persistent Rust process for Windows desktop capture, window inspection, and input, plus a Python client for OCR, workflow decisions, and measurement. Computer use means interacting with the full Windows desktop and native applications, including custom game interfaces. Browser-only automation does not satisfy this assignment.

Deliver two demonstrated workflows: a semantic UI Automation action against a native test application, and one visually recognized Football Manager Continue action whose resulting state is verified. Add bounded repetition only after the single-action workflow passes. A fast capture benchmark alone is not completion.

Follow phases in order. Each phase has a completion gate. If this machine cannot satisfy a gate, finish independent work and report the exact failed gate, API error, and remaining requirement. Preserve working source and report partial completion honestly. Do not replace a failed desktop demonstration with a mock and call it verified.

This plan resolves choices left open in `RESEARCH-FRONTIER.md` v2.0.0. Its initial implementation choices take precedence over that document's alternatives and estimated timing ranges.

## Read first and preserve existing work

Read repository instructions, `README.md`, `RESEARCH-FRONTIER.md`, `ISSUE-BOARD.md`, `research/fm_fast_loop_results.md`, and the existing Python adapter. Check Git status before editing. Existing research and the macOS submodule are reference material. Preserve unrelated work and raw telemetry.

The existing game report records eight startup frames, OCR durations around 45–446 ms, and no recognized targets. Those frames are not evidence of an OCR false-negative unless a target was actually present. CLI screenshot duration includes several operations; it does not isolate process startup, GPU readback, encoding, or disk costs. Its claims about game internals and synthetic-input rejection remain hypotheses without separate evidence. Do not use them as implementation facts.

The old adapter is a comparison baseline, not the foundation for the new process. Its subprocess path waits before draining output, merges stdout/stderr, ignores the wait result, constructs a shell command, and creates an environment dictionary without passing it. The new client must drain pipes while the child runs and handle deadlines explicitly. Repair the old baseline only if you need to execute it for a valid comparison; document those repairs separately.

## Fixed decisions for the first version

| Concern | Decision |
| --- | --- |
| Host | Windows 11 x64, interactive logged-in user, ordinary privileges |
| Rust process | One long-lived child launched by the Python client in that user's desktop session; no Windows service registration |
| Windows bindings | Microsoft's `windows` crate; use supported stable Rust/MSVC and commit `Cargo.lock` |
| Capture | WGC for one explicitly selected HWND; DXGI is a later fallback only if an observed WGC limitation requires it |
| Frame processing | Reusable D3D11 resources; bounded latest-frame storage; CPU BGRA readback for the first version |
| IPC | Inherited binary stdin/stdout pipes with length-prefixed messages; logs on stderr |
| OCR | Python RapidOCR with ONNX Runtime CPU execution, initialized once; pin working package versions and model files |
| Native app actions | Direct UI Automation in Rust, initially inspect, InvokePattern, and ValuePattern |
| Visual input | Win32 SendInput after observation, identity, geometry, and foreground checks |
| Model integration | Emit `needs_decision` plus evidence; let the calling agent decide. No model call required for the Continue experiment |

Ordinary Windows services run outside the user's interactive session. Specifying `WinSta0\\Default` does not grant access to another session or bypass desktop permissions. Run the executable in the intended interactive session and report when this prerequisite is missing.

Windows.Media.Ocr can remain an optional comparison backend. Microsoft documents package identity as a desktop requirement. A successful unconfigured call on this machine is not a supported deployment contract. The first implementation uses RapidOCR to avoid making packaging a prerequisite. Record its actual speed rather than promising it will match the native engine.

Defer shared-memory IPC, DirectML, full-screen monitor capture, GPU OCR, automatic launcher discovery, installers, and a universal UI reconstruction model. Add one only after a measured limitation identifies the need. Raw pointers are process-local and must never be sent as if another process can dereference them.

## Files to create

Keep this component self-contained under `windows-computer-use/`:

```text
engine/
  Cargo.toml
  Cargo.lock
  src/main.rs          # arguments, protocol loop, process lifecycle
  src/protocol.rs      # message types, framing, limits, errors
  src/windows.rs      # window identity, bounds, DPI, desktop checks
  src/capture.rs      # WGC session and D3D11 readback ownership
  src/uia.rs          # bounded inspection and semantic actions
  src/input.rs        # revalidation and SendInput
  src/state.rs        # observation tokens and invalidation
client/
  requirements.txt    # exact tested dependency versions
  wcu_client.py       # child lifecycle, binary IPC, deadlines
  recognition.py      # OCR engine and coordinate transforms
  fm_continue.py      # explicit workflow state machine
  benchmark.py        # measurements and report generation
profiles/fm24.example.json
tests/                # protocol/state tests and controlled desktop fixture
research/implementation-results.md
```

Use a small Python tkinter fixture with a button, editable field, visible action counter, and explicit Continue/Processing/Must Respond states. It provides a controlled desktop target without depending on an unsaved user document. Inspect its actual UIA support first; if its controls do not expose the required patterns, use a small .NET WinForms fixture instead and record the reason.

Keep generated frames, machine-specific profiles, model caches, virtual environments, and build outputs ignored. Commit only intentionally selected evidence images and anonymized telemetry. Use workspace-relative paths resolved from script locations, not hard-coded drive letters.

## Phase 0: environment and dependency checkpoint

1. Record OS build, session type, interactive desktop accessibility, CPU/GPU, display layout and DPI, Rust/MSVC/SDK versions, Python version, and available OCR languages/backends.
2. Read API bindings for the selected `windows` crate version before writing calls. Feature names and generated signatures vary by version. Enable only the namespaces used by the implementation.
3. Create the Rust package and Python environment. Confirm the process can initialize WinRT/COM, create a D3D11 device, and report whether WGC is supported. Verify OCR on one known text image and record engine/model versions and model checksums.
4. Make model acquisition an explicit setup step using the OCR project's documented sources. Store the model paths in configuration so benchmark runs do not download assets or select different default models.

Gate: a `doctor` command emits structured capability results; build and OCR setup succeed. Missing interactive access is an explicit capability failure, not a reason to automate elevation or unlock the workstation.

## Phase 1: protocol, window identity, and process lifecycle

Implement the transport before capture. Python starts one Rust process and keeps it alive across requests. Support one request in flight. Use dedicated Python reader threads for stdout and stderr; a deadline expires in the caller even if a reader is blocked. On deadline, terminate the owned child, close its pipes, join readers, and start a fresh child only for a subsequent request. Never replay a timed-out mutation automatically.

Wire format for requests and responses:

```text
u32 little-endian JSON header byte length
UTF-8 JSON header
payload_len raw bytes, as declared in the header
```

Read exact byte counts in loops. A partial read is not a complete message. Reject headers over 64 KiB, payloads over 128 MiB, integer overflow, unsupported protocol versions, and truncated messages. EOF ends the session cleanly. Frames use binary payloads, not base64 or PNG. Flush each response. Start with no unsolicited stdout events.

Request example:

```json
{"v":1,"id":7,"op":"observe","args":{"after_frame_id":12,"timeout_ms":2000},"payload_len":0}
```

Response shape:

```json
{"v":1,"id":7,"ok":true,"result":{},"payload_len":0}
```

Errors use `ok:false` and `error:{code,message,details}`. Keep `details` structured. Initial codes: `invalid_request`, `unsupported`, `no_interactive_desktop`, `window_gone`, `foreground_changed`, `geometry_changed`, `no_fresh_frame`, `stale_observation`, `ambiguous_target`, `uia_timeout`, `capture_failed`, `input_failed`, `timeout`, and `needs_decision`.

Implement `doctor`, `list_windows`, `attach`, `detach`, and `shutdown`. Identify a target using HWND, PID, and process creation time. Encode HWND and wide timestamp/counter values as decimal strings in JSON. HWND alone can be reused after a window closes. Require an exact HWND when selection matches multiple windows.

Gate: 100 sequential `doctor` requests use the same child PID; malformed/truncated messages terminate or return errors predictably; timeout leaves no owned child running; desktop fixture enumeration and exact attachment work.

## Phase 2: persistent capture and owned frame data

Use a dedicated MTA capture worker to own the D3D11 immediate context, WGC session, staging resources, and cleanup. Initialize COM on every thread that uses it. Never call the immediate context concurrently.

Implementation sequence:

1. Set per-monitor-v2 DPI awareness before creating relevant Windows resources. Verify the effective mode.
2. Create a D3D11 device with BGRA support, wrap it for WinRT, and create a GraphicsCaptureItem for the attached HWND through the documented interop API.
3. Use `Direct3D11CaptureFramePool::CreateFreeThreaded`, initially two buffers. Its callback signals the worker; keep OCR, IPC writes, and expensive processing out of the callback.
4. Drain available capture frames and use the newest one. Keep the queue bounded. At most one readback is active and at most two owned CPU snapshots are retained. Dropping superseded frames is expected and counted.
5. Copy the selected surface into a matching staging texture, map it, and copy each row using `RowPitch` into a tightly packed owned BGRA byte buffer. Reuse allocations when dimensions match. Unmap and release the capture frame promptly. Never retain a mapped pointer after unmapping or reuse a buffer being serialized.
6. On resize, close outstanding frames, recreate the frame pool and readback resources, and invalidate observations. On window closure/device failure, stop capture and return a typed error. On minimized or suspended capture, reject stale input and report the state.

Keep frame acquisition active, but cap expensive CPU readback at an initial 10 samples/second. Make this configurable for benchmarking. Consume the latest frame instead of processing a backlog. A request for a newer frame must wait only up to its deadline. If the desktop supplies no new frame, return `no_fresh_frame`; never relabel an old frame with a new capture timestamp. Record static-window behavior as part of the first capture trial.

`observe` returns full-frame BGRA initially, plus:

```text
observation_id, frame_id, window identity, geometry_epoch,
foreground_epoch, width, height, stride_bytes, pixel_format,
content_timestamp, received_timestamp, published_timestamp,
capture_bounds_physical_px, monitor/DPI context, payload_len
```

Use one documented monotonic clock domain for Rust timestamps. WGC SystemRelativeTime and local callback timing describe different events; establish their clock relationship before subtracting them. Otherwise report them separately. Python measures its own end-to-end request time without subtracting unrelated Rust clock values.

WGC frame pixels must be mapped to physical desktop pixels explicitly. Verify whether the captured extent matches the extended frame bounds from DWM for this target. If dimensions differ or the transform is not validated, disable coordinate input and return `geometry_changed` or `unsupported`. Avoid assuming GetWindowRect includes exactly the captured content. Preserve crop offsets and scaling in any later ROI support.

Gate: inspect one evidence image for a controlled fixture, verify row layout and colors, run continuous capture for 60 seconds without unbounded memory growth, and handle move/resize/minimize/restore/window-close. No PNG or disk round-trip in the measured frame delivery path. Diagnostic PNG export is allowed outside that path.

## Phase 3: native UI Automation

Add `inspect` and `uia_action` on a separate COM worker. Scope traversal to the attached window, with default depth 8, maximum 500 returned elements, and a request deadline. Return a truncation flag. A provider can hang inside COM; the Python child watchdog is the outer recovery boundary. Do not claim a traversal loop deadline can interrupt a blocked COM call.

Return role/control type, name, AutomationId, bounds, enabled/offscreen state, supported patterns, and an observation-local element token. Resolve tokens only within their attached window and observation. AutomationId is not globally unique. Reject ambiguous selection and stale elements. Use InvokePattern for a button and ValuePattern for an editable field when supported; return `unsupported` when the requested pattern is absent.

Gate: locate the fixture button and field, invoke once, set a value, and verify counter/text through a fresh inspection. An empty game accessibility tree returns valid empty data, allowing explicit visual fallback.

## Phase 4: OCR and target recognition without input

Python reshapes the received buffer using its declared width/height/stride and converts BGRA to the OCR engine's expected format. Initialize RapidOCR once. Warm up before measuring, and preserve cold-start timings separately. Record any internal resize and return polygons/boxes in original frame coordinates. Confidence from this backend is an OCR score, not probability of clickability.

Create a machine-specific FM profile using a verified gameplay/menu screenshot. Store process name, expected window title pattern, allowed capture dimensions or validated scaling rule, normalized button ROI, exact accepted label `Continue`, stop labels including `Must Respond`, and evidence for the expected post-click transition. The profile file must name its reference frame. Do not invent the ROI from the old startup screenshots.

Recognize exact normalized labels inside the configured ROI. Treat missing/duplicate candidates, disabled-looking controls, unrecognized screen states, and stop labels as non-actionable. Generic keywords such as `game`, `start`, `load`, or `exit` must not become automatic click targets. The FM workflow is English-only initially and reports unsupported language rather than guessing.

Maintain labeled fixture images: Continue present, Must Respond present, no target, duplicated Continue labels, loading screen, changed geometry, and obscuring dialog. Include real FM frames only where available and manually labeled. Distinguish correct abstention from a false-negative on a visible allowed target.

Gate: recognition identifies the allowed target in labeled positive cases and abstains on every negative case. Save boxes and annotated evidence. No desktop clicks occur in this phase.

## Phase 5: one guarded visual action

Add `click` accepting `{observation_id, target_bbox_frame_px}`. Rust validates that the observation belongs to the current attached process/window, its geometry and foreground epochs still match, and the bounding box is in range. Reject arbitrary coordinates detached from an observation.

Before clicking, Python obtains another observation and reruns recognition on the target region. Require the same allowed label, compatible bounds, no stop state, and unchanged window identity. Send the newer observation to Rust. Initial maximum observation age is 500 ms, configurable and logged. If recognition cannot finish inside it, improve cropping or explicitly revise the measured policy; do not silently bypass freshness.

Check foreground, geometry, target visibility/hit-test ownership at the intended point, and interactive desktop immediately before SendInput. An occluded window may be capturable but is not automatically safe to click. Initially require the user to foreground the target; report failure instead of using focus-unlock tricks. Poll the foreground while attached and increment its epoch on changes; also check it at dispatch. These checks reduce races, not eliminate them.

Map to the entire physical virtual desktop, including negative monitor origins. Use SM_XVIRTUALSCREEN/SM_YVIRTUALSCREEN/SM_CXVIRTUALSCREEN/SM_CYVIRTUALSCREEN with MOUSEEVENTF_ABSOLUTE and MOUSEEVENTF_VIRTUALDESK. Validate pixel-center normalization across monitor edges in the fixture. Submit move/down/up as one batch and check SendInput's returned count. Best-effort release a possibly pressed button on partial failure; return `input_failed` and stop the workflow. Never retry a possibly completed click automatically.

Gate: exactly one fixture click increments its counter once. Focus loss, moved/resized window, expired observation, occluding dialog, wrong PID, and out-of-bounds coordinates each prevent dispatch. Repeat on a verified FM Continue state once, then verify a fresh expected state. API success alone is not task success.

## Phase 6: bounded Continue workflow

Implement these states explicitly:

| State | Transition |
| --- | --- |
| WAIT_READY | Capture/recognize. Continue becomes READY; Must Respond or unknown decision state becomes NEEDS_DECISION; loading remains waiting within its timeout. |
| READY | Re-observe and revalidate. A mismatch returns to waiting or stops; a match permits one click. |
| WAIT_TRANSITION | Require a newer frame and the profile's expected transition evidence. Never click again while waiting. |
| COOLDOWN | Require a stable recognized ready state before the next permitted action. |
| NEEDS_DECISION | Emit evidence and stop automatic input. |
| STOPPED | Reached action limit, timeout, cancellation, or failure. |

Button disappearance alone and arbitrary pixel changes are insufficient success signals. On the fixture, use the changed counter/Processing state. On FM, configure an observed processing indicator or game-date/state transition. If no reliable FM postcondition has been identified, report that gate as blocked and retain single-action experimental status.

Default to dry-run. Live execution requires `--execute`, starts with `--max-actions 1`, and uses a disposable/manual test save. Include Ctrl+C cancellation, a finite wall-clock deadline, and logged stop reasons. `Must Respond` always produces `needs_decision`; it never follows the Continue click rule. A zero limit must not mean unlimited.

Gate: bounded fixture repetition works without duplicate actions; stop states and cancellation cease input. Demonstrate the FM single-action gate before enabling a bounded multi-action trial. Do not edit game assets, skip cinematics through file changes, or overwrite user saves to reach the benchmark screen.

## Phase 7: measurements, documentation, and handoff

Record separate measurements for frame timestamp age, receive-to-readback completion, IPC request/response, image conversion, OCR, recognition decision, input dispatch, verified application transition, and end-to-end workflow. Persistent stages overlap, so measure end-to-end time directly rather than summing overlapping intervals. Record model calls as absent, not 0 ms reasoning performance claims.

Collect 20 labeled single-action attempts for the initial live assessment when the environment allows it. Report count, median, min/max, failures, abstentions, and reasons. Use at least 100 successful observations for an exploratory p95 and at least 1,000 for an exploratory p99; always include sample counts and the quantile method. More samples do not establish safety by themselves. Keep warm-up, failures, and successful-action timings separately visible.

Compare persistent delivery with the repaired CLI baseline at the same resolution, target state, and capture scope. Measure rather than infer each stage. Do not attribute all CLI time to PNG or process launch. Performance goals are hypotheses; correctness gates must pass even if speed gains are modest.

Use focused tests for binary framing under partial reads, timeout cleanup, coordinate transforms with negative origins and DPI changes, observation invalidation, and state-machine duplicate suppression. Use the fixture for actual Windows API behavior. Run Rust formatting/build/tests and Python tests appropriate to the new code. Record exact commands and outcomes in `research/implementation-results.md`.

Update README with setup, dependency/model preparation, dry-run and one-action commands, evidence links, and current limitations. Replace its outdated universal 25 ms claim with measured results or an explicitly unmeasured target. Update ISSUE-BOARD statuses only when evidence supports resolution. Leave historical telemetry unchanged and add dated corrections for unsupported interpretations.

Final handoff must list implemented phases, exact commands, dependency versions, demonstrated workflows, latency and recognition results, failed/unrun gates, and the next specific action. Do not call the component complete while either required desktop workflow remains unverified.

## Primary references for implementation

- [Microsoft Rust Windows bindings](https://github.com/microsoft/windows-rs). Consult the selected release's generated API signatures.
- [WGC Win32 capture sample](https://github.com/microsoft/Windows.UI.Composition-Win32-Samples/tree/master/cpp/ScreenCaptureforHWND).
- [CreateFreeThreaded](https://learn.microsoft.com/en-us/uwp/api/windows.graphics.capture.direct3d11captureframepool.createfreethreaded). Callback threading and frame-pool construction.
- [Interactive services](https://learn.microsoft.com/en-us/windows/win32/services/interactive-services). Session boundaries.
- [UI Automation client guide](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-clientportal).
- [SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput) and [MOUSEINPUT](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-mouseinput). Injection limits and coordinate flags.
- [Windows.Media.Ocr](https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr). Package identity and supported contract.
- [RapidOCR](https://github.com/RapidAI/RapidOCR). Installation, configured model paths, output geometry, and license information.

## Copyable assignment for the implementing agent

Implement `windows-computer-use/IMPLEMENTATION-PLAN.md` in order. Begin with Phase 0 and proceed through each completion gate. Use the specified Rust user-session child, WGC capture, binary pipe protocol, UIA actions, and Python OCR workflow. Preserve existing research and unrelated changes. The first live game action is one verified Continue click; Must Respond stops automation. Deliver working source, setup instructions, focused checks, and measured evidence. Where interactive access or the game state prevents verification, identify the exact incomplete gate and finish all independent work without inventing results.
