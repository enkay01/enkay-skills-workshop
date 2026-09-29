# Windows Computer Use: Research Frontier & Realistic Performance Model

**Document Version:** 2.0.0 (Grounded Technical Specification)  
**Date:** 29 September 2026  
**Context:** Engineering Realities, Capture Pipeline Analysis, and Benchmark Methodology  

---

## 1. Grounded Architecture: The Continuous Capture & Input Daemon

The foundational value in optimizing Windows desktop computer use is **not** attempting a universal, magical "pixels-to-DOM" translation or promising sub-50ms general LLM reasoning. Rather, it is:

> **Keeping capture and input running continuously in a persistent low-level daemon, invoking the cognitive model only when a decision boundary requires it.**

By replacing on-demand child process execution (`winapp ui ...`), disk PNG serialization, and repeated full-frame LLM prompting with a persistent Windows service, we eliminate:
1. The **100–250ms process spawning penalty** of child CLI processes.
2. The **25–50ms disk I/O and PNG compression tax** of on-demand screenshots.
3. Unnecessary round-trips to the LLM when application state transitions follow deterministic rules.

```
+-------------------------------------------------------------------------------+
| PERSISTENT CAPTURE & INPUT DAEMON (Continuous Low-Level Loop)                |
|                                                                               |
| [WGC / DXGI Stream] ---> [In-Memory Frame] ---> [Local Recognition / State]   |
|         |                        |                             |              |
|         | (Steady State)         | (Decision Boundary)         | (Fast Rule)  |
|         v                        v                             v              |
|  [Track HWND/Epoch]       [Invoke Agent/VLM]          [Direct SendInput]      |
|                            (Model Decides)             (Immediate Action)     |
+-------------------------------------------------------------------------------+
```

---

## 2. Decoupling Latency Metrics

A critical engineering mistake is conflating input dispatch latency with end-to-end task completion. A low-latency computer-use loop comprises multiple distinct, measurable phases:

$$T_{\text{total}} = T_{\text{capture}} + T_{\text{transfer}} + T_{\text{recognize}} + T_{\text{reason}} + T_{\text{dispatch}} + T_{\text{settle}}$$

| Latency Component | What It Actually Measures | Realistic Target | Notes & Constraints |
| :--- | :--- | :--- | :--- |
| **$T_{\text{capture}}$ (Frame Capture)** | Time for DXGI/WGC to deliver a presented frame | **2ms – 8ms** | Bound by display refresh interval (60Hz = 16.6ms, 144Hz = 6.9ms). |
| **$T_{\text{transfer}}$ (Memory Readback)** | Copying GPU texture to staging and mapping to CPU | **3ms – 10ms** | Requires PCIe bus transit and GPU/CPU fence synchronization. NOT zero-copy. |
| **$T_{\text{recognize}}$ (Local OCR / Vision)** | Bounding box & text extraction | **15ms – 60ms** | CPU OCR or local ONNX runtime. Does NOT provide semantics, only text locations. |
| **$T_{\text{reason}}$ (Cognitive Turn)** | Evaluating state and deciding the next action | **0ms (Rule) / 200ms – 1,500ms (LLM)** | Rule-based states are instantaneous; LLM turns depend entirely on model size/hardware. |
| **$T_{\text{dispatch}}$ (Input Injection)** | Win32 `SendInput` call execution | **< 0.5ms** | Merely places events in the OS thread input queue. |
| **$T_{\text{settle}}$ (Application Redraw)** | Target app processes input, redraws, presents new frame | **16ms – 250ms** | Governed by the target app's internal message pump and render cycle. |

---

## 3. High-Performance Frame Acquisition: Systems Realities

### 3.1 GPU Capture is NOT Automatically Zero-Copy
Both Windows Graphics Capture (WGC) and DXGI Desktop Duplication output an `ID3D11Texture2D` in GPU VRAM (`D3D11_USAGE_DEFAULT`).
- **CPU OCR Path:** Passing this frame to a CPU-based OCR library (such as `Windows.Media.Ocr` or Tesseract) requires:
  1. Creating a staging texture with `D3D11_USAGE_STAGING` and `D3D11_CPU_ACCESS_READ`.
  2. Calling `ID3D11DeviceContext::CopyResource`.
  3. Calling `Map(D3D11_MAP_READ)` with fence synchronization to ensure the GPU has finished rendering.
  4. Copying or reading bytes across the PCIe bus into host memory.
- **GPU Inference Path:** Keeping the frame in VRAM for a DirectML/ONNX model avoids host memory readback, but still requires:
  1. Pixel format conversion (typically DXGI `B8G8R8A8_UNORM` to RGB or planar FP32/FP16).
  2. Bilinear/bicubic downscaling to model input dimensions (e.g. 640x640 or 1024x1024).
  3. Memory layout restructuring (NCHW vs NHWC).

