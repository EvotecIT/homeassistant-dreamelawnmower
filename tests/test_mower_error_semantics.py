"""Regression tests for mower-specific error meanings."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    device_code_semantics,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device import (
    DreameMowerDeviceStatus,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    descriptor_from_cloud_record,
    snapshot_from_device,
)

MowerDeviceCodeTier = device_code_semantics.MowerDeviceCodeTier
mower_device_code_name = device_code_semantics.mower_device_code_name
mower_device_code_tier = device_code_semantics.mower_device_code_tier
mower_fault_active = device_code_semantics.mower_fault_active


class _ErrorDevice:
    def __init__(
        self,
        code: int,
        inherited_name: str,
        *,
        state: str = "PAUSED",
    ) -> None:
        self.available = True
        self.device_connected = True
        self.cloud_connected = True
        self.status = SimpleNamespace(
            state=SimpleNamespace(name=state),
            state_name=state.lower(),
            task_status=None,
            task_status_name="unknown",
            error=SimpleNamespace(value=code),
            error_name=inherited_name,
            has_error=True,
            battery_level=80,
            paused=True,
            returning=False,
            docked=False,
            running=False,
            scheduled_clean=False,
            shortcut_task=False,
            cleaning_mode=None,
            cleaning_mode_name="unknown",
            attributes={"error": inherited_name, "started": True},
        )
        self.info = SimpleNamespace(
            firmware_version="test",
            hardware_version="test",
            raw={},
        )
        self.capability = SimpleNamespace(list=[])
        self.unknown_properties = {}
        self.realtime_properties = {}
        self.last_realtime_message = None
        self._error_code = code

    def get_property(self, property_key: object) -> int | None:
        if getattr(property_key, "name", None) == "ERROR":
            return self._error_code
        return None


def _snapshot(
    code: int,
    inherited_name: str,
    *,
    realtime_error_code: int | None = None,
    state: str = "PAUSED",
    model: str = "dreame.mower.g2408",
):
    descriptor = descriptor_from_cloud_record(
        {"did": "test", "model": model, "name": "Mower"},
        account_type="dreame",
        country="eu",
    )
    assert descriptor is not None
    device = _ErrorDevice(code, inherited_name, state=state)
    if realtime_error_code is not None:
        device.realtime_properties = {"2.2": {"value": realtime_error_code}}
    return snapshot_from_device(descriptor, device)


@pytest.mark.parametrize(
    ("code", "inherited_name", "expected"),
    [
        (2, "cliff", "Mower stuck"),
        (23, "heart", "Emergency stop pressed"),
        (59, "no_go_zone", "Battery temperature too low"),
    ],
)
def test_mower_fault_codes_override_vacuum_labels(
    code: int,
    inherited_name: str,
    expected: str,
) -> None:
    snapshot = _snapshot(code, inherited_name)

    assert snapshot.activity == "error"
    assert snapshot.error_code == code
    assert snapshot.error_display == expected


@pytest.mark.parametrize(
    ("code", "inherited_name", "state", "expected_activity", "expected_notice"),
    [
        (53, "unknown", "MOWING", "mowing", "Scheduled mowing started"),
        (54, "edge", "RETURNING", "returning", "Low battery returning"),
        (54, "edge", "CHARGING", "docked", "Low battery returning"),
        (
            56,
            "laser",
            "RETURNING",
            "returning",
            "Bad weather protection active",
        ),
        (56, "laser", "CHARGING", "docked", "Bad weather protection active"),
    ],
)
def test_mower_operating_conditions_are_not_hard_errors(
    code: int,
    inherited_name: str,
    state: str,
    expected_activity: str,
    expected_notice: str,
) -> None:
    snapshot = _snapshot(
        code,
        inherited_name,
        realtime_error_code=code,
        state=state,
    )

    assert snapshot.activity == expected_activity
    assert snapshot.error_code is None
    assert snapshot.error_name is None
    assert snapshot.error_text is None
    assert snapshot.error_display is None
    assert snapshot.error_source is None
    assert snapshot.status_notice_code == code
    assert snapshot.status_notice_display == expected_notice
    assert snapshot.status_notice_source == "status"
    assert snapshot.raw_error_code == code
    assert snapshot.realtime_error_code == code


def test_mower_native_name_replaces_vacuum_name() -> None:
    snapshot = _snapshot(1, "drop")

    assert snapshot.activity == "error"
    assert snapshot.error_name == "robot_tilted"
    assert snapshot.error_text is None
    assert snapshot.error_display == "Robot tilted"


def test_recoverable_alert_does_not_latch_error() -> None:
    snapshot = _snapshot(31, "left_wheell_speed")

    assert snapshot.activity == "paused"
    assert snapshot.error_code is None
    assert snapshot.error_name is None
    assert snapshot.status_notice_code == 31
    assert snapshot.status_notice_name == "return_to_station_failed"
    assert snapshot.status_notice_display == "Return to station failed"
    assert snapshot.status_notice_tier == "alert"


def test_unknown_numeric_code_is_visible_without_becoming_a_vacuum_error() -> None:
    snapshot = _snapshot(999, "return_to_charge_failed")

    assert snapshot.activity == "paused"
    assert snapshot.error_code is None
    assert snapshot.error_display is None
    assert snapshot.status_notice_code == 999
    assert snapshot.status_notice_name == "unknown_mower_device_code_999"
    assert snapshot.status_notice_tier == "unknown"


def test_unknown_numeric_code_uses_explicit_mower_error_state_as_fallback() -> None:
    snapshot = _snapshot(999, "return_to_charge_failed", state="ERROR")

    assert snapshot.activity == "error"
    assert snapshot.error_code == 999
    assert snapshot.error_name == "unknown_mower_device_code_999"
    assert snapshot.error_display == "Unknown mower device code 999"
    assert snapshot.status_notice_code is None


def test_model_overrides_prevent_cross_model_code_guesses() -> None:
    assert mower_device_code_name(0) == "no_device_code"
    assert mower_fault_active(0) is False
    assert (
        mower_device_code_name(0, model="dreame.mower.g2408")
        == "robot_lifted"
    )
    assert mower_fault_active(0, model="dreame.mower.g2408") is True
    assert (
        mower_device_code_name(19, model="dreame.mower.p2255")
        == "emergency_stop_pressed"
    )
    assert (
        mower_device_code_name(0, model="mova.mower.g2529c")
        == "robot_lifted"
    )
    assert mower_fault_active(0, model="mova.mower.g2529c") is True
    assert (
        mower_device_code_name(0, model="mova.mower.x1234")
        == "no_device_code"
    )
    assert mower_fault_active(0, model="mova.mower.x1234") is False


@pytest.mark.parametrize(
    "model",
    [None, "dreame.mower.p2255", "dreame.mower.x1234", "mova.mower.x1234"],
)
def test_base_no_device_code_is_not_a_status_notice(model: str | None) -> None:
    assert (
        device_code_semantics.mower_status_notice_code(0, model=model)
        is None
    )


@pytest.mark.parametrize(
    ("code", "tier"),
    [
        (2, MowerDeviceCodeTier.ERROR),
        (27, MowerDeviceCodeTier.ATTENTION),
        (31, MowerDeviceCodeTier.ALERT),
        (56, MowerDeviceCodeTier.INFO),
    ],
)
def test_a2_app_tiers_are_preserved(
    code: int,
    tier: MowerDeviceCodeTier,
) -> None:
    assert mower_device_code_tier(code, model="dreame.mower.g2408") is tier


def test_a2_catalog_has_a_meaning_for_every_observed_app_code() -> None:
    for code in range(78):
        assert (
            device_code_semantics.mower_device_code_definition(
                code,
                model="dreame.mower.g2408",
            )
            is not None
        )


@pytest.mark.parametrize(
    ("code", "has_error", "has_warning", "name"),
    [
        (2, True, False, "mower_stuck"),
        (31, False, True, "return_to_station_failed"),
        (56, False, False, "bad_weather_protection_active"),
    ],
)
def test_inherited_device_status_uses_mower_registry(
    code: int,
    has_error: bool,
    has_warning: bool,
    name: str,
) -> None:
    device = SimpleNamespace(
        info=SimpleNamespace(model="dreame.mower.g2408"),
        capability=SimpleNamespace(new_state=True),
    )
    device.get_property = lambda prop: (
        code if getattr(prop, "name", None) == "ERROR" else 3
    )
    status = DreameMowerDeviceStatus(device)

    assert status.device_code == code
    assert status.error == code
    assert status.has_error is has_error
    assert status.has_warning is has_warning
    assert status.error_name == name
    assert status.error_image is None


@pytest.mark.parametrize(
    ("code", "inherited_name"),
    [(48, "lds_error"), (63, "blocked")],
)
def test_mower_info_event_does_not_override_healthy_docked_state(
    code: int,
    inherited_name: str,
) -> None:
    snapshot = _snapshot(
        code,
        inherited_name,
        realtime_error_code=code,
        state="CHARGING_COMPLETED",
    )

    assert snapshot.state == "charging_completed"
    assert snapshot.activity == "docked"
    assert snapshot.docked is True
    assert snapshot.raw_error_code == code
    assert snapshot.realtime_error_code == code
    assert snapshot.error_code is None
    assert snapshot.error_name is None
    assert snapshot.error_display is None
    assert snapshot.error_source is None


@pytest.mark.parametrize("code", [48, 50, 61, 63, 70])
def test_mower_lifecycle_event_codes_are_not_active_errors(code: int) -> None:
    snapshot = _snapshot(code, "route")

    assert snapshot.activity == "paused"
    assert snapshot.raw_error_code == code
    assert snapshot.error_source is None
    assert snapshot.error_display is None


@pytest.mark.parametrize("code", [48, 50, 61, 63, 70])
def test_mower_lifecycle_event_suppresses_stale_realtime_error(code: int) -> None:
    snapshot = _snapshot(code, "route", realtime_error_code=23)

    assert snapshot.activity == "paused"
    assert snapshot.raw_error_code == code
    assert snapshot.realtime_error_code == 23
    assert snapshot.error_code is None
    assert snapshot.error_source is None
    assert snapshot.error_display is None
