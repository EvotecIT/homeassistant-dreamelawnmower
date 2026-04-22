from __future__ import annotations

import asyncio
from types import SimpleNamespace

from homeassistant.exceptions import HomeAssistantError

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.client import (
    DreameLawnMowerClient,
)
from custom_components.dreame_lawn_mower.update import (
    DreameLawnMowerFirmwareUpdateEntity,
)


def test_firmware_update_entity_prefers_live_snapshot_state() -> None:
    entity = object.__new__(DreameLawnMowerFirmwareUpdateEntity)
    entity.coordinator = SimpleNamespace(
        last_update_success=True,
        data=SimpleNamespace(
            firmware_version="4.3.6_0447",
            state_name="upgrading",
            activity="mowing",
            task_status_name=None,
        ),
        firmware_update_support=SimpleNamespace(
            current_version="4.3.6_0320",
            latest_version="4.3.6_0550",
            update_state=None,
            release_summary=None,
            release_summary_available=False,
            update_available=True,
            cloud_check_available=True,
            cloud_check_update_available=True,
            batch_ota_available=True,
            auto_upgrade_enabled=False,
            ota_status=0,
            reason=(
                "Cloud checkDeviceVersion and batch OTA info both report that a mower "
                "firmware update is available."
            ),
            warnings=(),
        ),
    )

    assert entity.available is True
    assert entity.installed_version == "4.3.6_0447"
    assert entity.latest_version == "4.3.6_0550"
    assert entity.in_progress is True
    assert entity.release_summary is None
    assert entity.extra_state_attributes["cloud_check_update_available"] is True
    assert entity.extra_state_attributes["batch_ota_available"] is True


def test_firmware_update_entity_prefers_live_non_update_state() -> None:
    entity = object.__new__(DreameLawnMowerFirmwareUpdateEntity)
    entity.coordinator = SimpleNamespace(
        last_update_success=True,
        data=SimpleNamespace(
            firmware_version="4.3.6_0447",
            state_name="mowing",
            activity="mowing",
            task_status_name=None,
        ),
        firmware_update_support=SimpleNamespace(
            current_version="4.3.6_0320",
            latest_version="4.3.6_0550",
            update_state="upgrading",
            release_summary=None,
            release_summary_available=False,
            update_available=True,
            cloud_check_available=True,
            cloud_check_update_available=True,
            batch_ota_available=True,
            auto_upgrade_enabled=False,
            ota_status=0,
            reason="Mower reports an update-related state.",
            warnings=(),
        ),
    )

    assert entity.in_progress is False


def test_approve_firmware_update_treats_wrapper_success_as_accepted() -> None:
    client = object.__new__(DreameLawnMowerClient)

    class _Cloud:
        def manual_firmware_update(self, language=None):
            return {
                "code": 0,
                "success": True,
                "msg": "ok",
                "data": {
                    "code": 2,
                    "success": False,
                },
            }

    client._sync_get_cloud_protocol = lambda: _Cloud()

    result = client._sync_approve_firmware_update("en")

    assert result["accepted"] is True
    assert result["success"] is True
    assert result["code"] == 0
    assert result["wrapper_success"] is True
    assert result["inner_code"] == 2
    assert result["inner_success"] is False


def test_firmware_update_entity_install_refreshes_after_success() -> None:
    calls: list[tuple[str, object]] = []

    class _Client:
        async def async_approve_firmware_update(self, language="en"):
            calls.append(("approve", language))
            return {
                "accepted": True,
                "success": True,
                "code": 0,
            }

    class _Coordinator:
        def __init__(self):
            self.client = _Client()
            self.firmware_update_support = SimpleNamespace(latest_version="4.3.6_0447")

        async def async_refresh_firmware_update_support(self, force=False):
            calls.append(("refresh_support", force))

        async def async_refresh_batch_device_data(self, force=False, source=None):
            calls.append(("refresh_batch", force, source))

        async def async_request_refresh(self):
            calls.append(("request_refresh", None))

    entity = object.__new__(DreameLawnMowerFirmwareUpdateEntity)
    entity.coordinator = _Coordinator()

    asyncio.run(entity.async_install("4.3.6_0447", backup=False))

    assert calls == [
        ("approve", "en"),
        ("refresh_support", True),
        ("refresh_batch", True, "firmware_update_install"),
        ("request_refresh", None),
    ]


def test_firmware_update_entity_install_rejects_wrong_version() -> None:
    entity = object.__new__(DreameLawnMowerFirmwareUpdateEntity)
    entity.coordinator = SimpleNamespace(
        firmware_update_support=SimpleNamespace(latest_version="4.3.6_0447")
    )

    try:
        asyncio.run(entity.async_install("4.3.6_0550", backup=False))
    except HomeAssistantError as err:
        assert "does not match approved target 4.3.6_0447" in str(err)
    else:
        raise AssertionError("Expected HomeAssistantError")
