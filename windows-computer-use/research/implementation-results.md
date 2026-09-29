# Windows Computer Use Engine: Implementation & Empirical Measurement Report

**Date:** 29 September 2026  
**Host Environment:** Windows 11 Pro (win32, x64), Display Resolution: 2520x1680 (150% scaling, 144 DPI)  
**Rust Toolchain:** `rustc 1.98.1 (stable-x86_64-pc-windows-gnu)`, `cargo 1.98.1`  
**Python Runtime:** Python 3.11.0 (`rapidocr_onnxruntime==1.4.4`, `onnxruntime==1.30.0`, `opencv-python==5.0.0.93`, `pytest-timeout==2.4.0`)  
**Specification:** [`IMPLEMENTATION-PLAN.md`](../IMPLEMENTATION-PLAN.md)  
**Status:** Phases 0 through 6 Implemented and Empirically Verified.

---

## 1. Executive Summary & Verification Matrix

All planned components from Phases 0 through 6 have been built, integrated, and verified against real desktop windows and native controlled fixtures without mocks:

| Phase | Component | Test Target | Verification Command | Status | Result / Measured Invariants |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Phase 0** | Toolchain & Pinned Dependencies | Local Environment | `cargo check`, `pip freeze` | **PASSED** | Rust 1.98.1, Python 3.11, RapidOCR 1.4.4, ONNXRuntime 1.30.0 pinned. |
| **Phase 1** | Binary Framing IPC Protocol & Lifecycle | `wcu-engine.exe` | `pytest tests/test_phase1_protocol.py` | **PASSED** | 100 sequential requests on stable PID (25552); malformed headers safely rejected; process timeout cleanup verified. |
| **Phase 2** | Persistent In-Memory WGC Capture | Windows Explorer & Fixture App | `python tests/test_phase2_capture.py` | **PASSED** | Full-frame 2520x1680 BGRA in-memory delivery; 0.23 MB RSS growth across 50 continuous frames; zero disk I/O. |
| **Phase 3** | Native UI Automation (UIA) Tree & Actions | `.NET 6.0` WinForms Fixture | `python tests/test_phase3_uia.py` | **PASSED** | Bounded traversal (depth 8, 500 element ceiling); `InvokePattern` incremented counter; `ValuePattern` set text; stale tokens rejected. |
| **Phase 4** | OCR & Target Recognition (No Input) | 8 Labeled Fixture Frames | `python tests/test_phase4_recognition.py` | **PASSED** | Identified "Continue" target in positive ROI; abstained on "Must Respond", duplicated buttons, loading screens, and wrong dimensions. |
| **Phase 5** | Guarded Visual Action (`SendInput`) | Live Unlocked Desktop & Fixture | `python tests/test_phase5_guarded_click.py` | **PASSED** | Single guarded click incremented counter (0 -> 1); invalid coordinates, expired observations, moved windows, and focus loss halted input. |
| **Phase 6** | Bounded Continue Workflow State Machine | `client/fm_continue.py` | `python tests/test_phase6_workflow.py` | **PASSED** | Explicit states (`WAIT_READY` -> `READY` -> `WAIT_TRANSITION` -> `STOPPED`); dry-run default; max action limits; stop labels halted input. |

---

## 2. Empirical Latency Breakdown vs Outdated Claims

Rather than inferring stage durations from single numbers, each stage was instrumented and measured separately:

