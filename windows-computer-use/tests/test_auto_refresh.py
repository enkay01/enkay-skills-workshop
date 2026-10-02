"""Tests for auto-refresh, stale observation recovery, and coordinate reporting."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch
import pytest

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_REPO, "client"))
sys.path.insert(0, os.path.join(_REPO, "cli"))

from wcu_client import WcuError
from wcu.session_server import SessionServer


def _make_server():
    server = SessionServer("test_session", "dummy_engine.exe")
    server.client = MagicMock()
    server.client.pid = 9999
    return server


@pytest.fixture(autouse=True)
def mock_pid_alive():
    with patch("wcu.session_server._pid_alive", return_value=True):
        yield


def test_auto_refresh_success_on_stale():
    server = _make_server()

    # Pre-populate observation 10
    obs10 = {
        "observation_id": 10,
        "width": 1920,
        "height": 1080,
        "dpi": 96,
        "geometry_epoch": 1,
        "foreground_epoch": 1,
        "window_identity": {"hwnd": "1234", "pid": 5678},
        "geometry": {
            "dpi": 96,
            "scale_factor": 1.0,
            "physical_bounds": {"x": 0, "y": 0, "w": 1920, "h": 1080},
            "logical_bounds": {"x": 0, "y": 0, "w": 1920, "h": 1080},
            "image_dimensions": {"w": 1920, "h": 1080},
        },
    }
    server._observation_meta[10] = obs10
    server._last_observation_meta = obs10

    # Fresh observation 11 that will be captured upon staleness
    obs11 = {
        "observation_id": 11,
        "width": 1920,
        "height": 1080,
        "dpi": 96,
        "geometry_epoch": 1,
        "foreground_epoch": 1,
        "window_identity": {"hwnd": "1234", "pid": 5678},
        "geometry": {
            "dpi": 96,
            "scale_factor": 1.0,
            "physical_bounds": {"x": 0, "y": 0, "w": 1920, "h": 1080},
            "logical_bounds": {"x": 0, "y": 0, "w": 1920, "h": 1080},
            "image_dimensions": {"w": 1920, "h": 1080},
        },
    }

    # First click call raises stale_observation; second click call succeeds
    server.client.click.side_effect = [
        WcuError("stale_observation", "Observation 10 is too old"),
        {"status": "clicked"},
    ]

    server._observe_and_save = MagicMock(return_value=obs11)

    res = server.op_act({
        "action": "click",
        "observation_id": 10,
        "point": [500, 500],
        "auto_refresh": True,
    })

    assert res["status"] == "ok"
    assert res["auto_refreshed"] is True
    assert res["observation_id"] == 11
    assert res["stale_observation_id"] == 10
    assert res["resolved_physical_point"] == [500, 500]
    assert res["dispatch"]["status"] == "clicked"
    assert server.client.click.call_count == 2
    # Verify second dispatch used the fresh observation id 11
    assert server.client.click.call_args_list[1][1]["observation_id"] == 11


def test_auto_refresh_refuses_when_geometry_epoch_changed():
    server = _make_server()

    obs10 = {
        "observation_id": 10,
        "width": 1920,
        "height": 1080,
        "geometry_epoch": 1,
        "foreground_epoch": 1,
        "window_identity": {"hwnd": "1234", "pid": 5678},
        "geometry": {"scale_factor": 1.0, "image_dimensions": {"w": 1920, "h": 1080}},
    }
    server._observation_meta[10] = obs10

    # Fresh observation where window moved / resized (geometry_epoch 2)
    obs11 = {
        "observation_id": 11,
        "width": 1920,
        "height": 1080,
        "geometry_epoch": 2,
        "foreground_epoch": 1,
        "window_identity": {"hwnd": "1234", "pid": 5678},
        "geometry": {"scale_factor": 1.0, "image_dimensions": {"w": 1920, "h": 1080}},
    }

    server.client.click.side_effect = WcuError("stale_observation", "stale")
    server._observe_and_save = MagicMock(return_value=obs11)

    with pytest.raises(WcuError) as exc_info:
        server.op_act({
            "action": "click",
            "observation_id": 10,
            "point": [100, 100],
            "auto_refresh": True,
        })

    assert exc_info.value.code == "window_moved"
    assert "geometry changed" in exc_info.value.message


def test_auto_refresh_refuses_when_foreground_epoch_changed():
    server = _make_server()

    obs10 = {
        "observation_id": 10,
        "width": 1920,
        "height": 1080,
        "geometry_epoch": 1,
        "foreground_epoch": 1,
        "window_identity": {"hwnd": "1234", "pid": 5678},
        "geometry": {"scale_factor": 1.0, "image_dimensions": {"w": 1920, "h": 1080}},
    }
    server._observation_meta[10] = obs10

    # Fresh observation where window lost foreground (foreground_epoch 2)
    obs11 = {
        "observation_id": 11,
        "width": 1920,
        "height": 1080,
        "geometry_epoch": 1,
        "foreground_epoch": 2,
        "window_identity": {"hwnd": "1234", "pid": 5678},
        "geometry": {"scale_factor": 1.0, "image_dimensions": {"w": 1920, "h": 1080}},
    }

    server.client.click.side_effect = WcuError("stale_observation", "stale")
    server._observe_and_save = MagicMock(return_value=obs11)

    with pytest.raises(WcuError) as exc_info:
        server.op_act({
            "action": "click",
            "observation_id": 10,
            "point": [100, 100],
            "auto_refresh": True,
        })

    assert exc_info.value.code == "foreground_lost"
    assert "lost foreground" in exc_info.value.message


def test_without_auto_refresh_stale_observation_raises_refusal():
    server = _make_server()

    obs10 = {
        "observation_id": 10,
        "width": 1920,
        "height": 1080,
        "geometry": {"scale_factor": 1.0, "image_dimensions": {"w": 1920, "h": 1080}},
    }
    server._observation_meta[10] = obs10

    fresh_obs = {"observation_id": 11, "width": 1920, "height": 1080}
    server.client.click.side_effect = WcuError("stale_observation", "stale")
    server._observe_and_save = MagicMock(return_value=fresh_obs)

    with pytest.raises(WcuError) as exc_info:
        server.op_act({
            "action": "click",
            "observation_id": 10,
            "point": [100, 100],
            "auto_refresh": False,
        })

    assert exc_info.value.code == "stale_observation"
    assert exc_info.value.details["evidence"] == fresh_obs
    assert server.client.click.call_count == 1


def test_coord_space_logical_and_normalized_reported_in_result():
    server = _make_server()

    # Observation with 150% scaling
    obs = {
        "observation_id": 5,
        "width": 2520,
        "height": 1680,
        "dpi": 144,
        "geometry": {
            "dpi": 144,
            "scale_factor": 1.5,
            "physical_bounds": {"x": 0, "y": 0, "w": 2520, "h": 1680},
            "logical_bounds": {"x": 0, "y": 0, "w": 1680, "h": 1120},
            "image_dimensions": {"w": 2520, "h": 1680},
        },
    }
    server._observation_meta[5] = obs
    server.client.click.return_value = {"status": "clicked"}
    server._observe_and_save = MagicMock(return_value=obs)

    # 1. Logical coordinate resolution: (1000, 500) -> (1500, 750)
    res_logical = server.op_act({
        "action": "click",
        "observation_id": 5,
        "point": [1000, 500],
        "coord_space": "logical",
    })
    assert res_logical["resolved_physical_point"] == [1500, 750]

    # 2. Normalized 0.0..1.0 unit resolution: (0.5, 0.5) -> (1260, 840)
    res_norm_unit = server.op_act({
        "action": "click",
        "observation_id": 5,
        "point": [0.5, 0.5],
        "coord_space": "normalized",
    })
    assert res_norm_unit["resolved_physical_point"] == [1260, 840]

    # 3. Normalized 0..1000 grid resolution: (500, 500) -> (1260, 840)
    res_norm_grid = server.op_act({
        "action": "click",
        "observation_id": 5,
        "point": [500, 500],
        "coord_space": "normalized",
    })
    assert res_norm_grid["resolved_physical_point"] == [1260, 840]


def test_auto_refresh_succeeds_on_second_retry():
    server = _make_server()

    obs10 = {
        "observation_id": 10,
        "width": 1920,
        "height": 1080,
        "geometry_epoch": 1,
        "foreground_epoch": 1,
        "window_identity": {"hwnd": "1234", "pid": 5678},
        "geometry": {"scale_factor": 1.0, "image_dimensions": {"w": 1920, "h": 1080}},
    }
    obs11 = dict(obs10, observation_id=11)
    obs12 = dict(obs10, observation_id=12)
    server._observation_meta[10] = obs10

    # Initial call fails with stale; retry 1 fails with stale; retry 2 succeeds
    server.client.click.side_effect = [
        WcuError("stale_observation", "stale 10"),
        WcuError("stale_observation", "stale 11"),
        {"status": "clicked"},
    ]
    server._observe_and_save = MagicMock(side_effect=[obs11, obs12])

    res = server.op_act({
        "action": "click",
        "observation_id": 10,
        "point": [100, 100],
        "auto_refresh": True,
    })

    assert res["status"] == "ok"
    assert res["auto_refreshed"] is True
    assert res["observation_id"] == 12
    assert server.client.click.call_count == 3


def test_auto_refresh_refuses_when_window_identity_changed():
    server = _make_server()

    obs10 = {
        "observation_id": 10,
        "width": 1920,
        "height": 1080,
        "geometry_epoch": 1,
        "foreground_epoch": 1,
        "window_identity": {"hwnd": "1234", "pid": 5678},
        "geometry": {"scale_factor": 1.0, "image_dimensions": {"w": 1920, "h": 1080}},
    }
    server._observation_meta[10] = obs10

    # Window HWND changed to another window
    obs11 = dict(obs10, observation_id=11, window_identity={"hwnd": "9999", "pid": 5678})

    server.client.click.side_effect = WcuError("stale_observation", "stale")
    server._observe_and_save = MagicMock(return_value=obs11)

    with pytest.raises(WcuError) as exc_info:
        server.op_act({
            "action": "click",
            "observation_id": 10,
            "point": [100, 100],
            "auto_refresh": True,
        })

    assert exc_info.value.code == "window_moved"
    assert "identity changed" in exc_info.value.message


def test_cli_parsing_coord_space_and_auto_refresh():
    from wcu.cli import build_parser

    parser = build_parser()

    # 1. click with normalized float coords and auto-refresh
    args = parser.parse_args(["act", "click", "--observation-id", "1", "--point", "0.5", "0.5", "--coord-space", "normalized", "--auto-refresh"])
    assert args.coord_space == "normalized"
    assert args.point == [0.5, 0.5]
    assert args.auto_refresh is True

    # 2. retry-if-stale alias
    args_alias = parser.parse_args(["act", "click", "--observation-id", "1", "--point", "100", "200", "--retry-if-stale"])
    assert args_alias.auto_refresh is True

    # 3. hover with logical coords
    args_hover = parser.parse_args(["act", "hover", "--observation-id", "1", "--point", "668", "487", "--coord-space", "logical"])
    assert args_hover.coord_space == "logical"
    assert args_hover.point == [668.0, 487.0]

    # 4. drag with normalized coords
    args_drag = parser.parse_args(["act", "drag", "--observation-id", "1", "--from", "100", "200", "--to", "300", "400", "--coord-space", "normalized"])
    assert args_drag.coord_space == "normalized"
    assert args_drag.from_ == [100.0, 200.0]
    assert args_drag.to == [300.0, 400.0]

    # 5. scroll with coord-space
    args_scroll = parser.parse_args(["act", "scroll", "--point", "500", "500", "--coord-space", "logical"])
    assert args_scroll.coord_space == "logical"
    assert args_scroll.point == [500.0, 500.0]

    # 6. ocr with coord-space
    args_ocr = parser.parse_args(["ocr", "--region", "0.1", "0.2", "0.3", "0.4", "--coord-space", "normalized"])
    assert args_ocr.coord_space == "normalized"
    assert args_ocr.region == [0.1, 0.2, 0.3, 0.4]

    # 7. find with coord-space
    args_find = parser.parse_args(["find", "--color", "#FF0000", "--region", "100", "200", "300", "400", "--coord-space", "logical"])
    assert args_find.coord_space == "logical"
    assert args_find.region == [100.0, 200.0, 300.0, 400.0]

