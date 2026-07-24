"""Operational and write-result sensors for Dreame lawn mower."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.helpers.entity import EntityCategory

from .coordinator import DreameLawnMowerCoordinator
from .dreame_lawn_mower_client.maintenance import (
    MaintenanceItem,
    maintenance_item_status,
    maintenance_status_attributes,
)
from .entity import DreameLawnMowerEntity


class DreameLawnMowerMaintenanceRemainingSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose remaining maintenance life for a CMS counter."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(
        self,
        coordinator: DreameLawnMowerCoordinator,
        item: MaintenanceItem,
    ) -> None:
        super().__init__(coordinator)
        self._item = item
        self._attr_name = f"{item.name} Remaining"
        self._attr_icon = item.icon
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_maintenance_{item.key}_remaining"
        )

    @property
    def native_value(self) -> float | None:
        """Return remaining maintenance life percentage."""
        item = maintenance_item_status(
            self.coordinator.maintenance_status,
            self._item,
        )
        if not isinstance(item, dict):
            return None
        value = item.get("remaining_percent")
        return value if isinstance(value, int | float) else None

    @property
    def available(self) -> bool:
        """Return whether maintenance data is cached."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe maintenance counter details."""
        status = self.coordinator.maintenance_status
        attributes = maintenance_status_attributes(status)
        item = maintenance_item_status(status, self._item)
        if isinstance(item, dict):
            attributes.update(
                {
                    "item": item.get("key"),
                    "item_name": item.get("name"),
                    "used_minutes": item.get("used_minutes"),
                    "used_hours": item.get("used_hours"),
                    "remaining_minutes": item.get("remaining_minutes"),
                    "remaining_hours": item.get("remaining_hours"),
                    "total_minutes": item.get("total_minutes"),
                    "total_hours": item.get("total_hours"),
                    "status": item.get("status"),
                    "warning": item.get("warning"),
                    "warning_percent": item.get("warning_percent"),
                    "due": item.get("due"),
                }
            )
        return {key: value for key, value in attributes.items() if value is not None}


class DreameLawnMowerLastMaintenanceResetSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the last guarded maintenance reset or dry-run result."""

    _attr_name = "Last Maintenance Reset"
    _attr_icon = "mdi:wrench-clock"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_last_maintenance_reset"

    @property
    def native_value(self) -> str:
        """Return a compact state for the last maintenance reset result."""
        return _maintenance_reset_state(self.coordinator.last_maintenance_reset_result)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe details for the last maintenance reset result."""
        return maintenance_reset_result_attributes(
            self.coordinator.last_maintenance_reset_result
        )


def _maintenance_reset_state(result: dict[str, Any] | None) -> str:
    if not result:
        return "none"
    return "executed" if result.get("executed") else "dry_run"


