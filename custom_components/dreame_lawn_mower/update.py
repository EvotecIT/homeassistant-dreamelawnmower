"""Firmware update entity for Dreame lawn mowers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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

FIRMWARE_INSTALL_ASSUMED_IN_PROGRESS = timedelta(hours=24)


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
        self._install_requested_at: datetime | None = None
        self._install_target_version: str | None = None

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
        current_version = getattr(support, "current_version", None)
        if isinstance(current_version, str) and current_version:
            return current_version
        return None

    @property
    def latest_version(self) -> str | None:
        """Return the app-approved target firmware version."""
        support = self.coordinator.firmware_update_support
        return None if support is None else support.latest_version

    @property
    def in_progress(self) -> bool | int | None:
        """Return whether the mower reports an update in progress."""
        live_in_progress = _snapshot_update_in_progress(self.coordinator)
        if live_in_progress is True:
            return True
        if self._assumed_install_in_progress():
            return True
        if live_in_progress is False:
            return False
        support = self.coordinator.firmware_update_support
        if support is None:
            return live_in_progress
        if support.update_state in {
            "upgrading",
            "updating",
            "installing",
            "downloading",
        }:
            return True
        return live_in_progress

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
            "ota_state": getattr(support, "ota_state", None),
            "ota_state_name": getattr(support, "ota_state_name", None),
            "ota_progress": getattr(support, "ota_progress", None),
            "release_summary_available": support.release_summary_available,
            "install_assumed_in_progress": self._assumed_install_in_progress(),
            "install_requested_at": (
                getattr(self, "_install_requested_at", None).isoformat()
                if getattr(self, "_install_requested_at", None) is not None
                else None
            ),
            "install_target_version": getattr(self, "_install_target_version", None),
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

        self._install_requested_at = datetime.now(UTC)
        self._install_target_version = latest_version
        await self.coordinator.async_refresh_firmware_update_support(force=True)
        await self.coordinator.async_refresh_batch_device_data(
            force=True,
            source="firmware_update_install",
        )
        await self.coordinator.async_request_refresh()

    def _assumed_install_in_progress(self) -> bool:
        """Return whether a recent HA install approval should still be active."""
        install_requested_at = getattr(self, "_install_requested_at", None)
        if install_requested_at is None:
            return False
        install_target_version = getattr(self, "_install_target_version", None)
        support = self.coordinator.firmware_update_support
        installed_version = self.installed_version
        target_version = install_target_version or (
            support.latest_version if support is not None else None
        )
        if (
            installed_version is not None
            and target_version is not None
            and installed_version == target_version
        ):
            self._install_requested_at = None
            self._install_target_version = None
            return False
        if (
            support is not None
            and getattr(support, "update_available", None) is False
            and getattr(support, "update_state", None) is None
            and target_version is None
        ):
            self._install_requested_at = None
            self._install_target_version = None
            return False
        if (
            datetime.now(UTC) - install_requested_at
            > FIRMWARE_INSTALL_ASSUMED_IN_PROGRESS
        ):
            self._install_requested_at = None
            self._install_target_version = None
            return False
        return True


def _snapshot_firmware_version(coordinator: DreameLawnMowerCoordinator) -> str | None:
    """Return the latest firmware version from the live coordinator snapshot."""
    snapshot = getattr(coordinator, "data", None)
    version = getattr(snapshot, "firmware_version", None)
    if isinstance(version, str) and version.strip():
        return version
    return None


def _snapshot_update_state(coordinator: DreameLawnMowerCoordinator) -> str | None:
    """Return the current live update state when the snapshot exposes one."""
    snapshot = getattr(coordinator, "data", None)
    for value in (
        getattr(snapshot, "state_name", None),
        getattr(snapshot, "activity", None),
        getattr(snapshot, "task_status_name", None),
    ):
        if not isinstance(value, str):
            continue
        normalized = value.strip().lower()
        if normalized in {"upgrading", "updating", "installing", "downloading"}:
            return normalized
    return None


def _snapshot_update_in_progress(
    coordinator: DreameLawnMowerCoordinator,
) -> bool | None:
    """Return a live in-progress signal when the snapshot exposes any state."""
    snapshot = getattr(coordinator, "data", None)
    saw_live_state = False
    for value in (
        getattr(snapshot, "state_name", None),
        getattr(snapshot, "activity", None),
        getattr(snapshot, "task_status_name", None),
    ):
        if not isinstance(value, str):
            continue
        normalized = value.strip().lower()
        if not normalized:
            continue
        saw_live_state = True
        if normalized in {"upgrading", "updating", "installing", "downloading"}:
            return True

    if saw_live_state:
        return False
    return None
