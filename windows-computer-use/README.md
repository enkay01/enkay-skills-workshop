# Windows Computer Use Engine & Research Suite

Start with [PRODUCT-ROADMAP.md](PRODUCT-ROADMAP.md) for the end goal, current position, and next milestones toward a desktop tool usable by different vision models. The phase reports below describe implementation work; the roadmap distinguishes that from demonstrated model-driven use.

The specs and phases describe increments of one maintained tool. The Rust engine, Python client, profiles, fixture, tests, and written findings are versioned. Experimental scripts, desktop captures, and raw telemetry from earlier trials live in ignored `scratch/` and remain local to this machine. Reports describe those trials, but linked capture files are only available in the local scratch copy.

**Location:** `enkay-skills-workshop/windows-computer-use`  
**Platform:** Windows 11 Pro (x64)  
**Status:** Phases 0–6 Implemented, Empirically Benchmarked & Verified  

## First model-controlled interaction

The [model-control session](client/model_control.py) now gives a configured vision model one window screenshot, accepts one validated click rectangle, checks a newer observation, sends one guarded click, and asks the model to judge a second screenshot. The controlled fixture demonstration and its limits are recorded in [model-controlled-interaction-results.md](research/model-controlled-interaction-results.md).

Set `WCU_PROXY_TOKEN` to the bearer token for the local EasyCLIProxyAPI endpoint, then build the fixture and run:

```powershell
dotnet build tests/fixture_app/fixture_app.csproj --no-restore
python tests/live_model_demo.py --model gpt-6-luna
python tests/live_model_demo.py --model gpt-6-luna --execute
```

Run those commands from `windows-computer-use/`. The first Python command is a dry run. The second permits exactly one click in the disposable fixture. The general CLI accepts a unique window-title substring, a task, and optional `--execute`:

```powershell
python client/model_control.py --window-title WCU_Native_WinForms_Fixture --task "Click Continue once"
```

The model endpoint and alias can be set with `WCU_MODEL_ENDPOINT` and `WCU_MODEL`. The current adapter requires a local OpenAI-compatible vision endpoint. Desktop screenshots are sent to the selected model; choose its provider according to the content you permit it to see. Raw screenshots are encoded in memory and are not saved by default.

---

## 1. Architecture Overview

This module provides a persistent, low-latency Windows desktop computer use engine combining:
1. **Persistent Rust Engine (`wcu-engine.exe`):**
   - User-session persistent child process over binary-framed stdio pipe (4-byte length prefix).
   - In-memory Windows Graphics Capture (WGC) via Direct3D 11 staging texture readback (**14.2 ms** median frame acquisition, zero disk writes).
   - Native UI Automation (UIA) COM traversal and pattern invocation (`InvokePattern`, `ValuePattern`).
   - Guarded Win32 `SendInput` physical mouse injection with virtual desktop normalization across monitor coordinates.
2. **Python Client & Workflow Controller (`client/`):**
   - Protocol transport with reentrant locking, non-blocking stderr draining, and request deadlines (`wcu_client.py`).
   - High-precision local OCR via RapidOCR (`client/recognition.py`) with ROI cropping and coordinate re-projection.
   - Bounded Continue Workflow state machine (`client/fm_continue.py`) with dry-run default, action limits, and stop-label guards.

