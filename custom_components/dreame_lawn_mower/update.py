"""Firmware update entity for Dreame lawn mowers."""

from __future__ import annotations

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import DreameLawnMowerCoordinator
from .entity import DreameLawnMowerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up mower firmware update entities."""
    coordinator: DreameLawnMowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DreameLawnMowerFirmwareUpdateEntity(coordinator)])


class DreameLawnMowerFirmwareUpdateEntity(
    DreameLawnMowerEntity,
    UpdateEntity,
):
    """Expose the approved mower firmware update path."""

    _attr_name = "Firmware"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = UpdateEntityFeature.INSTALL

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_firmware"

    @property
    def available(self) -> bool:
        """Return whether the entity is currently available."""
        return (
            super().available and self.coordinator.firmware_update_support is not None
        )

    @property
    def installed_version(self) -> str | None:
        """Return the currently installed firmware version."""
        if version := _snapshot_firmware_version(self.coordinator):
            return version
        support = self.coordinator.firmware_update_support
        if support is not None and support.current_version:
            return support.current_version
        return None

    @property
    def latest_version(self) -> str | None:
        """Return the app-approved target firmware version."""
        support = self.coordinator.firmware_update_support
        return None if support is None else support.latest_version

    @property
    def in_progress(self) -> bool | int | None:
        """Return whether the mower reports an update in progress."""
        if live_state := _snapshot_update_state(self.coordinator):
            return live_state in {"upgrading", "updating"}
        support = self.coordinator.firmware_update_support
        if support is None:
            return None
        return support.update_state in {"upgrading", "updating"}

    @property
    def release_summary(self) -> str | None:
        """Return release notes when the cloud endpoint provides them."""
        support = self.coordinator.firmware_update_support
        if support is None or not support.release_summary_available:
            return None
        return support.release_summary

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Return extra firmware/update diagnostics."""
        support = self.coordinator.firmware_update_support
        if support is None:
            return {}
        return {
            "update_available": support.update_available,
            "update_state": support.update_state,
            "cloud_check_available": support.cloud_check_available,
            "cloud_check_update_available": support.cloud_check_update_available,
            "batch_ota_available": support.batch_ota_available,
            "auto_upgrade_enabled": support.auto_upgrade_enabled,
            "ota_status": support.ota_status,
            "release_summary_available": support.release_summary_available,
            "reason": support.reason,
            "warnings": list(support.warnings),
        }

    async def async_install(
        self,
        version: str | None,
        backup: bool,
        **kwargs,
    ) -> None:
        """Trigger the cloud firmware approval step."""
        support = self.coordinator.firmware_update_support
        if support is None:
            raise HomeAssistantError("Firmware update support is not available.")

        latest_version = support.latest_version
        if (
            version is not None
            and latest_version is not None
            and version != latest_version
        ):
            raise HomeAssistantError(
                "Requested firmware version "
                f"{version} does not match approved target {latest_version}."
            )

        result = await self.coordinator.client.async_approve_firmware_update(
            language="en",
        )
        if not result.get("success"):
            message = result.get("msg") or "Firmware approval failed."
            raise HomeAssistantError(str(message))

        await self.coordinator.async_refresh_firmware_update_support(force=True)
        await self.coordinator.async_refresh_batch_device_data(
            force=True,
            source="firmware_update_install",
        )
        await self.coordinator.async_request_refresh()


def _snapshot_firmware_version(coordinator: DreameLawnMowerCoordinator) -> str | None:
    """Return the latest firmware version from the live coordinator snapshot."""
    snapshot = coordinator.data
    version = getattr(snapshot, "firmware_version", None)
    if isinstance(version, str) and version.strip():
        return version
    return None


def _snapshot_update_state(coordinator: DreameLawnMowerCoordinator) -> str | None:
    """Return the current live update state when the snapshot exposes one."""
    snapshot = coordinator.data
    for value in (
        getattr(snapshot, "state_name", None),
        getattr(snapshot, "activity", None),
        getattr(snapshot, "task_status_name", None),
    ):
        if not isinstance(value, str):
            continue
        normalized = value.strip().lower()
        if normalized in {"upgrading", "updating"}:
            return normalized
    return None
