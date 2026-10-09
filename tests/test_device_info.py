"""Regression contracts for device information projection."""

from __future__ import annotations

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device import (
    DreameMowerDevice,
    DreameMowerDeviceInfo,
)


def test_device_info_repr_is_safe_and_complete() -> None:
    info = DreameMowerDeviceInfo(
        {
            "model": "dreame.mower.g2408",
            "fw_ver": "4.3.6_0320",
            "mac": "00:11:22:33:44:55",
            "netif": {"localIp": "192.0.2.10"},
        }
    )

    assert repr(info) == (
        "dreame.mower.g2408 v320 (00:11:22:33:44:55) @ 192.0.2.10"
    )


@pytest.mark.parametrize("firmware", ["4.3.6_preview", "4.3.6_", 320, {}, None])
def test_optional_firmware_metadata_does_not_abort_identity(firmware: object) -> None:
    data = {"model": "dreame.mower.g2408", "fw_ver": firmware}
    info = DreameMowerDeviceInfo(data)
    assert info.model == "dreame.mower.g2408"
    assert info.version == 0
    assert info.firmware_version == (firmware if isinstance(firmware, str) else None)
    assert info.raw is data


def test_identity_uses_valid_fallback_firmware() -> None:
    info = DreameMowerDeviceInfo({"fw_ver": 320, "ver": "4.3.6_0320"})
    assert info.firmware_version == "4.3.6_0320"
    assert info.version == 320


@pytest.mark.parametrize("value", [None, 42, "invalid", []])
def test_invalid_optional_network_metadata_has_no_network(value: object) -> None:
    info = DreameMowerDeviceInfo({"netif": value})
    assert info.network_interface is None
    assert repr(info) == "None v0 (None) @ "


def test_identity_projects_only_string_fields_and_preserves_raw() -> None:
    data = {"model": {}, "mac": 42, "hw_ver": []}
    info = DreameMowerDeviceInfo(data)
    assert info.model is None
    assert info.mac_address is None
    assert info.hardware_version is None
    assert info.raw is data
    assert DreameMowerDeviceInfo({}).hardware_version == "Linux"


def test_initial_identity_retains_unknown_firmware_without_aborting() -> None:
    data = {
        "model": "dreame.mower.g2408",
        "fw_ver": "4.3.6_preview",
        "mac": "00:11:22:33:44:55",
    }
    mower = object.__new__(DreameMowerDevice)
    mower.mac = None

    mower._prepare_device_initialization(data)

    assert mower.info.version == 0
    assert mower.info.model == "dreame.mower.g2408"
    assert mower.info.raw is data
    assert mower.mac == "00:11:22:33:44:55"
