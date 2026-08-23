"""Regression tests for mower task and physical-state interpretation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device_status import (
    DreameMowerDeviceStatus,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device_types import (
    DreameMowerProperty,
    DreameMowerState,
    DreameMowerStatus,
    DreameMowerTaskStatus,
)

_EXPLICIT_ACTIVE_TASK_STATUSES = frozenset(
    {
        DreameMowerStatus.PAUSED,
        DreameMowerStatus.CLEANING,
        DreameMowerStatus.PART_CLEANING,
        DreameMowerStatus.FOLLOW_WALL,
        DreameMowerStatus.REMOTE_CONTROL,
        DreameMowerStatus.SEGMENT_CLEANING,
        DreameMowerStatus.ZONE_CLEANING,
        DreameMowerStatus.SPOT_CLEANING,
        DreameMowerStatus.FAST_MAPPING,
        DreameMowerStatus.CRUISING_PATH,
        DreameMowerStatus.CRUISING_POINT,
        DreameMowerStatus.SUMMON_CLEAN,
        DreameMowerStatus.SHORTCUT,
        DreameMowerStatus.PERSON_FOLLOW,
    }
)
_UNKNOWN_TASK_INACTIVE_STATUSES = {
    DreameMowerStatus.IDLE,
    DreameMowerStatus.STANDBY,
}


def _status(
    *,
    status: DreameMowerStatus,
    state: DreameMowerState,
    task_status: DreameMowerTaskStatus | None,
) -> DreameMowerDeviceStatus:
    properties = {
        DreameMowerProperty.STATUS: status.value,
        DreameMowerProperty.STATE: state.value,
        DreameMowerProperty.TASK_STATUS: (
            None if task_status is None else task_status.value
        ),
        DreameMowerProperty.CLEANING_PAUSED: 0,
        # A3 firmware can report an unsupported zero while the charging period
        # is active, so station state cannot be inferred from this property.
        DreameMowerProperty.CHARGING_STATUS: 0,
    }
    device = SimpleNamespace(
        capability=SimpleNamespace(
            auto_charging=False,
            cruising=False,
            new_state=True,
        ),
        device_connected=True,
        get_property=lambda prop: properties.get(prop),
    )
    return DreameMowerDeviceStatus(device)


@pytest.mark.parametrize(
    "reported_status",
    [DreameMowerStatus.IDLE, DreameMowerStatus.STANDBY],
)
def test_unknown_task_does_not_turn_charging_period_standby_into_pause(
    reported_status: DreameMowerStatus,
) -> None:
    status = _status(
        status=reported_status,
        state=DreameMowerState.IDLE,
        task_status=None,
    )

    assert status.task_status is DreameMowerTaskStatus.UNKNOWN
    assert status.started is False
    assert status.paused is False
    assert status.state is DreameMowerState.IDLE


@pytest.mark.parametrize("reported_status", list(DreameMowerStatus))
def test_unknown_task_requires_definitive_active_status(
    reported_status: DreameMowerStatus,
) -> None:
    status = _status(
        status=reported_status,
        state=DreameMowerState.IDLE,
        task_status=None,
    )

    assert status.started is (
        status.status not in _UNKNOWN_TASK_INACTIVE_STATUSES
    )


@pytest.mark.parametrize(
    "reported_status",
    sorted(_EXPLICIT_ACTIVE_TASK_STATUSES - {DreameMowerStatus.PAUSED}),
)
def test_unknown_task_keeps_explicit_running_status_active(
    reported_status: DreameMowerStatus,
) -> None:
    status = _status(
        status=reported_status,
        state=DreameMowerState.MOWING,
        task_status=None,
    )

    assert status.started is True
    assert status.running is True
    assert status.paused is False
    assert status.state is DreameMowerState.MOWING


def test_unknown_task_keeps_returning_active_and_separately_identified() -> None:
    status = _status(
        status=DreameMowerStatus.BACK_HOME,
        state=DreameMowerState.RETURNING,
        task_status=None,
    )

    assert status.started is True
    assert status.returning is True
    assert status.running is True


def test_unknown_task_keeps_explicit_paused_state_active() -> None:
    status = _status(
        status=DreameMowerStatus.PAUSED,
        state=DreameMowerState.PAUSED,
        task_status=None,
    )

    assert status.started is True
    assert status.paused is True
    assert status.state is DreameMowerState.PAUSED


def test_unknown_task_keeps_sleeping_interruption_active_and_paused() -> None:
    status = _status(
        status=DreameMowerStatus.SLEEPING,
        state=DreameMowerState.IDLE,
        task_status=None,
    )

    assert status.started is True
    assert status.running is False
    assert status.paused is True


def test_explicit_paused_task_remains_active_and_paused() -> None:
    status = _status(
        status=DreameMowerStatus.IDLE,
        state=DreameMowerState.IDLE,
        task_status=DreameMowerTaskStatus.AUTO_CLEANING_PAUSED,
    )

    assert status.started is True
    assert status.paused is True
    assert status.state is DreameMowerState.PAUSED
