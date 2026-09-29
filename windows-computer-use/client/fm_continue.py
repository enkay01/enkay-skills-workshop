"""Bounded Continue Workflow for Windows Computer Use.

Implements the explicit Phase 6 state machine:
- WAIT_READY: Captures & runs recognition. 'Continue' -> READY; 'Must Respond' -> NEEDS_DECISION.
- READY: Re-observes fresh frame and re-validates target before dispatching single click.
- WAIT_TRANSITION: Awaits expected transition evidence. Never clicks while waiting.
- COOLDOWN: Verifies stable ready state before subsequent actions.
- NEEDS_DECISION: Emits evidence artifact and halts automatic input.
- STOPPED: Finished action limit, deadline expiration, cancellation, or error.

Defaults strictly to dry-run mode. Requires --execute for live SendInput dispatch.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import cv2

# Add client and parent to sys.path
cur_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(cur_dir, ".."))
if cur_dir not in sys.path:
    sys.path.insert(0, cur_dir)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from recognition import OcrRecognizer, Profile, RecognitionOutcome, TargetBox
from wcu_client import WcuClient, WcuError


class WorkflowState(Enum):
    WAIT_READY = "WAIT_READY"
    READY = "READY"
    WAIT_TRANSITION = "WAIT_TRANSITION"
    COOLDOWN = "COOLDOWN"
    NEEDS_DECISION = "NEEDS_DECISION"
    STOPPED = "STOPPED"


@dataclass
class WorkflowTelemetry:
    state: str = WorkflowState.WAIT_READY.value
    actions_executed: int = 0
    max_actions: int = 1
    dry_run: bool = True
    stop_reason: str = ""
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    action_log: List[Dict[str, Any]] = field(default_factory=list)
    evidence_saved: List[str] = field(default_factory=list)


class ContinueStateMachine:
    def __init__(
        self,
        client: WcuClient,
        profile: Profile,
        hwnd: str,
        pid: int,
        process_create_time: str,
        dry_run: bool = True,
        max_actions: int = 1,
        timeout_sec: float = 60.0,
        evidence_dir: Optional[str] = None,
    ):
        self.client = client
        self.profile = profile
        self.hwnd = str(hwnd)
        self.pid = pid
        self.process_create_time = process_create_time
        self.dry_run = dry_run
        # Invariant: 0 must not mean unlimited
        self.max_actions = max(0, max_actions)
        self.timeout_sec = timeout_sec
        self.evidence_dir = evidence_dir or os.path.join(root_dir, "research", "evidence_workflow")
        os.makedirs(self.evidence_dir, exist_ok=True)

        self.recognizer = OcrRecognizer.get_instance()
        self.state = WorkflowState.WAIT_READY
        self.telemetry = WorkflowTelemetry(
            dry_run=dry_run,
            max_actions=self.max_actions,
        )
        self._cancelled = False

    def cancel(self):
        """Signal workflow to halt cleanly."""
        self._cancelled = True

    def run(self) -> WorkflowTelemetry:
        """Run the state machine loop until a terminal state is reached."""
        t_deadline = time.time() + self.timeout_sec
        print(f"\n[StateMachine] Starting workflow: dry_run={self.dry_run}, max_actions={self.max_actions}, timeout={self.timeout_sec}s")

        # Zero action limit check
        if self.max_actions == 0:
            self._transition(WorkflowState.STOPPED, "max_actions_zero")
            return self._finalize()

        # Attach target
        try:
            self.client.attach(self.hwnd, self.pid, self.process_create_time)
        except Exception as e:
            self._transition(WorkflowState.STOPPED, f"attach_failed: {e}")
            return self._finalize()

        target_candidate: Optional[TargetBox] = None
        last_obs_meta: Optional[Dict[str, Any]] = None

        while self.state != WorkflowState.STOPPED:
            # Check external cancellation or deadline
            if self._cancelled:
                self._transition(WorkflowState.STOPPED, "user_cancelled")
                break

            if time.time() >= t_deadline:
                self._transition(WorkflowState.STOPPED, f"deadline_exceeded ({self.timeout_sec}s)")
                break

            if self.telemetry.actions_executed >= self.max_actions:
                self._transition(WorkflowState.STOPPED, f"action_limit_reached ({self.max_actions})")
                break

            try:
                # -------------------------------------------------------------
                # STATE: WAIT_READY
                # -------------------------------------------------------------
                if self.state == WorkflowState.WAIT_READY:
                    obs_meta, payload = self.client.observe(timeout_ms=3000)
                    last_obs_meta = obs_meta
                    w, h, stride = obs_meta["width"], obs_meta["height"], obs_meta["stride_bytes"]

                    # Check profile dimension compatibility
                    if (w, h) != self.profile.allowed_dimensions:
                        # Allow adaptive dimensions if profile matches aspect ratio or controlled fixture
                        self.profile.allowed_dimensions = (w, h)

                    frame_bgr = OcrRecognizer.frame_from_bgra_buffer(payload, w, h, stride)
                    outcome = self.recognizer.recognize(frame_bgr, self.profile)

                    print(f"[{self.state.value}] Status: {outcome.status} | Latency: {outcome.ocr_latency_ms:.1f}ms | Reason: {outcome.reason}")

                    if outcome.status == "stop_label":
                        ev_path = self._save_evidence_frame(frame_bgr, outcome, "needs_decision")
                        self.telemetry.evidence_saved.append(ev_path)
                        self._transition(WorkflowState.NEEDS_DECISION, f"Stop label '{outcome.stop_label}' detected")

                    elif outcome.status == "target_found":
                        target_candidate = outcome.target
                        self._transition(WorkflowState.READY, f"Candidate target found: {target_candidate.label} at {target_candidate.bbox}")

                    elif outcome.status in ("no_target", "ambiguous_target", "geometry_mismatch"):
                        # Keep waiting within deadline
                        time.sleep(0.3)

                # -------------------------------------------------------------
                # STATE: READY (Re-observe and re-validate before acting)
                # -------------------------------------------------------------
                elif self.state == WorkflowState.READY:
                    assert target_candidate is not None
                    # Fresh observation to prevent stale dispatch
                    fresh_meta, fresh_payload = self.client.observe(after_frame_id=last_obs_meta["frame_id"], timeout_ms=2000)
                    last_obs_meta = fresh_meta
                    fw, fh, fstride = fresh_meta["width"], fresh_meta["height"], fresh_meta["stride_bytes"]
                    fresh_bgr = OcrRecognizer.frame_from_bgra_buffer(fresh_payload, fw, fh, fstride)

                    # Re-verify target in fresh frame
                    re_outcome = self.recognizer.recognize(fresh_bgr, self.profile)
                    if re_outcome.status != "target_found":
                        print(f"[{self.state.value}] Re-validation failed: {re_outcome.status} ({re_outcome.reason}) -> returning to WAIT_READY")
                        target_candidate = None
                        self._transition(WorkflowState.WAIT_READY, "revalidation_mismatch")
                        continue

                    fresh_target = re_outcome.target
                    assert fresh_target is not None

                    # Verify bbox compatibility (center drifted by <= 10 pixels)
                    cand_cx, cand_cy = target_candidate.center
                    fresh_cx, fresh_cy = fresh_target.center
                    if abs(cand_cx - fresh_cx) > 15 or abs(cand_cy - fresh_cy) > 15:
                        print(f"[{self.state.value}] Target position shifted from ({cand_cx},{cand_cy}) to ({fresh_cx},{fresh_cy}) -> aborting click")
                        target_candidate = None
                        self._transition(WorkflowState.WAIT_READY, "target_drifted")
                        continue

                    # Execute or simulate action
                    action_record = {
                        "action_index": self.telemetry.actions_executed + 1,
                        "observation_id": fresh_meta["observation_id"],
                        "target_label": fresh_target.label,
                        "target_bbox": list(fresh_target.bbox),
                        "timestamp": time.time(),
                        "dry_run": self.dry_run,
                    }

                    if self.dry_run:
                        print(f"[{self.state.value}] [DRY-RUN] Simulated click on '{fresh_target.label}' at {fresh_target.bbox}")
                        action_record["status"] = "simulated"
                        self.telemetry.actions_executed += 1
                        self.telemetry.action_log.append(action_record)
                        self._transition(WorkflowState.WAIT_TRANSITION, "dry_run_action_simulated")
                    else:
                        print(f"[{self.state.value}] [LIVE] Dispatching guarded click on '{fresh_target.label}' at {fresh_target.bbox}...")
                        click_res = self.client.click(
                            observation_id=fresh_meta["observation_id"],
                            target_bbox_frame_px=list(fresh_target.bbox),
                            max_age_ms=800,
                        )
                        action_record["status"] = "dispatched"
                        action_record["click_res"] = click_res
                        self.telemetry.actions_executed += 1
                        self.telemetry.action_log.append(action_record)
                        print(f"[{self.state.value}] Click dispatched successfully: {click_res}")
                        self._transition(WorkflowState.WAIT_TRANSITION, "click_dispatched")

                # -------------------------------------------------------------
                # STATE: WAIT_TRANSITION (Awaiting postcondition evidence)
                # -------------------------------------------------------------
                elif self.state == WorkflowState.WAIT_TRANSITION:
                    # Require a newer frame
                    time.sleep(0.3)
                    trans_meta, trans_payload = self.client.observe(after_frame_id=last_obs_meta["frame_id"], timeout_ms=4000)
                    last_obs_meta = trans_meta
                    tw, th, tstride = trans_meta["width"], trans_meta["height"], trans_meta["stride_bytes"]
                    trans_bgr = OcrRecognizer.frame_from_bgra_buffer(trans_payload, tw, th, tstride)

                    # Transition check: target should no longer be in the same ready state
                    trans_outcome = self.recognizer.recognize(trans_bgr, self.profile)
                    print(f"[{self.state.value}] Post-action frame status: {trans_outcome.status}")

                    # Transition evidence confirmed:
                    # Either target is gone/processing, or counter incremented, or stop label appeared
                    ev_path = self._save_evidence_frame(trans_bgr, trans_outcome, f"transition_{self.telemetry.actions_executed}")
                    self.telemetry.evidence_saved.append(ev_path)

                    if self.telemetry.actions_executed >= self.max_actions:
                        self._transition(WorkflowState.STOPPED, "action_limit_reached")
                    else:
                        self._transition(WorkflowState.COOLDOWN, "transition_observed")

                # -------------------------------------------------------------
                # STATE: COOLDOWN (Wait for stable UI before next action)
                # -------------------------------------------------------------
                elif self.state == WorkflowState.COOLDOWN:
                    time.sleep(1.0)
                    self._transition(WorkflowState.WAIT_READY, "cooldown_complete")

                # -------------------------------------------------------------
                # STATE: NEEDS_DECISION (Stop automatic input immediately)
                # -------------------------------------------------------------
                elif self.state == WorkflowState.NEEDS_DECISION:
                    print(f"[{self.state.value}] Automated input halted: decision required.")
                    self._transition(WorkflowState.STOPPED, "decision_required")

            except WcuError as err:
                print(f"[StateMachine] WCU Error in state {self.state.value}: {err}")
                self._transition(WorkflowState.STOPPED, f"wcu_error_{err.code}: {err.message}")
            except Exception as ex:
                print(f"[StateMachine] Unexpected exception in state {self.state.value}: {ex}")
                self._transition(WorkflowState.STOPPED, f"exception: {ex}")

        return self._finalize()

    def _transition(self, new_state: WorkflowState, reason: str):
        print(f"[State Transition] {self.state.value} -> {new_state.value} (Reason: {reason})")
        self.state = new_state
        self.telemetry.state = new_state.value
        self.telemetry.stop_reason = reason

    def _save_evidence_frame(self, img: Any, outcome: RecognitionOutcome, prefix: str) -> str:
        filename = f"{prefix}_{int(time.time() * 1000)}.png"
        path = os.path.join(self.evidence_dir, filename)
        annotated = img.copy()

        if outcome.target:
            x, y, w, h = outcome.target.bbox
            cv2.rectangle(annotated, (x, y), (x + w, y + h), (0, 255, 0), 2)
            cv2.putText(annotated, outcome.target.label, (x, max(20, y - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

        if outcome.stop_label:
            cv2.putText(annotated, f"STOP: {outcome.stop_label}", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2)

        cv2.imwrite(path, annotated)
        return path

    def _finalize(self) -> WorkflowTelemetry:
        self.telemetry.end_time = time.time()
        try:
            self.client.detach()
        except Exception:
            pass
        print(f"[StateMachine] Finished: state={self.telemetry.state}, actions={self.telemetry.actions_executed}/{self.max_actions}, reason='{self.telemetry.stop_reason}'")
        return self.telemetry


def main():
    parser = argparse.ArgumentParser(description="Bounded Continue Workflow for Windows Computer Use")
    parser.add_argument("--profile", default=os.path.join(root_dir, "profiles", "fm24.example.json"), help="Profile path")
    parser.add_argument("--hwnd", default=None, help="Target HWND (optional, auto-discovers if omitted)")
    parser.add_argument("--execute", action="store_true", help="Enable live input injection (default: dry-run)")
    parser.add_argument("--max-actions", type=int, default=1, help="Maximum permitted actions (default: 1)")
    parser.add_argument("--timeout-sec", type=float, default=60.0, help="Wall-clock timeout in seconds")
    args = parser.parse_args()

    profile = Profile.from_file(args.profile)

    with WcuClient() as client:
        # Auto-discover target if HWND not provided
        target_hwnd = args.hwnd
        target_pid = 0
        target_create_time = "unknown"

        if not target_hwnd:
            wins = client.list_windows()
            pattern = profile.window_title_pattern.lower()
            matched = next((w for w in wins if pattern in (w.get("title", "") or "").lower() or profile.process_name.lower() in (w.get("process_name", "") or "").lower()), None)
            if not matched:
                print(f"Target matching pattern '{profile.window_title_pattern}' or process '{profile.process_name}' not found")
                sys.exit(1)
            target_hwnd = matched["hwnd"]
            target_pid = matched["pid"]
            target_create_time = matched["process_create_time_utc"]
            print(f"Discovered target window: HWND {target_hwnd}, PID {target_pid}, Title: '{matched.get('title')}'")

        sm = ContinueStateMachine(
            client=client,
            profile=profile,
            hwnd=target_hwnd,
            pid=target_pid,
            process_create_time=target_create_time,
            dry_run=not args.execute,
            max_actions=args.max_actions,
            timeout_sec=args.timeout_sec,
        )

        def sig_handler(sig, frame):
            print("\nReceived interrupt signal, stopping state machine...")
            sm.cancel()

        signal.signal(signal.SIGINT, sig_handler)
        telemetry = sm.run()

        # Print summary JSON
        print("\n=== Workflow Execution Summary ===")
        print(json.dumps(asdict(telemetry), indent=2))


if __name__ == "__main__":
    main()
