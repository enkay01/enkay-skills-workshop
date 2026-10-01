"""Guard the observation max-age defaults that a CLI round trip depends on.

The engine refuses a pointer action whose backing observation is older than
`max_age_ms`, defaulting to 500 ms. That is shorter than one `wcu` process
round trip plus an OCR pass over the frame, so a click grounded from
`wcu ocr` was refused as stale every time. The fix was applied at three
layers on the original branch and was lost in the merge, with no test to
catch it - hence this file.

Pointer actions default to 30 s; keyboard actions bind to window identity
and foreground rather than to a point and keep the engine default.
"""

from __future__ import annotations

import argparse
import inspect
import os
import sys

import pytest

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_REPO, "client"))
sys.path.insert(0, os.path.join(_REPO, "cli"))

from wcu_client import WcuClient  # noqa: E402

POINTER_ACTIONS = ("click", "hover", "drag", "scroll")
KEY_ACTIONS = ("type_text", "press_key")


@pytest.mark.parametrize("action", POINTER_ACTIONS)
def test_pointer_client_default_is_30s(action):
    """A pointer action must not inherit the engine's 500 ms default."""
    param = inspect.signature(getattr(WcuClient, action)).parameters["max_age_ms"]
    assert param.default == 30000, (
        f"{action}.max_age_ms defaults to {param.default}; "
        "a grounded click would be refused as stale before it is dispatched"
    )


@pytest.mark.parametrize("action", KEY_ACTIONS)
def test_key_client_default_unchanged(action):
    """Keyboard actions bind to window identity, not a point, and stay at 500."""
    param = inspect.signature(getattr(WcuClient, action)).parameters["max_age_ms"]
    assert param.default == 500


@pytest.mark.parametrize("action", POINTER_ACTIONS)
def test_pointer_server_default_is_30s(action):
    """The server passes max_age explicitly, so its default is the binding one."""
    from wcu.session_server import _default_max_age

    assert _default_max_age(action) == 30000


@pytest.mark.parametrize("action", KEY_ACTIONS)
def test_key_server_default_unchanged(action):
    from wcu.session_server import _default_max_age

    assert _default_max_age(action) == 500


def test_unknown_action_falls_back_to_engine_default():
    """A newly added action gets the conservative default, never silently long."""
    from wcu.session_server import _default_max_age

    assert _default_max_age("something_new") == 500


def test_cli_does_not_preempt_the_server_default():
    """`--max-age-ms` must default to None so the per-action default applies."""
    from wcu.cli import _add_act_common, build_parser

    parser = build_parser()
    assert parser.parse_args(["act", "click", "--observation-id", "1"]).max_age_ms is None

    # The help must state the real default, or the next reader reverts it.
    bare = argparse.ArgumentParser()
    _add_act_common(bare)
    help_text = bare.format_help()
    assert "30000" in help_text
