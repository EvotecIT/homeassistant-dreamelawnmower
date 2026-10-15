"""Contract tests for mower-native point and edge patrol controls."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.app_map_metadata import (  # noqa: E501
    current_app_map_patrol_point_ids,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.maintenance_point_records import (  # noqa: E501
    app_map_maintenance_point_ids,
    app_map_patrol_point_ids,
)
from custom_components.dreame_lawn_mower.lawn_mower import DreameLawnMower
from dreame_lawn_mower_client.exceptions import DreameLawnMowerCommandRejectedError

from .test_current_map_controls import _batch_device_data, _vector_map_details
from .test_vector_map import _client as _new_client


@pytest.fixture
def client():
    """Close the real client's owned HTTP resources after each command test."""
    value = _new_client()
    value.async_get_current_app_map_index = AsyncMock(return_value=0)
    try:
        yield value
    finally:
        asyncio.run(value.async_close())


def _idle_snapshot(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "activity": "idle",
        "state": "idle",
        "mowing_session_active": False,
        "raw_attributes": {},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _patrol_app_maps(*, patrol_point_ids: list[int] | None = None) -> dict:
    return {
        "map_list_valid": True,
        "current_map_index": 0,
        "maps": [
            {
                "idx": 0,
                "current": True,
                "available": True,
                "created": True,
                "hash_match": True,
                "summary": {
                    "maintenance_point_ids": [301],
                    "patrol_point_ids": (
                        [1, 2, 3] if patrol_point_ids is None else patrol_point_ids
                    ),
                },
            },
            {
                "idx": 1,
                "current": False,
                "available": True,
                "summary": {"patrol_point_ids": [99]},
            },
        ],
    }


def test_patrol_point_records_are_separate_from_maintenance_points() -> None:
    entries = [
        {"id": 301, "param": {}, "point": [10, 20], "time": 1, "type": 1},
        {"id": 1, "param": {}, "point": [30, 40], "time": 2, "type": 2},
        {"id": 2, "param": {}, "point": [50, 60, 0], "time": 3, "type": 2},
        {"id": 2, "param": {}, "point": [50, 60, 0], "time": 3, "type": 2},
        {"id": 0, "param": {}, "point": [1, 2], "time": 4, "type": 2},
        {"id": 3, "type": 2},
    ]

    assert app_map_patrol_point_ids(entries) == [1, 2]
    assert app_map_maintenance_point_ids(entries) == [301]


def test_current_patrol_point_ids_use_only_the_active_available_map() -> None:
    assert current_app_map_patrol_point_ids(_patrol_app_maps()) == [1, 2, 3]

    app_maps = _patrol_app_maps()
    app_maps["maps"][0]["available"] = False
    assert current_app_map_patrol_point_ids(app_maps) == []

    app_maps = _patrol_app_maps()
    app_maps["current_map_index"] = None
    assert current_app_map_patrol_point_ids(app_maps) == []
    assert current_app_map_patrol_point_ids(None) == []


def test_client_point_patrol_sends_explicit_point_ids(client) -> None:
    client.async_refresh_authoritative_snapshot = AsyncMock(
        return_value=_idle_snapshot(activity="docked", state="charging")
    )
    client.async_get_app_maps = AsyncMock(return_value=_patrol_app_maps())
    client._async_call_mowing_task = AsyncMock(  # type: ignore[method-assign]
        return_value={"m": "r", "r": 0}
    )

    result = asyncio.run(client.async_start_point_patrol([1, 2, 3]))

    assert result == {"m": "r", "r": 0}
    client.async_get_app_maps.assert_awaited_once_with(include_objects=False)
    client.async_get_current_app_map_index.assert_awaited_once_with()
    client._async_call_mowing_task.assert_awaited_once_with(
        {"m": "a", "p": 0, "o": 107, "d": {"point": [1, 2, 3]}},
        task_name="point patrol",
    )


def test_client_point_patrol_defaults_to_active_map_patrol_points(client) -> None:
    client.async_refresh_authoritative_snapshot = AsyncMock(
        return_value=_idle_snapshot()
    )
    client.async_get_app_maps = AsyncMock(  # type: ignore[method-assign]
        return_value=_patrol_app_maps()
    )
    client._async_call_mowing_task = AsyncMock(  # type: ignore[method-assign]
        return_value={"m": "r", "r": 0}
    )

    asyncio.run(client.async_start_point_patrol())

    client.async_get_app_maps.assert_awaited_once_with(include_objects=False)
    client.async_get_current_app_map_index.assert_awaited_once_with()
    client._async_call_mowing_task.assert_awaited_once_with(
        {"m": "a", "p": 0, "o": 107, "d": {"point": [1, 2, 3]}},
        task_name="point patrol",
    )


def test_client_point_patrol_requires_patrol_points_when_ids_are_omitted(
    client,
) -> None:
    client.async_refresh_authoritative_snapshot = AsyncMock(
        return_value=_idle_snapshot()
    )
    client.async_get_app_maps = AsyncMock(  # type: ignore[method-assign]
        return_value=_patrol_app_maps(patrol_point_ids=[])
    )
    client._async_call_mowing_task = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(DreameLawnMowerCommandRejectedError, match="patrol point"):
        asyncio.run(client.async_start_point_patrol())

    client._async_call_mowing_task.assert_not_awaited()


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("async_start_point_patrol", ([1],)),
        ("async_start_edge_patrol", ([[1, 0]],)),
    ],
)
def test_client_patrols_are_blocked_while_mowing(
    client, method: str, args: tuple[object, ...]
) -> None:
    client.async_get_app_maps = AsyncMock(return_value=_patrol_app_maps())
    client.async_refresh_authoritative_snapshot = AsyncMock(
        return_value=_idle_snapshot(
            activity="mowing", state="mowing", mowing_session_active=True
        )
    )
    client._async_call_mowing_task = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(DreameLawnMowerCommandRejectedError, match="mower is active"):
        asyncio.run(getattr(client, method)(*args))

    client._async_call_mowing_task.assert_not_awaited()


