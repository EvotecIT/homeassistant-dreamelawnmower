"""Regression contracts for device information projection."""

from __future__ import annotations

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device import (
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
