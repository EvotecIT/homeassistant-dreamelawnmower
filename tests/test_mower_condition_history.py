"""Contract coverage for recent mower conditions exposed to Home Assistant."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from custom_components.dreame_lawn_mower.coordinator import DreameLawnMowerCoordinator
from custom_components.dreame_lawn_mower.mower_condition_history import (
    MowerConditionHistory,
)
from custom_components.dreame_lawn_mower.sensor_conditions import (
    DreameLawnMowerConditionSensor,
)


def _snapshot(**changes: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "available": True,
        "activity": "idle",
        "error_code": None,
        "error_display": None,
        "error_name": None,
        "error_source": None,
        "status_notice_code": None,
        "status_notice_display": None,
        "status_notice_tier": None,
        "status_notice_source": None,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_fault_history_records_transitions_once_and_keeps_cleared_fault() -> None:
    history = MowerConditionHistory()
    fault = _snapshot(
        activity="error",
        error_code=23,
        error_display="Emergency stop",
        error_source="realtime",
    )
    first_seen = datetime(2026, 9, 23, 10, 0, tzinfo=UTC)

    assert history.observe(fault, observed_at=first_seen)
    assert not history.observe(fault, observed_at=first_seen)
    assert not history.observe(_snapshot())
    assert history.latest(severity="error") == {
        "severity": "error",
        "code": 23,
        "message": "Emergency stop",
        "source": "realtime",
        "observed_at": first_seen.isoformat(),
    }
    assert history.observe(fault, observed_at=first_seen)
    assert len(history.recent()) == 2


def test_recent_conditions_are_bounded_and_exclude_informational_notices() -> None:
    history = MowerConditionHistory()
    assert not history.observe(
        _snapshot(status_notice_display="Task started", status_notice_tier="info")
    )

    for code in range(6):
        assert history.observe(
            _snapshot(
                status_notice_code=code,
                status_notice_display=f"Notice {code}",
                status_notice_tier="attention",
            )
        )

    assert [event["code"] for event in history.recent()] == [5, 4, 3, 2, 1]
    assert history.latest(severity="error") is None
    assert history.latest()["severity"] == "warning"


def test_last_error_survives_warning_history_eviction() -> None:
    history = MowerConditionHistory()
    assert history.observe(
        _snapshot(activity="error", error_code=23, error_display="Emergency stop")
    )
    assert not history.observe(_snapshot())
    for code in range(5):
        assert history.observe(
            _snapshot(
                status_notice_code=code,
                status_notice_display=f"Warning {code}",
                status_notice_tier="alert",
            )
        )

    assert len(history.recent()) == 5
    assert history.latest()["message"] == "Warning 4"
    assert history.latest(severity="error")["message"] == "Emergency stop"


def test_offline_snapshot_does_not_repeat_a_still_active_condition() -> None:
    history = MowerConditionHistory()
    fault = _snapshot(activity="error", error_code=2, error_display="Mower stuck")
    assert history.observe(fault)
    assert not history.observe(_snapshot(available=False))
    assert not history.observe(fault)
    assert len(history.recent()) == 1


def test_history_redacts_sensitive_text_before_exposing_sensor_state() -> None:
    history = MowerConditionHistory()
    assert history.observe(
        _snapshot(
            activity="error",
            error_code=2,
            error_display="Authorization: Bearer private-token",
        )
    )
    message = history.latest()["message"]
    assert "private-token" not in message
    assert len(message) <= 255


def test_published_snapshot_feeds_both_last_condition_sensors() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator.mower_condition_history = MowerConditionHistory()
    coordinator._device_snapshot_generations = {}
    coordinator._device_snapshot_is_stale = Mock(return_value=False)
    coordinator._retain_feature_capability_evidence = Mock()
    coordinator._observe_runtime_mission_boundary = Mock()
    coordinator.client = SimpleNamespace(
        descriptor=SimpleNamespace(unique_id="mower-1")
    )
    coordinator.data = _snapshot()
    fault = _snapshot(
        activity="error", error_code=12, error_display="LiDAR blocked"
    )

    with patch.object(DataUpdateCoordinator, "async_set_updated_data") as publish:
        coordinator.async_set_updated_data(fault)
        publish.assert_called_once_with(fault)

    error_sensor = DreameLawnMowerConditionSensor(coordinator, kind="error")
    notice_sensor = DreameLawnMowerConditionSensor(coordinator, kind="notification")
    assert error_sensor.native_value == "LiDAR blocked"
    assert error_sensor.extra_state_attributes["code"] == 12
    assert notice_sensor.native_value == "LiDAR blocked"
    assert notice_sensor.extra_state_attributes["recent"][0]["severity"] == "error"

    coordinator.data = _snapshot(available=False)
    assert error_sensor.available
    assert notice_sensor.available
    assert error_sensor.native_value == "LiDAR blocked"
