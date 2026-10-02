"""Persistent session process: owns the engine and serves CLI commands.

One session process per Windows user desktop. It holds the ``WcuClient``
(and therefore the Rust engine) across independent CLI invocations, so
capture state, attachments, and observation identity survive between
commands. Operations are serialized; a cancel on the separate cancel pipe
terminates the engine so a pending operation fails fast instead of hanging.

The server writes the state file only after the engine is up, so
``wcu session start`` fails fast when the engine cannot start.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Make the repo's client package importable (repo layout: cli/wcu -> ../..).
from wcu.paths import (
    cancel_pipe_name,
    client_dir,
    request_pipe_name,
    session_dir,
    shots_dir,
    state_file,
)

_client = str(client_dir())
if _client not in sys.path:
    sys.path.insert(0, _client)

from wcu_client import WcuClient, WcuError  # noqa: E402

from wcu import ipc  # noqa: E402
from wcu.constants import REFUSAL_CODES  # noqa: E402
from wcu.grounding import clamp_region, find_color, match_template  # noqa: E402
from wcu.coordinates import (
    build_geometry_metadata,
    resolve_bbox,
    resolve_point,
)
from wcu.history import History  # noqa: E402
from wcu.imaging import save_observation_png  # noqa: E402


# How stale an observation may be when a pointer or keyboard action is dispatched
# against it, in milliseconds.
#
# The engine default is 500 ms, which is shorter than one CLI round trip
# plus an OCR pass over the frame. A click grounded from `wcu ocr` would
# therefore always be refused as stale, so pointer and keyboard actions default to 30 s.
# Window identity and foreground guards protect keyboard actions.
POINTER_ACTIONS = frozenset({"click", "hover", "drag", "scroll"})
KEYBOARD_ACTIONS = frozenset({"press", "type", "type_text", "press_key"})
ALL_DEFAULT_30S_ACTIONS = POINTER_ACTIONS | KEYBOARD_ACTIONS
POINTER_MAX_AGE_MS = 30000
KEYBOARD_MAX_AGE_MS = 30000


def _default_max_age(action: str) -> int:
    return 30000 if action in ALL_DEFAULT_30S_ACTIONS else 500


def _pid_alive(pid: int) -> bool:
    import ctypes

    if not pid or pid <= 0:
        return False
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong(0)
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == 259
    finally:
        kernel32.CloseHandle(handle)


def ok_response(request: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "v": 1,
        "id": request.get("id", 0),
        "ok": True,
        "result": result,
    }


def error_response(
    request: Dict[str, Any],
    code: str,
    message: str,
    details: Any = None,
) -> Dict[str, Any]:
    err: Dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        err["details"] = details
    return {
        "v": 1,
        "id": request.get("id", 0),
        "ok": False,
        "error": err,
    }


class SessionServer:
    def __init__(self, session_id: str, engine_path: str):
        self.session_id = session_id
        self.engine_path = engine_path
        self.client: Optional[WcuClient] = None
        self.last_attach: Optional[Dict[str, Any]] = None
        self.history = History()
        self.cancel_event = threading.Event()
        self.in_flight = False
        self._shutdown = False
        self._op_lock = threading.Lock()
        # Observation PNGs this session has written, so a grounding call can
        # be pointed at an explicit id or at simply "the latest".
        self._observation_paths: Dict[int, Path] = {}
        self._last_observation_path: Optional[Path] = None
        self._observation_meta: Dict[int, Dict[str, Any]] = {}
        self._last_observation_meta: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------
    # Engine lifecycle
    # ------------------------------------------------------------------

    def ensure_engine(self) -> WcuClient:
        """Return a live engine client, restarting and re-attaching if needed."""
        if self.client is not None:
            pid = self.client.pid
            if pid and _pid_alive(pid):
                return self.client
            # Engine died; drop it and start a fresh one.
            try:
                self.client.stop()
            except Exception:
                pass
            self.client = None
        self.client = WcuClient(self.engine_path)
        self.client.start()
        if self.last_attach:
            self.client.attach(**self.last_attach)
        return self.client

    # ------------------------------------------------------------------
    # Observation helpers
    # ------------------------------------------------------------------

    def _observe_and_save(
        self,
        monitor: bool = False,
        crop: Optional[Sequence[int]] = None,
        timeout_ms: int = 12000,
    ) -> Dict[str, Any]:
        client = self.ensure_engine()
        if monitor:
            meta, payload = client.observe_monitor(timeout_ms=timeout_ms)
        else:
            meta, payload = client.observe(timeout_ms=timeout_ms)
        width = int(meta["width"])
        height = int(meta["height"])
        crop_offset = [0, 0]
        crop_size: Optional[Tuple[int, int]] = None
        if crop is not None:
            cx, cy, cw, ch = clamp_region(width, height, crop)
            crop_offset = [cx, cy]
            crop_size = (cw, ch)

        path = save_observation_png(
            shots_dir(self.session_id), meta, payload, crop=crop
        )
        meta = dict(meta)
        meta["image_path"] = str(path)
        meta["crop_offset"] = crop_offset
        if crop_size is not None:
            meta["full_width"] = width
            meta["full_height"] = height
            meta["width"] = crop_size[0]
            meta["height"] = crop_size[1]
        meta["geometry"] = build_geometry_metadata(meta, crop_size=crop_size)
        obs_id = int(meta.get("observation_id", 0))
        self._observation_paths[obs_id] = path
        self._last_observation_path = path
        self._observation_meta[obs_id] = meta
        self._last_observation_meta = meta
        return meta

    def _get_observation_meta(
        self, obs_id: Optional[int]
    ) -> Tuple[Dict[str, Any], Optional[int]]:
        """Retrieve observation metadata for an explicit id or the most recent."""
        if obs_id is not None:
            meta = self._observation_meta.get(obs_id)
            if meta is not None:
                return meta, obs_id
        if self._last_observation_meta is not None:
            last_id = self._last_observation_meta.get("observation_id")
            return self._last_observation_meta, int(last_id) if last_id is not None else None
        return {}, obs_id

    def _read_observation(
        self, args: Dict[str, Any]
    ) -> Tuple[Path, Optional[int]]:
        """Resolve the PNG a grounding call should read.

        An explicit ``observation_id`` wins. Otherwise the most recent
        observation is used, so ``wcu ocr`` straight after ``wcu observe``
        needs no id plumbing.
        """
        obs_id = args.get("observation_id")
        if obs_id is not None:
            obs_id = int(obs_id)
            path = self._observation_paths.get(obs_id)
            if path is None or not path.exists():
                raise WcuError(
                    "no_observation",
                    f"No stored observation {obs_id}; run `wcu observe` first",
                )
            return path, obs_id
        if self._last_observation_path is None:
            raise WcuError(
                "no_observation", "No observation available; run `wcu observe`"
            )
        return self._last_observation_path, None

    def _find_window(self, hwnd: str) -> Dict[str, Any]:
        client = self.ensure_engine()
        windows = client.list_windows()
        match = next(
            (w for w in windows if str(w.get("hwnd")) == str(hwnd)), None
        )
        if match is None:
            raise WcuError(
                "window_not_found", f"No visible window with hwnd {hwnd}"
            )
        return match

    def _record_attach(self, window: Dict[str, Any]) -> None:
        self.last_attach = {
            "hwnd": window["hwnd"],
            "pid": window["pid"],
            "process_create_time_utc": window.get(
                "process_create_time_utc", "unknown"
            ),
        }

    # ------------------------------------------------------------------
    # Operation handlers
    # ------------------------------------------------------------------

    def op_ping(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "ok", "session_id": self.session_id}

    def op_capabilities(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return self.ensure_engine().doctor()

    def op_windows(self, args: Dict[str, Any]) -> Dict[str, Any]:
        client = self.ensure_engine()
        windows = client.list_windows()
        filter_text = args.get("filter")
        if filter_text:
            needle = str(filter_text).lower()
            windows = [
                w for w in windows if needle in str(w.get("title", "")).lower()
            ]
        return {"windows": windows, "count": len(windows)}

    def op_monitors(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return {"monitors": self.ensure_engine().list_monitors()}

    def op_attach(self, args: Dict[str, Any]) -> Dict[str, Any]:
        client = self.ensure_engine()
        window = self._find_window(args.get("hwnd", ""))
        self._record_attach(window)
        return client.attach(
            window["hwnd"],
            window["pid"],
            window.get("process_create_time_utc", "unknown"),
        )

    def op_focus(self, args: Dict[str, Any]) -> Dict[str, Any]:
        client = self.ensure_engine()
        window = self._find_window(args.get("hwnd", ""))
        return client.focus_window(
            window["hwnd"],
            pid=window["pid"],
            process_create_time_utc=window.get("process_create_time_utc"),
        )

    def op_switch(self, args: Dict[str, Any]) -> Dict[str, Any]:
        client = self.ensure_engine()
        window = self._find_window(args.get("hwnd", ""))
        focus = client.focus_window(
            window["hwnd"],
            pid=window["pid"],
            process_create_time_utc=window.get("process_create_time_utc"),
        )
        # Re-resolve after the focus change: restoring a minimized window can
        # change its identity fields.
        window = self._find_window(args.get("hwnd", ""))
        self._record_attach(window)
        attach = client.attach(
            window["hwnd"],
            window["pid"],
            window.get("process_create_time_utc", "unknown"),
        )
        observation = self._observe_and_save()
        return {
            "focus": focus,
            "attach": attach,
            "observation": observation,
        }

    def op_observe(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return self._observe_and_save(
            monitor=bool(args.get("monitor")),
            crop=args.get("crop"),
            timeout_ms=int(args.get("timeout_ms", 12000)),
        )

    def op_ocr(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Text-target grounding over a stored observation PNG."""
        import cv2

        path, _obs_id = self._read_observation(args)
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            raise WcuError(
                "no_observation", f"Cannot read observation image: {path}"
            )
        img_h, img_w = image.shape[:2]
        region = args.get("region")
        clamped: Optional[Tuple[int, int, int, int]] = None
        if region is not None:
            coord_space = str(args.get("coord_space", "physical"))
            if coord_space != "physical":
                obs_meta, _ = self._get_observation_meta(_obs_id)
                geometry = obs_meta.get("geometry") or build_geometry_metadata(
                    {"width": img_w, "height": img_h}
                )
                crop_offset = obs_meta.get("crop_offset", [0, 0])
                region = list(
                    resolve_bbox(
                        region,
                        geometry,
                        coord_space=coord_space,
                        crop_offset=crop_offset,
                    )
                )
            try:
                clamped = clamp_region(img_w, img_h, region)
            except ValueError as e:
                raise WcuError("invalid_region", str(e)) from e

        try:
            from recognition import OcrRecognizer  # client dir on sys.path
        except Exception as e:  # noqa: BLE001
            raise WcuError(
                "ocr_unavailable",
                "OCR engine could not be imported: " f"{type(e).__name__}: {e}",
            ) from e

        started = time.perf_counter()
        try:
            boxes = OcrRecognizer.get_instance().recognize_raw(
                image,
                region=list(clamped) if clamped else None,
                min_confidence=float(args.get("min_confidence", 0.0)),
            )
        except WcuError:
            raise
        except Exception as e:  # noqa: BLE001
            raise WcuError(
                "ocr_failed", f"{type(e).__name__}: {e}"
            ) from e
        latency_ms = (time.perf_counter() - started) * 1000.0

        matches: List[Dict[str, Any]] = [
            {
                "text": box.label,
                "bbox": list(box.bbox),
                "confidence": float(box.confidence),
            }
            for box in boxes
        ]

        want = args.get("match")
        best: Optional[Dict[str, Any]] = None
        if want:
            needle = str(want).strip().lower()
            for m in matches:
                if needle in str(m["text"]).strip().lower():
                    if best is None or m["confidence"] > best["confidence"]:
                        best = m

        result: Dict[str, Any] = {
            "matches": matches,
            "count": len(matches),
            "latency_ms": round(latency_ms, 1),
            "image_path": str(path),
            "region": list(clamped) if clamped else None,
        }
        if want:
            result["matched"] = best
        return result

    def op_find(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Template or colour grounding over a stored observation PNG."""
        import cv2

        path, _obs_id = self._read_observation(args)
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            raise WcuError(
                "no_observation", f"Cannot read observation image: {path}"
            )
        img_h, img_w = image.shape[:2]
        region = args.get("region")
        clamped: Optional[Tuple[int, int, int, int]] = None
        if region is not None:
            coord_space = str(args.get("coord_space", "physical"))
            if coord_space != "physical":
                obs_meta, _ = self._get_observation_meta(_obs_id)
                geometry = obs_meta.get("geometry") or build_geometry_metadata(
                    {"width": img_w, "height": img_h}
                )
                crop_offset = obs_meta.get("crop_offset", [0, 0])
                region = list(
                    resolve_bbox(
                        region,
                        geometry,
                        coord_space=coord_space,
                        crop_offset=crop_offset,
                    )
                )
            try:
                clamped = clamp_region(img_w, img_h, region)
            except ValueError as e:
                raise WcuError("invalid_region", str(e)) from e

        template_path = args.get("template_path")
        color = args.get("color")
        if not template_path and not color:
            raise WcuError(
                "invalid_request",
                "At least one of 'template_path' or 'color' is required",
            )

        found: List[Dict[str, Any]] = []
        if template_path:
            template = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
            if template is None:
                raise WcuError(
                    "template_not_found",
                    f"Cannot read template image: {template_path}",
                )
            try:
                found = match_template(
                    image,
                    template,
                    region=list(clamped) if clamped else None,
                    threshold=float(args.get("threshold", 0.8)),
                )
            except ValueError as e:
                raise WcuError("find_failed", str(e)) from e
        else:
            try:
                found = find_color(
                    image,
                    str(color),
                    tolerance=int(args.get("tolerance", 30)),
                    region=list(clamped) if clamped else None,
                    min_area=int(args.get("min_area", 25)),
                )
            except ValueError as e:
                raise WcuError("find_failed", str(e)) from e

        return {
            "matches": found,
            "count": len(found),
            "image_path": str(path),
            "region": list(clamped) if clamped else None,
        }

    def op_launch(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Start a process or URI, optionally waiting for a matching window."""
        import os as _os
        import subprocess

        target = str(args.get("target", "")).strip()
        if not target:
            raise WcuError("invalid_request", "launch needs a target")

        argv = [str(a) for a in (args.get("args") or [])]
        timeout_ms = int(args.get("timeout_ms", 0))
        want_title = args.get("window_title")
        want_title_lower = str(want_title).lower() if want_title else None
        client = self.ensure_engine()

        if _os.name == "nt" and "://" in target:
            # URI or shell verb: hand it to the shell rather than CreateProcess.
            _os.startfile(target)  # noqa: S606
            pid = None
        else:
            # No console window: the caller is usually an agent, and a
            # flashing console over the desktop is both noise and a
            # foreground-steal risk for the very next action.
            flags = 0
            for _name in (
                "CREATE_NO_WINDOW",
                "CREATE_NEW_PROCESS_GROUP",
                "DETACHED_PROCESS",
            ):
                flags |= getattr(subprocess, _name, 0)
            try:
                proc = subprocess.Popen(
                    [target, *argv],
                    creationflags=flags,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except FileNotFoundError as e:
                raise WcuError(
                    "launch_failed", f"Program not found: {target}"
                ) from e
            except OSError as e:
                raise WcuError(
                    "launch_failed", f"Could not launch {target}: {e}"
                ) from e
            pid = proc.pid

        waited_for: Optional[str] = None
        if timeout_ms > 0:
            deadline = time.monotonic() + timeout_ms / 1000.0
            while time.monotonic() < deadline:
                try:
                    for w in client.list_windows():
                        if not w.get("is_visible"):
                            continue
                        title = str(w.get("title", ""))
                        if want_title_lower is None:
                            matched = bool(title)
                        else:
                            matched = want_title_lower in title.lower()
                        if matched:
                            waited_for = title
                            break
                except WcuError:
                    pass
                if waited_for is not None:
                    break
                time.sleep(0.25)

        result: Dict[str, Any] = {
            "status": "launched",
            "target": target,
            "pid": pid,
        }
        if timeout_ms > 0:
            result["window_found"] = waited_for is not None
            result["window_title"] = waited_for
            result["timed_out"] = waited_for is None
        return result

    def op_inspect(self, args: Dict[str, Any]) -> Dict[str, Any]:
        client = self.ensure_engine()
        elements, truncated = client.inspect(
            max_depth=int(args.get("max_depth", 8)),
            max_elements=int(args.get("max_elements", 500)),
            timeout_ms=int(args.get("timeout_ms", 2000)),
        )
        result: Dict[str, Any] = {
            "elements": elements,
            "truncated": truncated,
            "count": len(elements),
        }
        question = args.get("question")
        if question:
            result["question"] = str(question)
            # Record the question so history shows what each inspection served.
            self.history.record("inspect", "ok", question=str(question))
        return result

    def op_history(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return {"entries": self.history.entries()}

    def op_status(self, args: Dict[str, Any]) -> Dict[str, Any]:
        engine_pid = self.client.pid if self.client else None
        engine_alive = bool(engine_pid and _pid_alive(engine_pid))
        target = None
        if self.last_attach and engine_alive:
            try:
                windows = self.client.list_windows()
                match = next(
                    (
                        w
                        for w in windows
                        if str(w.get("hwnd")) == str(self.last_attach["hwnd"])
                    ),
                    None,
                )
                if match:
                    target = {
                        "hwnd": match["hwnd"],
                        "title": match.get("title", ""),
                        "pid": match["pid"],
                    }
            except Exception:
                target = dict(self.last_attach)
        return {
            "session_id": self.session_id,
            "engine_pid": engine_pid,
            "engine_alive": engine_alive,
            "attached_target": target,
            "in_flight": self.in_flight,
            "history_entries": len(self.history.entries()),
        }

    def op_act(self, args: Dict[str, Any]) -> Dict[str, Any]:
        client = self.ensure_engine()
        action = str(args.get("action", ""))
        dry_run = bool(args.get("dry_run"))
        auto_refresh = bool(args.get("auto_refresh"))
        obs_id = args.get("observation_id")
        obs_id = int(obs_id) if obs_id is not None else None
        max_age = int(args.get("max_age_ms", _default_max_age(action)))

        try:
            dispatch, res_pt, res_box = self._dispatch_action(
                client, action, args, obs_id, max_age, dry_run
            )
        except WcuError as e:
            if self.cancel_event.is_set():
                self.cancel_event.clear()
                raise WcuError("cancelled", "Operation cancelled") from e

            # Stale-observation fast-path and auto-refresh
            if auto_refresh and e.code == "stale_observation" and not dry_run:
                current_obs_id = obs_id
                for attempt in range(3):
                    fresh_meta = self._observe_and_save()
                    new_obs_id = int(fresh_meta.get("observation_id", 0))

                    # Safety boundary validation against the previous observation
                    prev_meta = (
                        self._observation_meta.get(current_obs_id)
                        if current_obs_id is not None
                        else self._last_observation_meta
                    )
                    if prev_meta:
                        orig_ident = prev_meta.get("window_identity") or {}
                        fresh_ident = fresh_meta.get("window_identity") or {}
                        if (
                            str(orig_ident.get("hwnd")) != str(fresh_ident.get("hwnd"))
                            or orig_ident.get("pid") != fresh_ident.get("pid")
                        ):
                            raise WcuError(
                                "window_moved",
                                "Auto-refresh aborted: window identity changed",
                                {"evidence": fresh_meta, "action": action},
                            ) from e
                        if (
                            fresh_meta.get("geometry_epoch")
                            != prev_meta.get("geometry_epoch")
                        ):
                            raise WcuError(
                                "window_moved",
                                "Auto-refresh aborted: window geometry changed",
                                {"evidence": fresh_meta, "action": action},
                            ) from e
                        if (
                            fresh_meta.get("foreground_epoch")
                            != prev_meta.get("foreground_epoch")
                        ):
                            raise WcuError(
                                "foreground_lost",
                                "Auto-refresh aborted: window lost foreground",
                                {"evidence": fresh_meta, "action": action},
                            ) from e

                    try:
                        dispatch, res_pt, res_box = self._dispatch_action(
                            client, action, args, new_obs_id, max_age, dry_run
                        )
                        self.history.record(
                            f"act:{action}", "ok", observation_id=new_obs_id
                        )
                        result: Dict[str, Any] = {
                            "status": "ok",
                            "action": action,
                            "auto_refreshed": True,
                            "observation_id": new_obs_id,
                            "stale_observation_id": obs_id,
                            "dispatch": dispatch,
                            "evidence": fresh_meta,
                        }
                        if res_pt is not None:
                            result["resolved_physical_point"] = res_pt
                        if res_box is not None:
                            result["resolved_physical_bbox"] = res_box
                        return result
                    except WcuError as retry_err:
                        if retry_err.code == "stale_observation" and attempt < 2:
                            current_obs_id = new_obs_id
                            time.sleep(0.05)
                            continue
                        raise WcuError(
                            retry_err.code,
                            retry_err.message,
                            {"evidence": fresh_meta, "action": action},
                        ) from retry_err

            if e.code in REFUSAL_CODES and not dry_run:
                # The proposal no longer applies. Return updated evidence so
                # the caller can reconsider without redundant setup. The
                # evidence rides in the error details; the envelope is ok:false.
                evidence = self._observe_and_save()
                self.history.record(
                    f"act:{action}",
                    "refused",
                    observation_id=obs_id,
                    error_code=e.code,
                )
                raise WcuError(
                    e.code,
                    e.message,
                    {"evidence": evidence, "action": action},
                ) from e
            raise

        outcome = "dry_run" if dry_run else "ok"
        self.history.record(
            f"act:{action}", outcome, observation_id=obs_id
        )
        result: Dict[str, Any] = {
            "status": outcome,
            "action": action,
            "dispatch": dispatch,
        }
        if res_pt is not None:
            result["resolved_physical_point"] = res_pt
        if res_box is not None:
            result["resolved_physical_bbox"] = res_box
        if not dry_run:
            # Post-action evidence so the caller can choose the next step.
            result["evidence"] = self._observe_and_save()
        return result

    def _dispatch_action(
        self,
        client: WcuClient,
        action: str,
        args: Dict[str, Any],
        obs_id: Optional[int],
        max_age: int,
        dry_run: bool,
    ) -> Tuple[Dict[str, Any], Optional[List[int]], Optional[List[int]]]:
        coord_space = str(args.get("coord_space", "physical"))
        obs_meta, _ = self._get_observation_meta(obs_id)
        geometry = obs_meta.get("geometry") or build_geometry_metadata(obs_meta)
        crop_offset = obs_meta.get("crop_offset", [0, 0])
        resolved_point: Optional[List[int]] = None
        resolved_bbox: Optional[List[int]] = None

        if action == "click":
            if args.get("bbox"):
                px_bbox = resolve_bbox(
                    args["bbox"], geometry, coord_space=coord_space, crop_offset=crop_offset
                )
                target = {"bbox_frame_px": list(px_bbox)}
                resolved_bbox = list(px_bbox)
            elif args.get("point"):
                px_point = resolve_point(
                    args["point"], geometry, coord_space=coord_space, crop_offset=crop_offset
                )
                target = {"point_frame_px": list(px_point)}
                resolved_point = list(px_point)
            else:
                raise WcuError("invalid_request", "click needs bbox or point")
            dispatch = client.click(
                observation_id=obs_id,
                target=target,
                max_age_ms=max_age,
                dry_run=dry_run,
                button=str(args.get("button", "left")),
                click_count=int(args.get("click_count", 1)),
            )
            return dispatch, resolved_point, resolved_bbox
        if action == "type":
            dispatch = client.type_text(
                text=str(args.get("text", "")),
                observation_id=obs_id,
                max_age_ms=max_age,
                delay_ms=args.get("delay_ms"),
                method=str(args.get("method", "unicode")),
                dry_run=dry_run,
            )
            return dispatch, None, None
        if action == "press":
            dispatch = client.press_key(
                chord=str(args.get("chord", "")),
                observation_id=obs_id,
                max_age_ms=max_age,
                dry_run=dry_run,
            )
            return dispatch, None, None
        if action == "scroll":
            target = None
            if args.get("point"):
                px_point = resolve_point(
                    args["point"], geometry, coord_space=coord_space, crop_offset=crop_offset
                )
                target = {"point_frame_px": list(px_point)}
                resolved_point = list(px_point)
            dispatch = client.scroll(
                notches_x=int(args.get("notches_x", 0)),
                notches_y=int(args.get("notches_y", 0)),
                observation_id=obs_id,
                max_age_ms=max_age,
                dry_run=dry_run,
                target=target,
            )
            return dispatch, resolved_point, None
        if action == "hover":
            if args.get("point"):
                px_point = resolve_point(
                    args["point"], geometry, coord_space=coord_space, crop_offset=crop_offset
                )
                target = {"point_frame_px": list(px_point)}
                resolved_point = list(px_point)
            elif args.get("bbox"):
                px_bbox = resolve_bbox(
                    args["bbox"], geometry, coord_space=coord_space, crop_offset=crop_offset
                )
                target = {"bbox_frame_px": list(px_bbox)}
                resolved_bbox = list(px_bbox)
            else:
                raise WcuError("invalid_request", "hover needs point or bbox")
            dispatch = client.hover(
                observation_id=obs_id,
                target=target,
                max_age_ms=max_age,
                dry_run=dry_run,
                duration_ms=args.get("duration_ms"),
            )
            return dispatch, resolved_point, resolved_bbox
        if action == "drag":
            if not args.get("from") or not args.get("to"):
                raise WcuError("invalid_request", "drag needs from and to points")
            from_pt = resolve_point(
                args["from"], geometry, coord_space=coord_space, crop_offset=crop_offset
            )
            to_pt = resolve_point(
                args["to"], geometry, coord_space=coord_space, crop_offset=crop_offset
            )
            dispatch = client.drag(
                from_target={"point_frame_px": list(from_pt)},
                to_target={"point_frame_px": list(to_pt)},
                observation_id=obs_id,
                max_age_ms=max_age,
                dry_run=dry_run,
                steps=args.get("steps"),
                duration_ms=args.get("duration_ms"),
            )
            return dispatch, list(from_pt), None
        if action == "focus":
            window = self._find_window(args.get("hwnd", ""))
            dispatch = client.focus_window(
                window["hwnd"],
                pid=window["pid"],
                process_create_time_utc=window.get("process_create_time_utc"),
            )
            return dispatch, None, None
        if action == "invoke":
            dispatch = client.uia_action(
                token=str(args.get("token", "")), action="invoke"
            )
            return dispatch, None, None
        if action == "set-value":
            dispatch = client.uia_action(
                token=str(args.get("token", "")),
                action="set_value",
                value=args.get("value"),
            )
            return dispatch, None, None
        raise WcuError("invalid_request", f"Unknown action '{action}'")

    def op_stop(self, args: Dict[str, Any]) -> Dict[str, Any]:
        self._shutdown = True
        if self.client is not None:
            try:
                self.client.stop()
            except Exception:
                pass
            self.client = None
        return {"status": "stopped", "session_id": self.session_id}

    # ------------------------------------------------------------------
    # Request dispatch
    # ------------------------------------------------------------------

    # Operations that only read local session state and never touch the engine.
    # They do not count as "in flight" for cancellation purposes.
    LOCAL_OPS = {"ping", "status", "history", "stop"}

    # Operations that record their own history entries (with richer detail
    # than the generic success record).
    SELF_RECORDING_OPS = {"act"}

    def handle(self, request: Dict[str, Any]) -> Dict[str, Any]:
        op = str(request.get("op", ""))
        args = request.get("args") or {}
        handler = getattr(self, f"op_{op}", None)
        if handler is None:
            return error_response(
                request, "unsupported", f"Unknown operation '{op}'"
            )
        is_local = op in self.LOCAL_OPS
        # A cancel that arrived while no operation was pending is a no-op;
        # clear it here so it never poisons the next command.
        self.cancel_event.clear()
        try:
            with self._op_lock:
                self.in_flight = not is_local
                try:
                    result = handler(args)
                finally:
                    self.in_flight = False
            if not is_local and op not in self.SELF_RECORDING_OPS:
                self.history.record(op, "ok")
            return ok_response(request, result)
        except WcuError as e:
            status = "refused" if e.code in REFUSAL_CODES else "error"
            self.history.record(
                op, status, error_code=e.code
            )
            return error_response(request, e.code, e.message, e.details)
        except Exception as e:  # noqa: BLE001 - report honestly, never crash
            self.history.record(op, "error", error_code="internal_error")
            return error_response(
                request,
                "internal_error",
                f"{type(e).__name__}: {e}",
                {"traceback": traceback.format_exc(limit=5)},
            )

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    def _do_cancel(self) -> bool:
        """Cancel a pending operation. Returns True if one was interrupted."""
        was_in_flight = self.in_flight
        if was_in_flight and self.client is not None:
            # Kill the engine directly: the pending operation holds the
            # client lock, so going through client.stop() would block until
            # the operation finished. Terminating the process unblocks the
            # pending read immediately.
            self.client.kill()
            self.cancel_event.set()
        return was_in_flight

    def _cancel_listener(self, cancel_handle) -> None:
        import win32file
        import win32pipe
        import pywintypes

        while not self._shutdown:
            try:
                win32pipe.ConnectNamedPipe(cancel_handle, None)
            except pywintypes.error:
                if self._shutdown:
                    break
                continue
            try:
                hr, data = win32file.ReadFile(cancel_handle, 256)
                if hr == 0 and data:
                    cancelled = self._do_cancel()
                    reply = ipc.make_message(
                        {"v": 1, "id": 0, "ok": True,
                         "result": {"cancelled": cancelled}}
                    )
                    try:
                        win32file.WriteFile(cancel_handle, reply)
                    except pywintypes.error:
                        pass
            except pywintypes.error:
                if self._shutdown:
                    break
            except Exception:
                if self._shutdown:
                    break

    # ------------------------------------------------------------------
    # Serving
    # ------------------------------------------------------------------

    def _create_request_pipe(self):
        import win32pipe

        return win32pipe.CreateNamedPipe(
            request_pipe_name(self.session_id),
            win32pipe.PIPE_ACCESS_DUPLEX,
            win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_WAIT,
            win32pipe.PIPE_UNLIMITED_INSTANCES,
            ipc.MESSAGE_BUFFER,
            ipc.MESSAGE_BUFFER,
            0,
            None,
        )

    def _create_cancel_pipe(self):
        import win32pipe

        return win32pipe.CreateNamedPipe(
            cancel_pipe_name(self.session_id),
            win32pipe.PIPE_ACCESS_DUPLEX,
            win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_WAIT,
            win32pipe.PIPE_UNLIMITED_INSTANCES,
            4096,
            4096,
            0,
            None,
        )

    def _write_state(self) -> None:
        state = {
            "session_id": self.session_id,
            "pid": os.getpid(),
            "request_pipe": request_pipe_name(self.session_id),
            "cancel_pipe": cancel_pipe_name(self.session_id),
            "created_at": time.time(),
            "engine_path": self.engine_path,
        }
        state_file().parent.mkdir(parents=True, exist_ok=True)
        tmp = state_file().with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, state_file())

    def serve(self) -> int:
        import win32file
        import win32pipe
        import pywintypes

        # Fail fast: bring the engine up before advertising the session.
        try:
            self.ensure_engine()
        except Exception as e:
            _write_error_state(
                self.session_id,
                "engine_start_failed",
                f"{type(e).__name__}: {e}",
            )
            return 1

        self._write_state()

        cancel_handle = self._create_cancel_pipe()
        cancel_thread = threading.Thread(
            target=self._cancel_listener,
            args=(cancel_handle,),
            daemon=True,
        )
        cancel_thread.start()

        try:
            while not self._shutdown:
                req_handle = self._create_request_pipe()
                try:
                    win32pipe.ConnectNamedPipe(req_handle, None)
                except pywintypes.error:
                    win32file.CloseHandle(req_handle)
                    if self._shutdown:
                        break
                    continue

                response = None
                try:
                    hr, data = win32file.ReadFile(req_handle, ipc.MESSAGE_BUFFER)
                    if hr == ipc.ERROR_MORE_DATA:
                        response = error_response(
                            {}, "response_too_large",
                            "Request exceeded the 1 MiB message bound",
                        )
                    elif hr != 0:
                        # Client disconnected before sending; wait for the next.
                        continue
                    elif not data:
                        continue
                    else:
                        request = ipc.parse_message(data)
                        response = self.handle(request)
                except pywintypes.error:
                    # Client went away; nothing to respond to.
                    continue
                except Exception as e:  # noqa: BLE001
                    response = error_response(
                        {}, "internal_error", f"{type(e).__name__}: {e}"
                    )

                if response is not None:
                    try:
                        win32file.WriteFile(
                            req_handle, ipc.make_message(response)
                        )
                    except pywintypes.error:
                        pass
                try:
                    win32file.CloseHandle(req_handle)
                except Exception:
                    pass
        finally:
            self._shutdown = True
            try:
                win32file.CloseHandle(cancel_handle)
            except Exception:
                pass
            if self.client is not None:
                try:
                    self.client.stop()
                except Exception:
                    pass
            # Release session resources: screenshots and state.
            import shutil

            root = session_dir(self.session_id)
            if root.exists():
                try:
                    shutil.rmtree(root)
                except OSError:
                    pass
            try:
                os.remove(state_file())
            except OSError:
                pass
        return 0


def _write_error_state(session_id: str, code: str, message: str) -> None:
    state = {
        "session_id": session_id,
        "error": code,
        "error_message": message,
    }
    try:
        state_file().parent.mkdir(parents=True, exist_ok=True)
        with open(state_file(), "w", encoding="utf-8") as f:
            json.dump(state, f)
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="wcu session server")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--engine-path", required=True)
    args = parser.parse_args()
    server = SessionServer(args.session_id, args.engine_path)
    return server.serve()


if __name__ == "__main__":
    raise SystemExit(main())