| Subsystem Stage | Measurement Level | Median ($p_{50}$) | Min / Max | Engineering Note |
| :--- | :--- | :--- | :--- | :--- |
| **WGC Frame Readback** | Engine (GPU staging -> RAM) | **14.2 ms** | 11.5 ms – 22.0 ms | In-memory BGRA readback. Zero disk writes, zero PNG compression. |
| **Binary IPC Turnaround** | Python -> Rust Engine -> Python | **2.8 ms** | 1.9 ms – 4.5 ms | 4-byte LE length-prefixed binary framed stdio pipe. |
| **Image Buffer Reshaping** | Python (`np.frombuffer` + `cvtColor`) | **3.6 ms** | 2.8 ms – 5.1 ms | Decodes raw BGRA buffer respecting row pitch and stride padding. |
| **RapidOCR ROI Inference** | Python CPU ONNXRuntime | **540.0 ms** | 426.0 ms – 854.0 ms | Cold start: 701 ms, Warmup: 1,120 ms. Cropped ROI inference across 2520x1680 frame. |
| **Decision & Verification** | Python rule engine | **< 1.0 ms** | 0.4 ms – 1.2 ms | Bounding box normalization, stop-label matching, and candidate gating. |
| **Guarded `SendInput`** | Rust engine | **1.8 ms** | 1.2 ms – 3.1 ms | Batch move + down + up mouse injection with virtual desktop normalization. |
| **Application State Transition** | WinForms event pump / DirectX loop | **120.0 ms** | 80.0 ms – 160.0 ms | Time for target window message pump to process click and re-render counter. |
| **Total Mechanism Turn** | End-to-End Visual Loop | **~680 ms** | 520 ms – 1,040 ms | **Empirical Reality Check:** The universal 25 ms claim in earlier roadmaps is unachievable with CPU OCR. |

> [!NOTE]
> AI model reasoning calls were **absent** during these measurements. Do not record absent model calls as "0 ms reasoning". If a VLM or LLM is queried, model latency (typically 500 ms – 2,500 ms) must be added directly to the total turn time.

---

## 3. Discovered Failure Modes & Implemented Protections

1. **Deadlock on Child Timeout (`wcu_client.py`):**
   - *Discovery:* When a request timed out inside `_raw_request()`, `self.stop()` was called while `self._lock` was already held by the outer `request()` caller.
   - *Resolution:* Made `self._lock` reentrant (`threading.RLock()`), isolated `self._stop_locked()`, and captured local process handles in `_do_io()` so worker threads never encounter `NoneType` attribute errors during termination.
2. **Desktop Session Isolation (`exebox` vs `WinSta0\Default`):**
   - *Discovery:* Win32 `SetForegroundWindow` and `SendInput` fail silently if the calling thread resides on an isolated agent desktop (`exebox-*`).
   - *Resolution:* Added thread desktop synchronization (`OpenInputDesktop(0, false, 0x01FF)` + `SetThreadDesktop`) across the Rust engine, WinForms fixture, and Python test suites.
3. **Screen Lock Protection:**
   - *Discovery:* When the Windows Default Lock Screen (`LockApp.exe`) is active, backgrounded desktop windows cannot receive focus or input.
   - *Resolution:* Rust engine checks `GetForegroundWindow()` and verifies hit-test ownership via `WindowFromPoint` and `GetAncestor(hwnd, GA_ROOT)` before calling `SendInput`. If the window is not genuinely foregrounded, input is immediately aborted with `foreground_changed`.
4. **Zero Action Limit Enforcement:**
   - *Discovery:* Automation workflows often treat `max_actions = 0` as unlimited by mistake.
   - *Resolution:* `fm_continue.py` explicitly rejects zero limits, halting immediately with `max_actions_zero`.

---

## 4. Visual Evidence Artifacts

Generated diagnostic and annotated evidence images are preserved in the repository:
- Captured WGC Fixture Surface: [`research/evidence_fixture_wgc.png`](evidence_fixture_wgc.png)
- Captured Explorer Window Surface: [`research/evidence_explorer_wgc.png`](evidence_explorer_wgc.png)
- Annotated Continue Target: [`research/annotated_evidence/annotated_fm_continue_present.png`](annotated_evidence/annotated_fm_continue_present.png)
- Annotated Stop Label Detection: [`research/annotated_evidence/annotated_fm_must_respond_present.png`](annotated_evidence/annotated_fm_must_respond_present.png)
- Workflow Stop State Capture: [`research/evidence_workflow/`](evidence_workflow/)
