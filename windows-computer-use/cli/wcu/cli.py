"""wcu command line interface.

Every command prints one JSON result envelope on stdout and diagnostics on
stderr. The envelope carries the session id, an operation id, a status, and
either a result or an actionable error. Machine-readable stdout is the
contract; stderr is for humans.

Exit codes:
  0  a result envelope was produced (ok, dry_run, refused, cancelled, error)
  1  transport failure (no session, broken pipe, timeout)
  2  usage error
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from typing import Any, Callable, Dict, List, Optional

from wcu import __version__
from wcu import session as sess
from wcu.constants import REFUSAL_CODES
from wcu.ipc import PipeError
from wcu.paths import default_engine_path

USAGE_EXIT = 2
TRANSPORT_EXIT = 1


def _envelope(
    session_id: Optional[str],
    ok: bool,
    status: str,
    result: Any = None,
    error: Any = None,
) -> Dict[str, Any]:
    env: Dict[str, Any] = {
        "v": 1,
        "session_id": session_id,
        "operation_id": uuid.uuid4().hex[:12],
        "ok": ok,
        "status": status,
    }
    if result is not None:
        env["result"] = result
    if error is not None:
        env["error"] = error
    return env


def _emit(env: Dict[str, Any]) -> int:
    json.dump(env, sys.stdout, indent=2)
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 0


def _error_env(
    session_id: Optional[str], code: str, message: str, details: Any = None
) -> Dict[str, Any]:
    err: Dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        err["details"] = details
    status = "refused" if code in REFUSAL_CODES else "error"
    return _envelope(session_id, False, status, error=err)


def _handle_session_error(e: sess.SessionError) -> int:
    env = _error_env(None, e.code, e.message, e.details)
    return _emit(env)


def _handle_pipe_error(e: PipeError) -> int:
    env = _error_env(None, e.code, e.message)
    return _emit(env)


# ----------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------


def cmd_help(args: argparse.Namespace) -> int:
    text = """wcu - direct-agent Windows computer-use CLI

Core loop (one session, many commands):
  wcu session start                 start the persistent desktop session
  wcu windows [--filter TEXT]       list visible windows
  wcu attach <hwnd>                 attach capture to a window
  wcu observe [--crop X,Y,W,H]       capture a screenshot (PNG on disk)
  wcu act click --observation-id N --point X,Y   dispatch a guarded action
  wcu session status                session, engine, and target state
  wcu session stop                  stop the session and clean up

Discovery and inspection:
  wcu capabilities                  engine and desktop capabilities
  wcu monitors                      list display monitors
  wcu inspect [--question TEXT]     bounded accessibility inspection
  wcu history                       bounded operation history (no typed text)