```
windows-computer-use/
├── engine/                      <-- Rust persistent engine (windows-rs, D3D11, WGC, UIA, SendInput)
│   ├── Cargo.toml
│   └── src/
│       ├── main.rs              <-- Binary IPC protocol dispatch loop
│       ├── capture.rs           <-- Dedicated MTA WGC capture worker & staging readback
│       ├── uia.rs               <-- Dedicated MTA COM UIA inspection & action worker
│       ├── input.rs             <-- Guarded SendInput dispatch with foreground/hit-test checks
│       ├── win_utils.rs         <-- Window enumeration, bounds, and desktop synchronization
│       └── protocol.rs          <-- Binary message framing & error typing
├── client/                      <-- Python client and visual workflow
│   ├── wcu_client.py            <-- Binary framed IPC client
│   ├── recognition.py          <-- RapidOCR engine & ROI target matcher
│   ├── fm_continue.py           <-- Bounded Continue state machine CLI
│   └── requirements.txt         <-- Pinned dependencies
├── profiles/                    <-- Target profiles with geometry, ROI, and label rules
│   ├── fm24.example.json        <-- Football Manager 2024 profile
│   └── fixture.json             <-- Controlled .NET WinForms test profile
├── tests/                       <-- Verified test suites
│   ├── test_phase1_protocol.py  <-- Protocol framing & timeout cleanup
│   ├── test_phase2_capture.py   <-- WGC direct capture & memory stability
│   ├── test_phase3_uia.py       <-- Native UIA inspection & semantic actions
│   ├── test_phase4_recognition.py <-- RapidOCR target gating & abstention suite
│   ├── test_phase5_guarded_click.py <-- Live guarded SendInput & negative gates
│   └── test_phase6_workflow.py  <-- Bounded state machine transitions
└── research/
    ├── implementation-results.md <-- Full empirical measurements & benchmark data
    └── *.md                     <-- Findings and measured results
```

---

## 2. Setup & Installation

### Prerequisites
- Windows 10/11 x64 (Interactive desktop session on `WinSta0\Default`).
- Rust 1.80+ (`stable-x86_64-pc-windows-gnu` or `msvc`).
- Python 3.10+ (64-bit).
- .NET 6.0 SDK (for compiling the controlled WinForms fixture).

### Building the Rust Engine
```powershell
cd engine
cargo build
```
Compiled binary outputs to: `engine/target/debug/wcu-engine.exe`.

### Python Dependencies
```powershell
pip install -r client/requirements.txt
```

### Compiling the Test Fixture
```powershell
dotnet build tests/fixture_app/fixture_app.csproj
```

---

## 3. Running Workflows & Verification

### Running the Full Test Suite
Each phase has a dedicated verification suite:
```powershell
# Phase 1: Protocol framing & timeout recovery
pytest tests/test_phase1_protocol.py --timeout=30

# Phase 2: In-memory WGC capture & memory stability
python tests/test_phase2_capture.py

# Phase 3: Semantic UIA inspection & actions
python tests/test_phase3_uia.py

# Phase 4: Visual recognition gating & abstentions (8 labeled frames)
python tests/test_phase4_recognition.py

# Phase 5: Live guarded visual click & negative gates
python tests/test_phase5_guarded_click.py

# Phase 6: Bounded Continue workflow state machine
python tests/test_phase6_workflow.py
```

### Running the Bounded Continue Workflow
```powershell
# Default: Dry-run mode (simulates target detection and logs proposed action, zero clicks)
python client/fm_continue.py --profile profiles/fixture.json --max-actions 1

# Live execution: Requires explicit --execute flag
python client/fm_continue.py --profile profiles/fixture.json --execute --max-actions 1
```

---

## 4. Empirical Performance Reality Check

Earlier speculative roadmaps hypothesized a universal "25 ms" turn time. Empirical measurements on Windows 11 hardware at native 2520x1680 display resolution reveal the real breakdown:

- **GPU -> RAM Readback (WGC):** **14.2 ms** (Zero disk writes, eliminates the 1.5s PNG export tax).
- **Binary IPC Stdio Round-Trip:** **2.8 ms** (Eliminates the ~150 ms CLI subprocess tax).
- **RapidOCR ROI Inference (CPU):** **~540 ms** (Cold start: 701 ms, Warmup: 1,120 ms).
- **Guarded Win32 `SendInput`:** **1.8 ms** (Batch move + down + up).
- **Total Mechanism Turn:** **~680 ms** without model reasoning.

For full empirical telemetry tables, error analysis, and validation records, see [`research/implementation-results.md`](research/implementation-results.md).