def maintenance_reset_result_attributes(
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return compact, non-secret attributes for a maintenance reset result."""
    if not result:
        return {}

    attributes: dict[str, Any] = {
        "source": result.get("source"),
        "action": result.get("action"),
        "dry_run": result.get("dry_run"),
        "executed": result.get("executed"),
        "changed": result.get("changed"),
        "item": result.get("item"),
        "item_name": result.get("item_name"),
        "previous_cms": result.get("previous_cms"),
        "updated_cms": result.get("updated_cms"),
        "previous_item": result.get("previous_item"),
        "updated_item": result.get("updated_item"),
        "request": result.get("request"),
        "response_data": result.get("response_data"),
        "refreshed_cms": result.get("refreshed_cms"),
        "refreshed_item": result.get("refreshed_item"),
        "refresh_error": result.get("refresh_error"),
    }
    return {key: value for key, value in attributes.items() if value is not None}


class DreameLawnMowerLastScheduleWriteSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the last guarded schedule write or dry-run result."""

    _attr_name = "Last Schedule Write"
    _attr_icon = "mdi:calendar-edit"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_last_schedule_write"

    @property
    def native_value(self) -> str:
        """Return a compact state for the last schedule write result."""
        return _schedule_write_state(self.coordinator.last_schedule_write_result)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe details for the last schedule write result."""
        return schedule_write_result_attributes(
            self.coordinator.last_schedule_write_result
        )


def _schedule_write_state(result: dict[str, Any] | None) -> str:
    if not result:
        return "none"
    return "executed" if result.get("executed") else "dry_run"


def schedule_write_result_attributes(
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return compact, non-secret attributes for a schedule write result."""
    if not result:
        return {}

    target_plan = result.get("target_plan")
    schedule = result.get("schedule")
    target_schedule = result.get("target_schedule")
    attributes: dict[str, Any] = {
        "source": result.get("source"),
        "action": result.get("action"),
        "dry_run": result.get("dry_run"),
        "executed": result.get("executed"),
        "changed": result.get("changed"),
        "map_index": result.get("map_index"),
        "plan_id": result.get("plan_id"),
        "previous_enabled": result.get("previous_enabled"),
        "enabled": result.get("enabled"),
        "version": result.get("version"),
        "chunk_size": result.get("chunk_size"),
        "chunk_count": result.get("chunk_count"),
        "payload_size": result.get("payload_size"),
        "request": result.get("request"),
    }
    if isinstance(schedule, dict):
        attributes["schedule"] = schedule
    if isinstance(target_plan, dict):
        attributes["target_plan"] = target_plan
    if isinstance(target_schedule, dict):
        attributes["target_schedule"] = target_schedule
    if result.get("response_data") is not None:
        attributes["response_data"] = result.get("response_data")
    return {key: value for key, value in attributes.items() if value is not None}


class DreameLawnMowerLastPreferenceWriteSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the last mowing preference plan or executed write."""

    _attr_name = "Last Preference Write"
    _attr_icon = "mdi:tune-variant"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_last_preference_write"

    @property
    def native_value(self) -> str:
        """Return a compact state for the last preference write result."""
        return _preference_write_state(self.coordinator.last_preference_write_result)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe details for the last preference write plan."""
        return preference_write_result_attributes(
            self.coordinator.last_preference_write_result
        )


def _preference_write_state(result: dict[str, Any] | None) -> str:
    if not result:
        return "none"
    if result.get("executed"):
        return "executed"
    return "planned"


def preference_write_result_attributes(
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return compact, non-secret attributes for a preference write plan."""
    if not result:
        return {}

    attributes: dict[str, Any] = {
        "source": result.get("source"),
        "action": result.get("action"),
        "dry_run": result.get("dry_run"),
        "executed": result.get("executed"),
        "execute_supported": result.get("execute_supported"),
        "request_verified": result.get("request_verified"),
        "map_index": result.get("map_index"),
        "area_id": result.get("area_id"),
        "mode": result.get("mode"),
        "mode_name": result.get("mode_name"),
        "target_mode": result.get("target_mode"),
        "target_mode_name": result.get("target_mode_name"),
        "mode_changed": result.get("mode_changed"),
        "changed": result.get("changed"),
        "changed_fields": result.get("changed_fields"),
        "changes": result.get("changes"),
        "payload": result.get("payload"),
        "request_candidate": result.get("request_candidate"),
        "request_candidates": result.get("request_candidates"),
        "write_commands": result.get("write_commands"),
        "notes": result.get("notes"),
    }
    if isinstance(result.get("map"), dict):
        attributes["map"] = result.get("map")
    if isinstance(result.get("previous_preference"), dict):
        attributes["previous_preference"] = result.get("previous_preference")
    if isinstance(result.get("updated_preference"), dict):
        attributes["updated_preference"] = result.get("updated_preference")
    if isinstance(result.get("selection_scope"), dict):
        attributes["selection_scope"] = result.get("selection_scope")
    if result.get("response_data") is not None:
        attributes["response_data"] = result.get("response_data")
    return {key: value for key, value in attributes.items() if value is not None}