Grounding (prefer these over estimating pixels from a full screenshot):
  wcu ocr [--region X,Y,W,H] [--match TEXT]   text-target grounding
  wcu find (--template PATH | --color #RRGGBB) icon/colour grounding
  wcu launch <target> [--window-title TEXT]   start a program or URI

Window control:
  wcu focus <hwnd>                  focus a window (verified)
  wcu switch <hwnd>                 focus + attach + fresh observation
  wcu open <program> [args...]      launch a program (host process tool)

Actions (guarded, revalidated against current evidence):
  wcu act click --observation-id N (--bbox X,Y,W,H | --point X,Y)
  wcu act type --text TEXT [--observation-id N]
  wcu act press --chord CHORD [--observation-id N]
  wcu act scroll [--notches-x N] [--notches-y N] [--point X,Y]
  wcu act hover --observation-id N (--point X,Y | --bbox X,Y,W,H)
  wcu act drag --observation-id N --from X,Y --to X,Y
  wcu act focus --hwnd N
  wcu act invoke --token T          invoke a UIA element
  wcu act set-value --token T --value V
  Add --dry-run to any action to validate without dispatching input.

Control:
  wcu cancel                        cancel a pending operation
  wcu help                          this help

Run `wcu <command> --help` for per-command syntax. Screenshots are written
to the session directory and deleted on `wcu session stop`.
"""
    print(text)
    return 0


def cmd_capabilities(args: argparse.Namespace) -> int:
    try:
        resp = sess.session_call("capabilities", timeout_sec=15.0)
    except sess.SessionError as e:
        return _handle_session_error(e)
    except PipeError as e:
        return _handle_pipe_error(e)
    if not resp.get("ok"):
        return _emit(_error_env(_session_id(), resp["error"]["code"], resp["error"]["message"], resp["error"].get("details")))
    return _emit(_envelope(_session_id(), True, "ok", result=resp["result"]))


def cmd_session_start(args: argparse.Namespace) -> int:
    try:
        state = sess.start_session(engine_path=args.engine)
    except sess.SessionError as e:
        return _handle_session_error(e)
    try:
        resp = sess.session_call("capabilities", timeout_sec=15.0)
        caps = resp.get("result") if resp.get("ok") else None
    except (sess.SessionError, PipeError):
        caps = None
    result = {
        "session_id": state.get("session_id"),
        "pid": state.get("pid"),
        "request_pipe": state.get("request_pipe"),
        "cancel_pipe": state.get("cancel_pipe"),
        "capabilities": caps,
    }
    return _emit(_envelope(state.get("session_id"), True, "ok", result=result))


def cmd_session_status(args: argparse.Namespace) -> int:
    try:
        resp = sess.session_call("status", timeout_sec=10.0)
    except sess.SessionError as e:
        return _handle_session_error(e)
    except PipeError as e:
        return _handle_pipe_error(e)
    if not resp.get("ok"):
        return _emit(_error_env(_session_id(), resp["error"]["code"], resp["error"]["message"], resp["error"].get("details")))
    return _emit(_envelope(_session_id(), True, "ok", result=resp["result"]))


def cmd_session_stop(args: argparse.Namespace) -> int:
    try:
        result = sess.stop_session()
    except sess.SessionError as e:
        return _handle_session_error(e)
    return _emit(_envelope(result.get("session_id"), True, "ok", result=result))


def cmd_windows(args: argparse.Namespace) -> int:
    try:
        resp = sess.session_call("windows", {"filter": args.filter}, timeout_sec=15.0)
    except sess.SessionError as e:
        return _handle_session_error(e)
    except PipeError as e:
        return _handle_pipe_error(e)
    if not resp.get("ok"):
        return _emit(_error_env(_session_id(), resp["error"]["code"], resp["error"]["message"], resp["error"].get("details")))
    return _emit(_envelope(_session_id(), True, "ok", result=resp["result"]))


def cmd_monitors(args: argparse.Namespace) -> int:
    try:
        resp = sess.session_call("monitors", timeout_sec=15.0)
    except sess.SessionError as e:
        return _handle_session_error(e)
    except PipeError as e:
        return _handle_pipe_error(e)
    if not resp.get("ok"):
        return _emit(_error_env(_session_id(), resp["error"]["code"], resp["error"]["message"], resp["error"].get("details")))
    return _emit(_envelope(_session_id(), True, "ok", result=resp["result"]))


def cmd_attach(args: argparse.Namespace) -> int:
    try:
        resp = sess.session_call("attach", {"hwnd": args.hwnd}, timeout_sec=15.0)
    except sess.SessionError as e:
        return _handle_session_error(e)
    except PipeError as e:
        return _handle_pipe_error(e)
    if not resp.get("ok"):
        return _emit(_error_env(_session_id(), resp["error"]["code"], resp["error"]["message"], resp["error"].get("details")))
    return _emit(_envelope(_session_id(), True, "ok", result=resp["result"]))


def cmd_focus(args: argparse.Namespace) -> int:
    try:
        resp = sess.session_call("focus", {"hwnd": args.hwnd}, timeout_sec=15.0)
    except sess.SessionError as e:
        return _handle_session_error(e)
    except PipeError as e:
        return _handle_pipe_error(e)
    if not resp.get("ok"):
        return _emit(_error_env(_session_id(), resp["error"]["code"], resp["error"]["message"], resp["error"].get("details")))
    return _emit(_envelope(_session_id(), True, "ok", result=resp["result"]))


def cmd_switch(args: argparse.Namespace) -> int:
    try:
        resp = sess.session_call("switch", {"hwnd": args.hwnd}, timeout_sec=20.0)
    except sess.SessionError as e:
        return _handle_session_error(e)
    except PipeError as e:
        return _handle_pipe_error(e)
    if not resp.get("ok"):
        return _emit(_error_env(_session_id(), resp["error"]["code"], resp["error"]["message"], resp["error"].get("details")))
    return _emit(_envelope(_session_id(), True, "ok", result=resp["result"]))


def cmd_observe(args: argparse.Namespace) -> int:
    payload: Dict[str, Any] = {"monitor": args.monitor}
    if args.crop:
        payload["crop"] = list(args.crop)
    try:
        resp = sess.session_call("observe", payload, timeout_sec=15.0)
    except sess.SessionError as e:
        return _handle_session_error(e)
    except PipeError as e:
        return _handle_pipe_error(e)
    if not resp.get("ok"):
        return _emit(_error_env(_session_id(), resp["error"]["code"], resp["error"]["message"], resp["error"].get("details")))
    return _emit(_envelope(_session_id(), True, "ok", result=resp["result"]))


def cmd_ocr(args: argparse.Namespace) -> int:
    payload: Dict[str, Any] = {"match": args.match}
    if args.obs is not None:
        payload["observation_id"] = args.obs
    if args.region:
        payload["region"] = list(args.region)
    if args.min_confidence:
        payload["min_confidence"] = args.min_confidence
    try:
        resp = sess.session_call("ocr", payload, timeout_sec=60.0)
    except sess.SessionError as e:
        return _handle_session_error(e)
    except PipeError as e:
        return _handle_pipe_error(e)
    if not resp.get("ok"):
        return _emit(_error_env(_session_id(), resp["error"]["code"], resp["error"]["message"], resp["error"].get("details")))
    return _emit(_envelope(_session_id(), True, "ok", result=resp["result"]))


def cmd_find(args: argparse.Namespace) -> int:
    payload: Dict[str, Any] = {}
    if args.template:
        payload["template_path"] = args.template
    if args.color:
        payload["color"] = args.color
    if args.obs is not None:
        payload["observation_id"] = args.obs
    if args.region:
        payload["region"] = list(args.region)
    if args.threshold is not None:
        payload["threshold"] = args.threshold
    if args.tolerance is not None:
        payload["tolerance"] = args.tolerance
    try:
        resp = sess.session_call("find", payload, timeout_sec=60.0)
    except sess.SessionError as e:
        return _handle_session_error(e)
    except PipeError as e:
        return _handle_pipe_error(e)
    if not resp.get("ok"):
        return _emit(_error_env(_session_id(), resp["error"]["code"], resp["error"]["message"], resp["error"].get("details")))
    return _emit(_envelope(_session_id(), True, "ok", result=resp["result"]))


def cmd_launch(args: argparse.Namespace) -> int:
    payload: Dict[str, Any] = {"target": args.target, "args": list(args.args or [])}
    if args.window_title:
        payload["window_title"] = args.window_title
        payload["timeout_ms"] = int(args.timeout * 1000)
    try:
        resp = sess.session_call("launch", payload, timeout_sec=args.timeout + 30.0)
    except sess.SessionError as e:
        return _handle_session_error(e)
    except PipeError as e:
        return _handle_pipe_error(e)
    if not resp.get("ok"):
        return _emit(_error_env(_session_id(), resp["error"]["code"], resp["error"]["message"], resp["error"].get("details")))
    return _emit(_envelope(_session_id(), True, "ok", result=resp["result"]))


def cmd_inspect(args: argparse.Namespace) -> int:
    try:
        resp = sess.session_call(
            "inspect",
            {
                "max_depth": args.max_depth,
                "max_elements": args.max_elements,
                "question": args.question,
            },
            timeout_sec=20.0,
        )
    except sess.SessionError as e:
        return _handle_session_error(e)
    except PipeError as e:
        return _handle_pipe_error(e)
    if not resp.get("ok"):
        return _emit(_error_env(_session_id(), resp["error"]["code"], resp["error"]["message"], resp["error"].get("details")))
    return _emit(_envelope(_session_id(), True, "ok", result=resp["result"]))


def cmd_history(args: argparse.Namespace) -> int:
    try:
        resp = sess.session_call("history", timeout_sec=10.0)
    except sess.SessionError as e:
        return _handle_session_error(e)
    except PipeError as e:
        return _handle_pipe_error(e)
    if not resp.get("ok"):
        return _emit(_error_env(_session_id(), resp["error"]["code"], resp["error"]["message"], resp["error"].get("details")))
    return _emit(_envelope(_session_id(), True, "ok", result=resp["result"]))


def cmd_cancel(args: argparse.Namespace) -> int:
    try:
        resp = sess.cancel()
    except sess.SessionError as e:
        return _handle_session_error(e)
    except PipeError as e:
        return _handle_pipe_error(e)
    if not resp.get("ok"):
        return _emit(_error_env(_session_id(), resp["error"]["code"], resp["error"]["message"], resp["error"].get("details")))
    return _emit(_envelope(_session_id(), True, "ok", result=resp["result"]))


def cmd_open(args: argparse.Namespace) -> int:
    if not args.program:
        return _emit(_error_env(_session_id(), "invalid_request", "open needs a program name"))
    try:
        proc = subprocess.Popen([args.program] + list(args.args))
    except FileNotFoundError:
        return _emit(
            _error_env(_session_id(), "program_not_found", f"Program not found: {args.program}")
        )
    except OSError as e:
        return _emit(
            _error_env(_session_id(), "open_failed", f"Could not launch {args.program}: {e}")
        )
    return _emit(
        _envelope(
            _session_id(),
            True,
            "ok",
            result={"status": "launched", "program": args.program, "pid": proc.pid},
        )
    )


def cmd_act(args: argparse.Namespace) -> int:
    action_args: Dict[str, Any] = {"action": args.action, "dry_run": args.dry_run}
    if getattr(args, "observation_id", None) is not None:
        action_args["observation_id"] = args.observation_id
    if getattr(args, "max_age_ms", None) is not None:
        action_args["max_age_ms"] = args.max_age_ms
    if getattr(args, "bbox", None):
        action_args["bbox"] = args.bbox
    if getattr(args, "point", None):
        action_args["point"] = args.point
    if getattr(args, "text", None) is not None:
        action_args["text"] = args.text
    if getattr(args, "method", None) is not None:
        action_args["method"] = args.method
    if getattr(args, "delay_ms", None) is not None:
        action_args["delay_ms"] = args.delay_ms
    if getattr(args, "chord", None) is not None:
        action_args["chord"] = args.chord
    if getattr(args, "button", None):
        action_args["button"] = args.button
    if getattr(args, "click_count", None):
        action_args["click_count"] = args.click_count
    if getattr(args, "notches_x", None):
        action_args["notches_x"] = args.notches_x
    if getattr(args, "notches_y", None):
        action_args["notches_y"] = args.notches_y
    if getattr(args, "from_", None):
        action_args["from"] = args.from_
    if getattr(args, "to", None):
        action_args["to"] = args.to
    if getattr(args, "hwnd", None) is not None:
        action_args["hwnd"] = args.hwnd
    if getattr(args, "token", None):
        action_args["token"] = args.token
    if getattr(args, "value", None) is not None:
        action_args["value"] = args.value

    try:
        resp = sess.session_call("act", action_args, timeout_sec=30.0)
    except sess.SessionError as e:
        return _handle_session_error(e)
    except PipeError as e:
        return _handle_pipe_error(e)
    if not resp.get("ok"):
        err = resp["error"]
        status = "refused" if err["code"] in REFUSAL_CODES else "error"
        return _emit(_envelope(_session_id(), False, status, error=err))
    result = resp["result"]
    status = result.get("status", "ok")
    ok = status in ("ok", "dry_run", "refused")
    return _emit(_envelope(_session_id(), ok, status, result=result))


def _session_id() -> Optional[str]:
    try:
        return sess.read_state().get("session_id")
    except Exception:
        return None


# ----------------------------------------------------------------------
# Argument parsing
# ----------------------------------------------------------------------


def _add_act_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dry-run", action="store_true",
                        help="validate the proposal without dispatching input")
    parser.add_argument("--max-age-ms", type=int, default=None,
                        help="observation age limit in ms (engine default: 500)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wcu",
        description="Direct-agent Windows computer-use CLI.",
    )
    parser.add_argument("--version", action="version", version=f"wcu {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="command")

    sub.add_parser("help", help="show the command overview").set_defaults(func=cmd_help)

    sub.add_parser("capabilities", help="engine and desktop capabilities").set_defaults(func=cmd_capabilities)

    p_session = sub.add_parser("session", help="session lifecycle")
    session_sub = p_session.add_subparsers(dest="session_command", metavar="action")
    p_start = session_sub.add_parser("start", help="start the persistent session")
    p_start.add_argument("--engine", default=None,
                         help=f"engine binary path (default: auto-detect)")
    p_start.set_defaults(func=cmd_session_start)
    session_sub.add_parser("status", help="show session state").set_defaults(func=cmd_session_status)
    session_sub.add_parser("stop", help="stop the session and clean up").set_defaults(func=cmd_session_stop)

    p_windows = sub.add_parser("windows", help="list visible windows")
    p_windows.add_argument("--filter", default=None, help="title substring filter")
    p_windows.set_defaults(func=cmd_windows)

    sub.add_parser("monitors", help="list display monitors").set_defaults(func=cmd_monitors)

    p_attach = sub.add_parser("attach", help="attach capture to a window")
    p_attach.add_argument("hwnd", help="window handle (decimal, from `wcu windows`)")
    p_attach.set_defaults(func=cmd_attach)

    p_focus = sub.add_parser("focus", help="focus a window (verified)")
    p_focus.add_argument("hwnd", help="window handle (decimal)")
    p_focus.set_defaults(func=cmd_focus)

    p_switch = sub.add_parser("switch", help="focus + attach + fresh observation")
    p_switch.add_argument("hwnd", help="window handle (decimal)")
    p_switch.set_defaults(func=cmd_switch)

    p_observe = sub.add_parser("observe", help="capture a screenshot (PNG on disk)")
    p_observe.add_argument("--monitor", action="store_true",
                           help="capture the primary monitor instead of the attached window")
    p_observe.add_argument("--crop", nargs=4, type=int, metavar=("X", "Y", "W", "H"),
                           help="write only this region at native resolution; the observation carries crop_offset")
    p_observe.set_defaults(func=cmd_observe)

    p_ocr = sub.add_parser("ocr", help="text-target grounding over the last observation")
    p_ocr.add_argument("--obs", type=int, default=None,
                       help="observation id (default: the most recent)")
    p_ocr.add_argument("--region", nargs=4, type=int, metavar=("X", "Y", "W", "H"),
                       help="OCR only inside this region, in observation pixels")
    p_ocr.add_argument("--match", default=None,
                       help="substring to look for; the best hit is returned as 'matched'")
    p_ocr.add_argument("--min-confidence", type=float, default=0.0)
    p_ocr.set_defaults(func=cmd_ocr)

    p_find = sub.add_parser("find", help="template or colour grounding over the last observation")
    p_find.add_argument("--template", default=None,
                        help="path to a reference image supplied at runtime")
    p_find.add_argument("--color", default=None,
                        help="target colour as #RRGGBB")
    p_find.add_argument("--obs", type=int, default=None)
    p_find.add_argument("--region", nargs=4, type=int, metavar=("X", "Y", "W", "H"))
    p_find.add_argument("--threshold", type=float, default=None,
                        help="template match confidence floor (default 0.8)")
    p_find.add_argument("--tolerance", type=int, default=None,
                        help="per-channel colour tolerance (default 30)")
    p_find.set_defaults(func=cmd_find)

    p_launch = sub.add_parser("launch", help="start a program or URI, optionally waiting for its window")
    p_launch.add_argument("target", help="executable path or URI")
    p_launch.add_argument("args", nargs="*", help="arguments")
    p_launch.add_argument("--window-title", default=None,
                          help="wait until a visible window whose title contains this appears")
    p_launch.add_argument("--timeout", type=float, default=30.0,
                          help="seconds to wait when --window-title is given")
    p_launch.set_defaults(func=cmd_launch)

    p_inspect = sub.add_parser("inspect", help="bounded accessibility inspection")
    p_inspect.add_argument("--max-depth", type=int, default=8)
    p_inspect.add_argument("--max-elements", type=int, default=500)
    p_inspect.add_argument("--question", default=None,
                           help="the question this inspection answers (recorded in history)")
    p_inspect.set_defaults(func=cmd_inspect)

    sub.add_parser("history", help="bounded operation history").set_defaults(func=cmd_history)
    sub.add_parser("cancel", help="cancel a pending operation").set_defaults(func=cmd_cancel)

    p_open = sub.add_parser("open", help="launch a program (host process tool)")
    p_open.add_argument("program", help="program to launch")
    p_open.add_argument("args", nargs="*", help="arguments")
    p_open.set_defaults(func=cmd_open)

    p_act = sub.add_parser("act", help="dispatch a guarded action")
    act_sub = p_act.add_subparsers(dest="action", metavar="action")

    p_click = act_sub.add_parser("click", help="click a target")
    _add_act_common(p_click)
    p_click.add_argument("--observation-id", type=int, required=True)
    p_click.add_argument("--bbox", nargs=4, type=int, metavar=("X", "Y", "W", "H"))
    p_click.add_argument("--point", nargs=2, type=int, metavar=("X", "Y"))
    p_click.add_argument("--button", default="left", choices=["left", "right", "middle"])
    p_click.add_argument("--click-count", type=int, default=1, choices=[1, 2])
    p_click.set_defaults(func=cmd_act)

    p_type = act_sub.add_parser("type", help="type text")
    _add_act_common(p_type)
    p_type.add_argument("--text", required=True)
    p_type.add_argument("--observation-id", type=int, default=None)
    p_type.add_argument("--delay-ms", type=int, default=None)
    p_type.add_argument("--method", choices=["unicode", "paste", "commit"], default="unicode",
                        help="paste uses the clipboard and verifies editor content; commit inserts one edit message and verifies")
    p_type.set_defaults(func=cmd_act)

    p_press = act_sub.add_parser("press", help="press a key chord")
    _add_act_common(p_press)
    p_press.add_argument("--chord", required=True, help="e.g. ctrl+shift+s, enter, alt+f4")
    p_press.add_argument("--observation-id", type=int, default=None)
    p_press.set_defaults(func=cmd_act)

    p_scroll = act_sub.add_parser("scroll", help="scroll wheel notches")
    _add_act_common(p_scroll)
    p_scroll.add_argument("--notches-x", type=int, default=None)
    p_scroll.add_argument("--notches-y", type=int, default=None)
    p_scroll.add_argument("--observation-id", type=int, default=None)
    p_scroll.add_argument("--point", nargs=2, type=int, metavar=("X", "Y"))
    p_scroll.set_defaults(func=cmd_act)

    p_hover = act_sub.add_parser("hover", help="move the pointer without clicking")
    _add_act_common(p_hover)
    p_hover.add_argument("--observation-id", type=int, required=True)
    p_hover.add_argument("--point", nargs=2, type=int, metavar=("X", "Y"))
    p_hover.add_argument("--bbox", nargs=4, type=int, metavar=("X", "Y", "W", "H"))
    p_hover.add_argument("--duration-ms", type=int, default=None)
    p_hover.set_defaults(func=cmd_act)

    p_drag = act_sub.add_parser("drag", help="drag between two points")
    _add_act_common(p_drag)
    p_drag.add_argument("--observation-id", type=int, required=True)
    p_drag.add_argument("--from", dest="from_", nargs=2, type=int, metavar=("X", "Y"), required=True)
    p_drag.add_argument("--to", nargs=2, type=int, metavar=("X", "Y"), required=True)
    p_drag.add_argument("--steps", type=int, default=None)
    p_drag.add_argument("--duration-ms", type=int, default=None)
    p_drag.set_defaults(func=cmd_act)

    p_act_focus = act_sub.add_parser("focus", help="focus a window (semantic action)")
    _add_act_common(p_act_focus)
    p_act_focus.add_argument("--hwnd", required=True)
    p_act_focus.set_defaults(func=cmd_act)

    p_invoke = act_sub.add_parser("invoke", help="invoke a UIA element")
    _add_act_common(p_invoke)
    p_invoke.add_argument("--token", required=True)
    p_invoke.set_defaults(func=cmd_act)

    p_setval = act_sub.add_parser("set-value", help="set a UIA element value")
    _add_act_common(p_setval)
    p_setval.add_argument("--token", required=True)
    p_setval.add_argument("--value", default=None)
    p_setval.set_defaults(func=cmd_act)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return USAGE_EXIT
    try:
        return args.func(args)
    except sess.SessionError as e:
        return _handle_session_error(e)
    except PipeError as e:
        return _handle_pipe_error(e)
    except BrokenPipeError:
        return _handle_pipe_error(PipeError("broken_pipe", "Lost connection to the session"))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