def test_client_edge_patrol_sends_contour_pairs(client) -> None:
    client.async_refresh_authoritative_snapshot = AsyncMock(
        return_value=_idle_snapshot(activity="docked", state="charging")
    )
    client._async_call_mowing_task = AsyncMock(  # type: ignore[method-assign]
        return_value={"m": "r", "r": 0}
    )

    asyncio.run(client.async_start_edge_patrol([[1, 0]]))

    client._async_call_mowing_task.assert_awaited_once_with(
        {"m": "a", "p": 0, "o": 108, "d": {"edge": [[1, 0]]}},
        task_name="edge patrol",
    )


def _entity(**coordinator_overrides: object) -> DreameLawnMower:
    values: dict[str, object] = {
        "client": SimpleNamespace(
            async_start_point_patrol=AsyncMock(),
            async_start_edge_patrol=AsyncMock(),
        ),
        "app_maps": _patrol_app_maps(),
        "batch_device_data": _batch_device_data(),
        "vector_map_details": _vector_map_details(),
        "selected_map_index": None,
        "selected_mowing_action": "all_area",
        "async_request_refresh": AsyncMock(),
    }
    values.update(coordinator_overrides)
    entity = object.__new__(DreameLawnMower)
    entity.coordinator = SimpleNamespace(**values)
    return entity


def test_point_patrol_service_validates_explicit_ids_against_active_map() -> None:
    entity = _entity()

    asyncio.run(entity.async_start_point_patrol([3, "1"]))  # type: ignore[list-item]

    entity.coordinator.client.async_start_point_patrol.assert_awaited_once_with([3, 1])
    assert entity.coordinator.selected_mowing_action == "all_area"
    entity.coordinator.async_request_refresh.assert_awaited_once()


def test_point_patrol_service_rejects_ids_from_another_map() -> None:
    entity = _entity()

    with pytest.raises(HomeAssistantError, match=r"Patrol point #99"):
        asyncio.run(entity.async_start_point_patrol([99]))

    entity.coordinator.client.async_start_point_patrol.assert_not_called()
    entity.coordinator.async_request_refresh.assert_not_awaited()


def test_point_patrol_service_without_ids_lets_client_use_all_points() -> None:
    entity = _entity()

    asyncio.run(entity.async_start_point_patrol())

    entity.coordinator.client.async_start_point_patrol.assert_awaited_once_with(None)
    entity.coordinator.async_request_refresh.assert_awaited_once()


def test_point_patrol_service_rejects_explicit_empty_selection() -> None:
    entity = _entity()

    with pytest.raises(HomeAssistantError, match="At least one patrol point"):
        asyncio.run(entity.async_start_point_patrol([]))

    entity.coordinator.client.async_start_point_patrol.assert_not_called()
    entity.coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.parametrize("point_ids", [None, [1]])
@pytest.mark.parametrize("flag", ["map_list_valid", "created", "hash_match"])
def test_point_patrol_rejects_unverified_maps_before_dispatch(
    client, point_ids: list[int] | None, flag: str
) -> None:
    app_maps = _patrol_app_maps()
    if flag == "map_list_valid":
        app_maps[flag] = False
    else:
        app_maps["maps"][0][flag] = None
    client.async_refresh_authoritative_snapshot = AsyncMock(
        return_value=_idle_snapshot()
    )
    client.async_get_app_maps = AsyncMock(return_value=app_maps)
    client._async_call_mowing_task = AsyncMock()

    with pytest.raises(DreameLawnMowerCommandRejectedError, match="patrol point"):
        asyncio.run(client.async_start_point_patrol(point_ids))

    client._async_call_mowing_task.assert_not_awaited()


def test_client_point_patrol_rejects_points_outside_fresh_active_map(client) -> None:
    client.async_get_app_maps = AsyncMock(return_value=_patrol_app_maps())
    client._async_call_mowing_task = AsyncMock()

    with pytest.raises(DreameLawnMowerCommandRejectedError, match="Every patrol point"):
        asyncio.run(client.async_start_point_patrol([99]))

    client._async_call_mowing_task.assert_not_awaited()


