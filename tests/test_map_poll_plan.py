"""Polling recovery waits for the current-map result and releases its owner."""

from types import SimpleNamespace

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_manager import (
    DreameMapMowerMapManager,
)


@pytest.mark.parametrize("found", [False, True])
def test_second_attempt_fallback_depends_on_completed_map_request(found):
    manager = DreameMapMowerMapManager(
        SimpleNamespace(
            dreame_cloud=True,
            cloud=SimpleNamespace(connected=False),
        )
    )
    manager._map_request_count = 1
    manager._connected = False
    manager._map_request_time = 1000
    plan = manager._update_plan()
    current = next(plan)
    assert (current.kind, current.start_time) == ("current", 1000)
    assert manager._update_running
    assert manager._map_request_count == 2
    if not found:
        assert plan.send(False).kind == "object"
    with pytest.raises(StopIteration):
        plan.send(found)
    assert manager._ready
    assert not manager._update_running


def test_closing_suspended_poll_releases_running_flag():
    manager = DreameMapMowerMapManager(
        SimpleNamespace(
            dreame_cloud=True,
            cloud=SimpleNamespace(connected=False),
        )
    )
    manager._need_map_request = True
    plan = manager._update_plan()
    assert next(plan).kind == "current"
    assert manager._update_running
    plan.close()
    assert not manager._update_running


def test_sixth_attempt_clears_request_without_another_rpc():
    manager = DreameMapMowerMapManager(
        SimpleNamespace(
            dreame_cloud=True,
            cloud=SimpleNamespace(connected=False),
        )
    )
    manager._need_map_request = True
    manager._map_request_time = 1000
    manager._map_request_count = 5
    manager._connected = False
    assert list(manager._update_plan()) == []
    assert manager._map_request_time is None
    assert not manager._need_map_request
    assert not manager._update_running
