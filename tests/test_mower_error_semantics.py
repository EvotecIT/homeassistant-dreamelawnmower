"""Regression tests for mower-specific error meanings."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    descriptor_from_cloud_record,
    snapshot_from_device,
)


class _ErrorDevice:
    def __init__(self, code: int, inherited_name: str) -> None:
        self.available = True
        self.device_connected = True
        self.cloud_connected = True
        self.status = SimpleNamespace(
            state=SimpleNamespace(name="PAUSED"),
            state_name="paused",
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
):
    descriptor = descriptor_from_cloud_record(
        {"did": "test", "model": "dreame.mower.g2408", "name": "Mower"},
        account_type="dreame",
        country="eu",
    )
    assert descriptor is not None
    device = _ErrorDevice(code, inherited_name)
    if realtime_error_code is not None:
        device.realtime_properties = {"2.2": {"value": realtime_error_code}}
    return snapshot_from_device(descriptor, device)


@pytest.mark.parametrize(
    ("code", "inherited_name", "expected"),
    [
        (2, "cliff", "Mower stuck"),
        (23, "heart", "Emergency stop pressed"),
        (53, "unknown", "Rain detected"),
        (54, "edge", "Low battery"),
    ],
)
def test_mower_error_codes_override_vacuum_labels(
    code: int,
    inherited_name: str,
    expected: str,
) -> None:
    snapshot = _snapshot(code, inherited_name)

    assert snapshot.activity == "error"
    assert snapshot.error_code == code
    assert snapshot.error_display == expected


@pytest.mark.parametrize("code", [61, 70])
def test_dnd_transition_codes_are_not_active_errors(code: int) -> None:
    snapshot = _snapshot(code, "route")

    assert snapshot.activity == "paused"
    assert snapshot.raw_error_code == code
    assert snapshot.error_source is None
    assert snapshot.error_display is None


@pytest.mark.parametrize("code", [61, 70])
def test_dnd_transition_suppresses_stale_realtime_error(code: int) -> None:
    snapshot = _snapshot(code, "route", realtime_error_code=23)

    assert snapshot.activity == "paused"
    assert snapshot.raw_error_code == code
    assert snapshot.realtime_error_code == 23
    assert snapshot.error_code is None
    assert snapshot.error_source is None
    assert snapshot.error_display is None