*Engineering Takeaway:* Eliminating disk writes and PNG compression is straightforward; eliminating all memory copies and format conversions requires careful Direct3D 11 pipeline design.

### 3.2 The Reality of Dirty Rectangles
DXGI Desktop Duplication provides changed regions via `IDXGIOutputDuplication::GetFrameDirtyRects` (not a single struct field). However, practical automation must account for:
1. **Coalescing:** The graphics driver and DWM frequently merge multiple small updates into a single large bounding box that contains mostly static pixels.
2. **Animation Invalidation:** In video playback, web pages with animated banners, or 3D games (such as Football Manager with pitch grass shaders, weather effects, or pulsing UI highlights), **the entire screen or viewport is marked dirty on every frame**, rendering dirty-rect optimizations ineffective.
3. **Pixels $\ne$ Semantics:** A dirty rect only indicates that pixel values changed. It does not indicate whether the change was a button appearing, a progress bar ticking, or an aesthetic hover transition.

### 3.3 Occluded vs. Minimized Windows
- **Occluded Windows:** Windows Graphics Capture (WGC) can reliably capture windows that are visually occluded or behind other application windows on the desktop.
- **Minimized Windows:** WGC **cannot** capture minimized windows. When a window is minimized, Windows suspends DWM composition for that HWND, and the capture stream stops receiving frames until the window is restored (`SW_RESTORE`).

---

## 4. Local Recognition vs. Semantic Reconstruction

### 4.1 What Local OCR Actually Delivers
Windows Media OCR (`Windows.Media.Ocr`) runs locally and hardware-accelerated. However, its exact contract must be respected:
- `OcrWord` provides only `Text` (string) and `BoundingRect` (`Windows.Foundation.Rect`).
- **No confidence scores:** Unlike some third-party engines, native `OcrWord` does not expose per-word confidence metrics.
- **No semantic metadata:** OCR does not indicate whether text is a clickable button, disabled text, a tab header, or an error banner.
- **No tooltips or hover hierarchy:** Hidden, flyout, or hover-only UI elements do not exist in the visual capture.

### 4.2 Target Identifiers and Observational Staleness
Assigning synthetic target IDs (e.g., `#btn_continue` for the text "Continue") enables coordinate decoupling, but introduces **observational staleness risk**:
- A coordinate resolved from frame $N$ may be invalid by frame $N+2$ if the user or background process changed the active screen.
- **Required Safeguards:**
  1. **Frame Timestamps:** Target coordinates must be tagged with capture timestamps.
  2. **HWND Epoch Validation:** Track window activation epochs; reject clicks if focus changed.
  3. **Pre-Click Re-verification:** For high-stakes actions, verify the target region has not drastically altered prior to mouse-down.

---

## 5. Architectural Roadmap: Rust Persistent Service + Python Evaluation

### 5.1 Technology Selection
- **Core Capture & Input Daemon (Rust):**
  - Uses the `windows-rs` crate for first-class, zero-overhead bindings to DXGI, WGC (`Windows::Graphics::Capture`), Direct3D 11, and Win32 `SendInput`.
  - Runs as a persistent background process or Windows service.
  - Manages persistent D3D11 capture sessions, avoiding the ~150ms CLI startup tax.
  - Exposes an ultra-fast local IPC interface (Named Pipe or Shared Memory).
- **Evaluation & Recognition Harness (Python):**
  - Flexible environment for evaluating OCR models, computer vision approaches, and latency distributions.

---

## 6. Concrete Empirical Experiment: Football Manager "Continue" Loop

Rather than relying on theoretical estimates, we establish a concrete, reproducible benchmark:

### Benchmark Workflow:
1. Target active Football Manager 2024 window on `WinSta0\Default`.
2. Capture a fresh frame via persistent capture (record $T_{\text{capture}} + T_{\text{transfer}}$).
3. Run local recognition to locate the "Continue" / "Must Respond" button (record $T_{\text{recognize}}$).
4. Dispatch input click to the resolved button coordinates (record $T_{\text{dispatch}}$).
5. Poll capture stream until the screen visibly transitions to the processing/in-game state (record $T_{\text{settle}}$).
6. Repeat across 20 iterations.

### Metrics to Record:
- **Latency Distribution:** $T_{\text{total}}$ (Median, p95, p99, Min, Max).
- **Stage Breakdown:** Median latency of each sub-component.
- **Reliability:** Recognition failure rate (%) and false-click rate (%).
