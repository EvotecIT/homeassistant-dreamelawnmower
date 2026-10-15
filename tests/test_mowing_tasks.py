"""Contract tests for mower-native task requests."""

from __future__ import annotations

import pytest

from dreame_lawn_mower_client.mowing_tasks import (
    MOWING_TASK_EDGE_PATROL,
    MOWING_TASK_POINT_PATROL,
    MowingTaskResponseError,
    build_edge_mowing_request,
    build_edge_patrol_request,
    build_maintenance_point_request,
    build_point_patrol_request,
    build_spot_mowing_request,
    build_zone_mowing_request,
    ensure_mowing_task_succeeded,
)


def test_zone_mowing_request_uses_saved_region_ids() -> None:
    assert build_zone_mowing_request([1, 3]) == {
        "m": "a",
        "p": 0,
        "o": 102,
        "d": {"region": [1, 3]},
    }


def test_spot_mowing_request_uses_saved_area_ids() -> None:
    assert build_spot_mowing_request([2, 4]) == {
        "m": "a",
        "p": 0,
        "o": 103,
        "d": {"area": [2, 4]},
    }


def test_maintenance_point_request_uses_configured_point_id() -> None:
    assert build_maintenance_point_request([301]) == {
        "m": "a",
        "p": 0,
        "o": 109,
        "d": {"point": [301]},
    }


@pytest.mark.parametrize("point_ids", [[], [0], [-1], [True], [1, 2, 3, 4, 5, 6]])
def test_maintenance_point_request_rejects_invalid_ids(
    point_ids: list[int],
) -> None:
    with pytest.raises(ValueError, match="(?i)maintenance point"):
        build_maintenance_point_request(point_ids)


def test_point_patrol_request_uses_patrol_point_ids() -> None:
    assert MOWING_TASK_POINT_PATROL == 107
    assert build_point_patrol_request([1, 2, 3]) == {
        "m": "a",
        "p": 0,
        "o": 107,
        "d": {"point": [1, 2, 3]},
    }


@pytest.mark.parametrize("point_ids", [[], [0], [-1], [True], ["x"]])
def test_point_patrol_request_rejects_invalid_ids(point_ids: list[int]) -> None:
    with pytest.raises(ValueError, match="(?i)patrol point"):
        build_point_patrol_request(point_ids)


def test_edge_patrol_request_uses_contour_id_pairs() -> None:
    assert MOWING_TASK_EDGE_PATROL == 108
    assert build_edge_patrol_request([[1, 0], ("2", "1")]) == {
        "m": "a",
        "p": 0,
        "o": 108,
        "d": {"edge": [[1, 0], [2, 1]]},
    }


def test_edge_patrol_request_shares_edge_mowing_contour_shape() -> None:
    assert (
        build_edge_patrol_request([[1, 0]])["d"]
        == build_edge_mowing_request([[1, 0]])["d"]
    )


@pytest.mark.parametrize("contour_ids", [[], [[1]]])
def test_edge_patrol_request_rejects_invalid_pairs(
    contour_ids: list[list[int]],
) -> None:
    with pytest.raises(ValueError, match="(?i)contour id"):
        build_edge_patrol_request(contour_ids)


@pytest.mark.parametrize("zone_ids", [[], [0], [-1], [True]])
def test_zone_mowing_request_rejects_invalid_ids(zone_ids: list[int]) -> None:
    with pytest.raises(ValueError, match="(?i)zone id"):
        build_zone_mowing_request(zone_ids)


def test_mowing_task_response_requires_explicit_vendor_success() -> None:
    response = {"m": "r", "r": 0, "d": {}}

    assert ensure_mowing_task_succeeded(response, task_name="zone mowing") is response

    with pytest.raises(MowingTaskResponseError, match="did not acknowledge"):
        ensure_mowing_task_succeeded(None, task_name="zone mowing")
    with pytest.raises(MowingTaskResponseError, match="result 5"):
        ensure_mowing_task_succeeded(
            {"m": "r", "r": 5, "d": {"reason": "busy"}},
            task_name="zone mowing",
        )
    with pytest.raises(MowingTaskResponseError, match="result 9"):
        ensure_mowing_task_succeeded(
            {"m": "r", "r": 0, "d": {"r": 9}},
            task_name="zone mowing",
        )
