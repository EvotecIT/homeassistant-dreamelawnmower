"""Regression tests for mower-specific error meanings."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    device_code_semantics,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.client import (
    DreameLawnMowerClient,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device import (
    DreameMowerDeviceStatus,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerSnapshot,
    descriptor_from_cloud_record,
    snapshot_from_device,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.types import (
    DreameMowerProperty,
)

MowerDeviceCodeTier = device_code_semantics.MowerDeviceCodeTier
mower_device_code_name = device_code_semantics.mower_device_code_name
mower_device_code_tier = device_code_semantics.mower_device_code_tier
mower_fault_active = device_code_semantics.mower_fault_active


class _ErrorDevice:
    def __init__(
        self,
        code: int | None,
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
            has_error=code not in (None, -1),
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
    code: int | None,
    inherited_name: str,
    *,
    realtime_error_code: int | None = None,
    realtime_error_last_seen: float | None = None,
    realtime_state_last_seen: float | None = None,
    state: str = "PAUSED",
    model: str = "dreame.mower.g2408",
    previous_snapshot: DreameLawnMowerSnapshot | None = None,
) -> DreameLawnMowerSnapshot:
    descriptor = descriptor_from_cloud_record(
        {"did": "test", "model": model, "name": "Mower"},
        account_type="dreame",
        country="eu",
    )
    assert descriptor is not None
    device = _ErrorDevice(code, inherited_name, state=state)
    if realtime_error_code is not None:
        device.realtime_properties["2.2"] = {
            "value": realtime_error_code,
            "last_seen": realtime_error_last_seen,
        }
    if realtime_state_last_seen is not None:
        device.realtime_properties["2.1"] = {
            "value": state,
            "last_seen": realtime_state_last_seen,
        }
    return snapshot_from_device(
        descriptor,
        device,
        previous_snapshot=previous_snapshot,
    )


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


def test_a2_3000_uses_a2_maintenance_point_notice_catalog() -> None:
    snapshot = _snapshot(
        75,
        "unknown",
        realtime_error_code=75,
        model="dreame.mower.g2568d",
    )

    assert snapshot.error_code is None
    assert snapshot.status_notice_code == 75
    assert snapshot.status_notice_name == "maintenance_point_reached"
    assert snapshot.status_notice_display == "Maintenance point reached"


def test_realtime_start_notice_retains_event_time_for_session_identity() -> None:
    snapshot = _snapshot(
        53,
        "unknown",
        realtime_error_code=53,
        realtime_error_last_seen=123.5,
        state="MOWING",
    )

    assert snapshot.status_notice_name == "scheduled_mowing_started"
    assert snapshot.status_notice_event_at == 123.5


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


@pytest.mark.parametrize(
    ("status_code", "realtime_code", "expected_source"),
    [
        (27, None, "status"),
        (27, 27, "status"),
        (None, 27, "realtime_property_2.2"),
    ],
)
def test_human_detection_is_a_notice_while_q2501a_keeps_mowing(
    status_code: int | None,
    realtime_code: int | None,
    expected_source: str,
) -> None:
    snapshot = _snapshot(
        status_code,
        "infrared_shielding",
        realtime_error_code=realtime_code,
        state="MOWING",
        model="dreame.mower.q2501a",
    )

    assert snapshot.state == "mowing"
    assert snapshot.activity == "mowing"
    assert snapshot.error_code is None
    assert snapshot.error_name is None
    assert snapshot.error_display is None
    assert snapshot.error_source is None
    assert snapshot.status_notice_code == 27
    assert snapshot.status_notice_name == "human_detected"
    assert snapshot.status_notice_display == "Human detected"
    assert snapshot.status_notice_tier == "attention"
    assert snapshot.status_notice_source == expected_source
    assert snapshot.raw_error_code == status_code
    assert snapshot.realtime_error_code == realtime_code


@pytest.mark.parametrize("state", ["PAUSED", "ERROR"])
def test_human_detection_remains_a_fault_when_q2501a_is_halted(
    state: str,
) -> None:
    snapshot = _snapshot(
        27,
        "infrared_shielding",
        realtime_error_code=27,
        state=state,
        model="dreame.mower.q2501a",
    )

    assert snapshot.activity == "error"
    assert snapshot.error_code == 27
    assert snapshot.error_name == "human_detected"
    assert snapshot.error_display == "Human detected"
    assert snapshot.error_source == "status"
    assert snapshot.status_notice_code is None
    assert snapshot.raw_error_code == 27
    assert snapshot.realtime_error_code == 27


@pytest.mark.parametrize(
    "model",
    [
        "dreame.mower.p2255",
        "dreame.mower.x1234",
    ],
)
def test_human_detection_while_mowing_remains_a_fault_on_other_models(
    model: str,
) -> None:
    snapshot = _snapshot(
        27,
        "infrared_shielding",
        realtime_error_code=27,
        state="MOWING",
        model=model,
    )

    assert snapshot.activity == "error"
    assert snapshot.error_code == 27
    assert snapshot.error_name == "human_detected"
    assert snapshot.error_display == "Human detected"
    assert snapshot.status_notice_code is None


def test_mower_native_name_replaces_vacuum_name() -> None:
    snapshot = _snapshot(1, "drop")

    assert snapshot.activity == "error"
    assert snapshot.error_name == "robot_tilted"
    assert snapshot.error_text is None
    assert snapshot.error_display == "Robot tilted"


@pytest.mark.parametrize(
    ("state", "expected_activity"),
    [
        ("MOWING", "mowing"),
        ("RETURNING", "returning"),
        ("CHARGING", "docked"),
    ],
)
def test_operational_state_releases_stale_fault_code(
    state: str,
    expected_activity: str,
) -> None:
    fault = _snapshot(0, "drop")
    recovered = _snapshot(
        0,
        "drop",
        state=state,
        previous_snapshot=fault,
    )

    assert fault.activity == "error"
    assert recovered.activity == expected_activity
    assert recovered.error_code is None
    assert recovered.error_name is None
    assert recovered.error_display is None
    assert recovered.error_source is None
    assert recovered.raw_error_code == 0
    assert recovered.realtime_error_code is None


def test_operational_state_releases_stale_realtime_only_fault() -> None:
    fault = _snapshot(0, "drop")
    snapshot = _snapshot(
        None,
        "",
        realtime_error_code=0,
        realtime_error_last_seen=100,
        realtime_state_last_seen=200,
        state="MOWING",
        previous_snapshot=fault,
    )

    assert snapshot.activity == "mowing"
    assert snapshot.error_code is None
    assert snapshot.error_display is None
    assert snapshot.error_source is None
    assert snapshot.raw_error_code is None
    assert snapshot.realtime_error_code == 0


def test_recovered_fault_stays_released_across_repeated_refreshes() -> None:
    fault = _snapshot(0, "drop")
    recovered = _snapshot(
        0,
        "drop",
        state="MOWING",
        previous_snapshot=fault,
    )
    repeated = _snapshot(
        0,
        "drop",
        state="MOWING",
        previous_snapshot=recovered,
    )

    assert recovered.activity == "mowing"
    assert repeated.activity == "mowing"
    assert repeated.error_code is None
    assert repeated.raw_error_code == 0


@pytest.mark.parametrize(
    ("state_last_seen", "error_last_seen"),
    [
        (None, None),
        (100, 100),
        (100, 200),
    ],
)
def test_fresh_realtime_fault_overrides_operational_state(
    state_last_seen: float | None,
    error_last_seen: float | None,
) -> None:
    fault = _snapshot(0, "drop")
    snapshot = _snapshot(
        None,
        "",
        realtime_error_code=0,
        realtime_error_last_seen=error_last_seen,
        realtime_state_last_seen=state_last_seen,
        state="MOWING",
        previous_snapshot=fault,
    )

    assert snapshot.activity == "error"
    assert snapshot.error_code == 0
    assert snapshot.error_display == "Robot lifted"
    assert snapshot.error_source == "realtime_property_2.2"


def test_newer_operational_event_releases_realtime_fault_seen_while_mowing() -> None:
    fresh_fault = _snapshot(
        None,
        "",
        realtime_error_code=0,
        realtime_error_last_seen=200,
        realtime_state_last_seen=100,
        state="MOWING",
    )
    recovered = _snapshot(
        None,
        "",
        realtime_error_code=0,
        realtime_error_last_seen=200,
        realtime_state_last_seen=300,
        state="MOWING",
        previous_snapshot=fresh_fault,
    )

    assert fresh_fault.activity == "error"
    assert recovered.activity == "mowing"
    assert recovered.error_code is None
    assert recovered.realtime_error_code == 0


def test_newer_realtime_fault_relatches_after_recovery() -> None:
    fault = _snapshot(0, "drop")
    recovered = _snapshot(
        None,
        "",
        realtime_error_code=0,
        realtime_error_last_seen=100,
        realtime_state_last_seen=200,
        state="MOWING",
        previous_snapshot=fault,
    )
    relatched = _snapshot(
        None,
        "",
        realtime_error_code=0,
        realtime_error_last_seen=300,
        realtime_state_last_seen=200,
        state="MOWING",
        previous_snapshot=recovered,
    )

    assert recovered.activity == "mowing"
    assert relatched.activity == "error"
    assert relatched.error_code == 0
    assert relatched.error_source == "realtime_property_2.2"


def test_fresh_bare_error_flag_is_not_mistaken_for_suppression() -> None:
    descriptor = descriptor_from_cloud_record(
        {"did": "test", "model": "dreame.mower.g2408", "name": "Mower"},
        account_type="dreame",
        country="eu",
    )
    assert descriptor is not None
    healthy = _snapshot(None, "", state="CHARGING")
    device = _ErrorDevice(None, "", state="CHARGING")
    device.status.has_error = True

    snapshot = snapshot_from_device(
        descriptor,
        device,
        previous_snapshot=healthy,
    )

    assert snapshot.activity == "error"
    assert snapshot.error_code is None
    assert snapshot.error_source == "status"


def test_client_carries_fault_state_into_recovery_reconciliation() -> None:
    descriptor = descriptor_from_cloud_record(
        {"did": "test", "model": "dreame.mower.g2408", "name": "Mower"},
        account_type="dreame",
        country="eu",
    )
    assert descriptor is not None
    fault = _snapshot(0, "drop")
    device = _ErrorDevice(0, "drop", state="MOWING")
    client = object.__new__(DreameLawnMowerClient)
    client._descriptor = descriptor
    client._latest_snapshot = fault
    client._sync_update_device = lambda: device
    client._sync_get_status_blob = lambda *_args: None
    client._sync_get_cached_cloud_device_info = lambda: None

    recovered = asyncio.run(client.async_refresh())
    repeated = asyncio.run(client.async_refresh())

    assert recovered.activity == "mowing"
    assert recovered.error_code is None
    assert repeated.activity == "mowing"
    assert repeated.error_code is None
    assert client._latest_snapshot is repeated


def test_remote_control_support_reuses_recovered_fault_context() -> None:
    descriptor = descriptor_from_cloud_record(
        {"did": "test", "model": "dreame.mower.g2408", "name": "Mower"},
        account_type="dreame",
        country="eu",
    )
    assert descriptor is not None
    fault = _snapshot(0, "drop")
    device = _ErrorDevice(0, "drop", state="CHARGING")
    device.property_mapping = {
        DreameMowerProperty.REMOTE_CONTROL: {"siid": 4, "piid": 15}
    }
    device.status.status = None
    device.status.fast_mapping = False
    device._remote_control = False
    client = object.__new__(DreameLawnMowerClient)
    client._descriptor = descriptor
    client._latest_snapshot = fault
    client._ensure_device = lambda: device

    support = client._sync_get_remote_control_support(refresh=False)

    assert support.supported is True
    assert support.state_safe is True
    assert support.state_block_reason is None
    assert client._latest_snapshot.activity == "docked"
    assert client._latest_snapshot.error_code is None


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
    for model in ("mova.mower.g2529f", "g2529f"):
        assert mower_device_code_name(0, model=model) == "robot_lifted"
        assert mower_fault_active(0, model=model) is True
        assert mower_device_code_name(55, model=model) == "cannot_start_low_battery"
        assert mower_fault_active(55, model=model) is True
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


@pytest.mark.parametrize(
    ("code", "expected_name"),
    [
        (28, "blades_worn"),
        (29, "station_brush_worn"),
        (30, "maintenance_due"),
    ],
)
def test_attention_notices_do_not_override_mowing_for_model_variants(
    code: int,
    expected_name: str,
) -> None:
    snapshot = _snapshot(
        code,
        "fan_speed_error",
        realtime_error_code=code,
        state="MOWING",
        model="dreame.mower.x1234",
    )

    assert snapshot.state == "mowing"
    assert snapshot.activity == "mowing"
    assert snapshot.error_code is None
    assert snapshot.error_display is None
    assert snapshot.status_notice_code == code
    assert snapshot.status_notice_name == expected_name
    assert snapshot.status_notice_tier == "attention"


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