def test_client_point_patrol_checks_movement_state_after_map_read(client) -> None:
    client.async_get_app_maps = AsyncMock(return_value=_patrol_app_maps())
    client._async_call_mowing_task = AsyncMock()

    async def refresh_snapshot():
        client.async_get_app_maps.assert_awaited_once()
        return _idle_snapshot(activity="mowing", state="mowing")

    client.async_refresh_authoritative_snapshot = AsyncMock(
        side_effect=refresh_snapshot
    )
    with pytest.raises(DreameLawnMowerCommandRejectedError, match="mower is active"):
        asyncio.run(client.async_start_point_patrol([1]))

    client._async_call_mowing_task.assert_not_awaited()


@pytest.mark.parametrize("current_map_index", [1, None])
def test_client_point_patrol_requires_unchanged_current_map(
    client, current_map_index
) -> None:
    client.async_get_app_maps = AsyncMock(return_value=_patrol_app_maps())
    client.async_refresh_authoritative_snapshot = AsyncMock(
        return_value=_idle_snapshot()
    )
    client.async_get_current_app_map_index.return_value = current_map_index
    client._async_call_mowing_task = AsyncMock()

    with pytest.raises(DreameLawnMowerCommandRejectedError, match="map changed"):
        asyncio.run(client.async_start_point_patrol([1]))

    client._async_call_mowing_task.assert_not_awaited()
    client.async_get_current_app_map_index.assert_awaited_once_with()


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("async_start_point_patrol", ([1],)),
        ("async_start_edge_patrol", ([[1, 0]],)),
        ("async_go_to_maintenance_point", (301,)),
    ],
)
def test_client_autonomous_movement_rejects_manual_control(
    client, method, args
) -> None:
    client.async_get_app_maps = AsyncMock(return_value=_patrol_app_maps())
    client.async_refresh_authoritative_snapshot = AsyncMock(
        return_value=_idle_snapshot(
            activity="remote_control", state="remote_control", started=True
        )
    )
    client._async_call_mowing_task = AsyncMock()

    with pytest.raises(DreameLawnMowerCommandRejectedError, match="manual remote"):
        asyncio.run(getattr(client, method)(*args))

    client._async_call_mowing_task.assert_not_awaited()


def test_maintenance_button_guard_rejects_manual_control_without_mowing() -> None:
    from custom_components.dreame_lawn_mower.manual_control import (
        maintenance_point_movement_block_reason,
    )

    reason = maintenance_point_movement_block_reason(
        _idle_snapshot(activity="remote_control", state="remote_control")
    )

    assert reason is not None
    assert "manual remote control" in reason


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("async_start_point_patrol", ([1],)),
        ("async_start_edge_patrol", ([[1, 0]],)),
    ],
)
@pytest.mark.parametrize("response", [None, {"r": 7}, {"r": 0, "d": {"r": 9}}])
def test_client_patrols_require_mower_acknowledgement(
    client, monkeypatch, method, args, response
) -> None:
    from dreame_lawn_mower_client import DreameLawnMowerConnectionError
    from dreame_lawn_mower_client._loader import load_internal_module

    client.async_get_app_maps = AsyncMock(return_value=_patrol_app_maps())
    client.async_refresh_authoritative_snapshot = AsyncMock(
        return_value=_idle_snapshot()
    )
    transport = AsyncMock(return_value=response)
    monkeypatch.setattr(
        load_internal_module("client_app_reads"), "async_command_app_action", transport
    )

    with pytest.raises(DreameLawnMowerConnectionError, match="acknowledge|rejected"):
        asyncio.run(getattr(client, method)(*args))

    transport.assert_awaited_once()


def test_edge_patrol_service_uses_explicit_contours() -> None:
    entity = _entity()

    asyncio.run(entity.async_start_edge_patrol([[1, 0], ("3", "0")]))  # type: ignore[list-item]

    entity.coordinator.client.async_start_edge_patrol.assert_awaited_once_with(
        [[1, 0], [3, 0]]
    )
    assert entity.coordinator.selected_mowing_action == "all_area"
    entity.coordinator.async_request_refresh.assert_awaited_once()


def test_edge_patrol_service_rejects_unknown_active_map_contour() -> None:
    entity = _entity()

    with pytest.raises(HomeAssistantError, match=r"Edge contour \(7, 1\)"):
        asyncio.run(entity.async_start_edge_patrol([[7, 1]]))

    entity.coordinator.client.async_start_edge_patrol.assert_not_called()


def test_edge_patrol_service_requires_valid_pairs() -> None:
    entity = _entity()

    with pytest.raises(HomeAssistantError, match="two-item list"):
        asyncio.run(entity.async_start_edge_patrol([[7]]))

    entity.coordinator.client.async_start_edge_patrol.assert_not_called()


def test_patrol_services_are_declared_for_home_assistant() -> None:
    from pathlib import Path

    import yaml

    services = yaml.safe_load(
        (
            Path(__file__).parents[1]
            / "custom_components/dreame_lawn_mower/services.yaml"
        ).read_text()
    )

    assert services["start_point_patrol"]["fields"]["point_ids"]["required"] is False
    assert services["start_edge_patrol"]["fields"]["contour_ids"]["required"] is True
