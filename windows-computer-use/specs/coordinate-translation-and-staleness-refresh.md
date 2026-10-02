# Specification: Robust Coordinate Translation, Unified Staleness Limits, and Automatic Observation Refresh

**Tracking Issue:** [Issue #8](https://github.com/enkay01/enkay-skills-workshop/issues/8)  
**Status:** Implemented & Shipped (`main` @ commit [`a3f6ff9`](https://github.com/enkay01/enkay-skills-workshop/commit/a3f6ff9))  
**Date:** October 2026  

---

## 1. Problem Statement & Context

During end-to-end desktop automation of complex Windows applications (specifically a full onboarding and career-creation run on Football Manager 26 running on Windows 11 at 2520×1680 physical resolution, 144 DPI / 150% display scaling), three operational friction points caused execution stalls, coordinate thrashing, and unnecessary multi-turn round trips:

1. **Coordinate Space Fidelity & DPI Translation (Issue B)**:
   Vision models frequently reason in logical points, downsampled image bounds, or normalized coordinates (e.g., `0.0..1.0` or `0..1000` grids). On macOS, translation between logical points (Retina scale factor) and physical pixels is standard; Windows WCU previously required the model to guess raw physical screen pixels, causing click misses when applications rendered under non-standard DPI scaling.

2. **Harmonized Action Staleness Limits (Issue D)**:
   Pointer actions (`click`, `hover`, `drag`, `scroll`) in the CLI session server defaulted to 30,000 ms (`POINTER_MAX_AGE_MS`), but keyboard actions (`press`, `type`) defaulted to the engine's 500 ms limit. Because standard shell/process startup overhead in CLI agent loops is ~1,000–1,200 ms, single-turn keyboard commands failed immediately with `stale_observation` unless `--max-age-ms` was explicitly passed.

3. **Stale Observation Fast-Path & Auto-Refresh (Issue E)**:
   When an action was refused due to staleness during UI animations or long database/scene loading, the engine already captured fresh evidence and returned a new `observation_id` in the refusal payload. Previously, this forced an entire round-trip failure cycle where the agent had to parse the error, extract the new ID, and re-invoke `wcu act`.

---

## 2. Solution Architecture

### 2.1 Standardized Observation Geometry Metadata
Every observation produced by `wcu observe` and `wcu switch` carries a standardized `geometry` block in its result envelope:

```json
"geometry": {
  "dpi": 144,
  "scale_factor": 1.5,
  "physical_bounds": { "x": 0, "y": 0, "w": 2520, "h": 1680 },
  "logical_bounds": { "x": 0, "y": 0, "w": 1680, "h": 1120 },
  "image_dimensions": { "w": 2520, "h": 1680 }
}
```

- `scale_factor`: Computed directly from system/monitor DPI (`dpi / 96.0`).
- `logical_bounds`: Derived from physical bounds divided by `scale_factor`.
- `image_dimensions`: Actual pixel dimensions of the captured observation image on disk.

### 2.2 First-Class Coordinate Translation (`coordinates.py`)
A dedicated module (`wcu.coordinates`) handles bidirectional coordinate translation:
- **`physical` (default)**: Raw physical pixel coordinates matching the captured PNG image.
- **`logical`**: Logical/DIP coordinates. Multiplied by `scale_factor` to map to physical pixels.
- **`normalized`**:
  - Unit floats (`0.0 <= x <= 1.0`, `0.0 <= y <= 1.0`): Scaled by `(w, h)` of the observation image.
  - Grid integers (`0 <= x <= 1000`, `0 <= y <= 1000`): Scaled by `(w / 1000, h / 1000)`.
- Applied across all pointer actions (`click`, `hover`, `drag`, `scroll`) and region-bounded grounding operations (`ocr --region`, `find --region`).
- The result envelope reports `resolved_physical_point` or `resolved_physical_bbox` to ensure complete execution transparency.

### 2.3 Unified Observation Staleness Limits
- In `wcu.session_server._default_max_age(action)`: All user-interactive actions (`click`, `hover`, `drag`, `scroll`, `type`, `press`) share the unified 30,000 ms (`POINTER_MAX_AGE_MS`) default.
- In `wcu.client.wcu_client`: `type_text()` and `press_key()` default `max_age_ms` harmonized to 30,000 ms.
- Safe execution is guaranteed by validating window identity, process create time, and foreground epoch before input injection.

### 2.4 Auto-Refresh on Staleness (`--auto-refresh` / `--retry-if-stale`)
- When `--auto-refresh` is passed to `wcu act`:
  - If pre-flight refusal is caused **only** by `stale_observation`:
  - Up to 3 automatic refresh retries are performed with 50 ms backoff.
  - The server captures a fresh observation frame, validates that the window identity and geometry epoch remain identical, re-translates coordinates against the new frame, and re-dispatches the action.
  - Returns `status: "ok"`, `auto_refreshed: true`, with `observation_id` reflecting the fresh frame.
  - **Strict Safety Boundaries**: If the window was moved, resized, minimized, or lost foreground ownership, the retry loop immediately aborts and refuses execution.

---

## 3. CLI Contract & Usage

### 3.1 Pointer Actions with Coordinate Space
```bash
# Physical pixels (default)
wcu act click --observation-id 12 --point 1253 915

# Logical points (e.g. 150% scaling)
wcu act click --observation-id 12 --coord-space logical --point 835 610

# Normalized unit floats (center of target)
wcu act click --observation-id 12 --coord-space normalized --point 0.5 0.5

# Normalized 1000-grid coordinates
wcu act click --observation-id 12 --coord-space normalized --point 500 500
```

### 3.2 Automatic Observation Refresh
```bash
# Automatically refresh observation and retry if stale
wcu act click --observation-id 12 --point 500 300 --auto-refresh
```

### 3.3 Grounding Operations with Coordinate Space
```bash
# OCR over a logical-coordinate subregion
wcu ocr --obs 12 --coord-space logical --region 100 100 400 200 --match "Confirm"

# Find template matching in normalized bounds
wcu find --obs 12 --coord-space normalized --region 0.1 0.1 0.8 0.8 --template icon.png
```

---

## 4. Verification & Testing

The implementation is verified by 5 test suites (54 automated tests):
1. **`test_coordinate_translation.py`**:
   - Physical identity translation.
   - Logical DIP point and bounding box translation across scale factors (1.0x, 1.25x, 1.5x, 2.0x).
   - Normalized unit float and 0–1000 integer translation.
   - Out-of-bounds point and bbox rejection.
   - `build_geometry_meta()` schema validation.
2. **`test_auto_refresh.py`**:
   - Pre-flight staleness refusal triggering automatic refresh.
   - Coordinate re-translation on fresh observation frames.
   - Abort on window identity mismatch, foreground epoch shift, or geometry changes.
   - Max retry limit exhaustion.
3. **`test_max_age_defaults.py`**:
   - Unified 30,000 ms defaults for `click`, `hover`, `drag`, `scroll`, `type`, and `press`.
   - Explicit `--max-age-ms` override preservation.
4. **`test_cli_session.py`**:
   - Full CLI argument parsing and session server dispatch integration.
5. **`test_installer.py`**:
   - NTFS Directory Junction creation, idempotency, and skill environment synchronization.
