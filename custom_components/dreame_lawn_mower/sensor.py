"""Sensors for Dreame lawn mower."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .control_options import (  # noqa: F401
    MOWING_ACTION_EDGE,
    MOWING_ACTION_SPOT,
    MOWING_ACTION_ZONE,
    contour_label,
    current_contour_entries,
    current_spot_entries,
    current_zone_entries,
    map_entries,
    mowing_action_label,
    spot_label,
    zone_label,
)
from .control_options import (
    current_map_index as selected_current_map_index,  # noqa: F401
)
from .coordinator import (
    DreameLawnMowerCoordinator,
    runtime_tracking_active,  # noqa: F401
)
from .dreame_lawn_mower_client.const import STATE_CODE_TO_STATE
from .dreame_lawn_mower_client.maintenance import (  # noqa: F401
    MAINTENANCE_ITEMS,
    MaintenanceItem,
    maintenance_item_status,
    maintenance_status_attributes,
)
from .dreame_lawn_mower_client.models import _mower_terminology
from .entity import DreameLawnMowerEntity
from .manual_control import remote_control_block_reason
from .runtime_cache import (
    DreameLawnMowerRuntimeTelemetryCache as DreameLawnMowerRuntimeTelemetryCache,
)

# Keep historical ``sensor`` imports working while implementations live in
# focused modules. These are intentional compatibility re-exports.
from .sensor_diagnostics import (  # noqa: F401
    DreameLawnMowerConfiguredScheduleCountSensor,
    DreameLawnMowerFirmwareUpdateStatusSensor,
    DreameLawnMowerLastBatchDeviceDataProbeSensor,
    DreameLawnMowerLastPreferenceProbeSensor,
    DreameLawnMowerLastScheduleProbeSensor,
    DreameLawnMowerLastTaskStatusProbeSensor,
    DreameLawnMowerLastWeatherProbeSensor,
    DreameLawnMowerPreferenceMapCountSensor,
    DreameLawnMowerRainDelayEndTimeSensor,
    DreameLawnMowerRainProtectionDurationSensor,
    DreameLawnMowerWeatherProtectionStatusSensor,
    _batch_device_data_probe_state,
    _batch_ota_probe_summary,
    _batch_ota_section,
    _batch_ota_status_name,
    _batch_preference_map_count,
    _batch_preference_probe_summary,
    _batch_preference_section,
    _batch_schedule_count,
    _batch_schedule_probe_summary,
    _batch_schedule_section,
    _preference_probe_entry_summary,
    _preference_probe_map_summary,
    _preference_probe_state,
    _schedule_probe_entry_summary,
    _schedule_probe_state,
    _weather_probe_state,
    _weather_section,
    batch_device_data_probe_result_attributes,
    batch_ota_attributes,
    batch_preference_attributes,
    batch_schedule_attributes,
    preference_probe_result_attributes,
    schedule_probe_result_attributes,
    weather_probe_result_attributes,
)
from .sensor_map_data import (  # noqa: F401
    _app_map_count,
    _app_map_entry_summary,
    _app_map_object_count,
    _app_map_object_section,
    _app_map_object_summary,
    _app_maps_summary,
    _available_vector_map_count,
    _coordinate_path_length_m,
    _current_app_map_cut_relation_count,
    _current_app_map_index,
    _current_app_map_spot_count,
    _current_app_map_summary,
    _current_app_map_total_area,
    _current_app_map_trajectory_length_m,
    _current_app_map_trajectory_point_count,
    _current_app_map_zone_count,
    _current_vector_map_contour_count,
    _current_vector_map_id,
    _current_vector_map_mow_path_length_m,
    _current_vector_map_mow_path_point_count,
    _current_vector_map_name,
    _current_vector_map_runtime_track_length_m,
    _current_vector_map_runtime_track_point_count,
    _current_vector_map_runtime_track_segment_count,
    _current_vector_map_summary,
    _selected_contour_id,
    _selected_map_index,
    _selected_map_label,
    _selected_map_preference_summary,
    _selected_map_preference_value,
    _selected_mowing_action_label,
    _selected_run_scope_attributes,
    _selected_target_label,
    _selected_target_summary,
    _selected_zone_preference_summary,
    _selected_zone_preference_value,
    _vector_map_entry_summary,
    _vector_map_summary,
    app_map_attributes,
    app_map_object_attributes,
    current_app_map_attributes,
    current_vector_map_attributes,
    vector_map_attributes,
)
from .sensor_map_entities import (  # noqa: F401
    DreameLawnMowerAppMapCountSensor,
    DreameLawnMowerAppMapObjectCountSensor,
    DreameLawnMowerAvailableVectorMapCountSensor,
    DreameLawnMowerCurrentAppMapAreaSensor,
    DreameLawnMowerCurrentAppMapCutRelationCountSensor,
    DreameLawnMowerCurrentAppMapEdgeCountSensor,
    DreameLawnMowerCurrentAppMapIndexSensor,
    DreameLawnMowerCurrentAppMapMowPathLengthSensor,
    DreameLawnMowerCurrentAppMapMowPathPointCountSensor,
    DreameLawnMowerCurrentAppMapSpotCountSensor,
    DreameLawnMowerCurrentAppMapTrajectoryLengthSensor,
    DreameLawnMowerCurrentAppMapTrajectoryPointCountSensor,
    DreameLawnMowerCurrentAppMapZoneCountSensor,
    DreameLawnMowerCurrentVectorMapIdSensor,
    DreameLawnMowerCurrentVectorMapNameSensor,
    DreameLawnMowerSelectedMapPreferenceAreaCountSensor,
    DreameLawnMowerSelectedMapPreferenceCountSensor,
    DreameLawnMowerSelectedMapPreferenceModeSensor,
    DreameLawnMowerSelectedMapSensor,
    DreameLawnMowerSelectedMowingActionSensor,
    DreameLawnMowerSelectedTargetSensor,
    DreameLawnMowerSelectedZoneDirectionModeSensor,
    DreameLawnMowerSelectedZoneEfficiencyModeSensor,
    DreameLawnMowerSelectedZoneMowingHeightSensor,
    DreameLawnMowerSelectedZoneObstacleAvoidanceSensor,
    DreameLawnMowerSelectedZoneObstacleClassSensor,
    DreameLawnMowerSelectedZoneObstacleDistanceSensor,
    DreameLawnMowerSelectedZoneObstacleHeightSensor,
)
from .sensor_operations import (  # noqa: F401
    DreameLawnMowerLastMaintenanceResetSensor,
    DreameLawnMowerLastPreferenceWriteSensor,
    DreameLawnMowerLastScheduleWriteSensor,
    DreameLawnMowerMaintenanceRemainingSensor,
    _maintenance_reset_state,
    _preference_write_state,
    _schedule_write_state,
    maintenance_reset_result_attributes,
    preference_write_result_attributes,
    schedule_write_result_attributes,
)
from .sensor_runtime import (  # noqa: F401
    DreameLawnMowerMowingProgressSensor,
    DreameLawnMowerRuntimeCurrentAreaSensor,
    DreameLawnMowerRuntimeHeadingSensor,
    DreameLawnMowerRuntimeMissionProgressSensor,
    DreameLawnMowerRuntimePositionXSensor,
    DreameLawnMowerRuntimePositionYSensor,
    DreameLawnMowerRuntimeTotalAreaSensor,
    DreameLawnMowerRuntimeTrackLengthSensor,
    DreameLawnMowerRuntimeTrackPointCountSensor,
    DreameLawnMowerRuntimeTrackSegmentCountSensor,
    _current_zone_label,
    _runtime_progress_available_for_snapshot,
    _runtime_session_attributes,
    _runtime_session_blob,
    _runtime_status_blob_current_area_sqm,
    _runtime_status_blob_heading_deg,
    _runtime_status_blob_pose_x,
    _runtime_status_blob_pose_y,
    _runtime_status_blob_progress_percent,
    _runtime_status_blob_summary,
    _runtime_status_blob_total_area_sqm,
)
from .task_status_probe import (  # noqa: F401
    task_status_probe_result_attributes,
    task_status_probe_state,
)


@dataclass(frozen=True, slots=True)
class DreameSensorDescription:
    key: str
    name: str
    value_fn: Callable[[Any], Any]
    exists_fn: Callable[[Any], bool] = lambda _: True
    entity_registry_enabled_default: bool = True
    entity_registry_visible_default: bool = True
    translation_key: str | None = None
    translation_placeholders: dict[str, str] | None = None
    force_update: bool = False
    device_class: SensorDeviceClass | None = None
    unit_of_measurement: str | None = None
    native_unit_of_measurement: str | None = None
    suggested_unit_of_measurement: str | None = None
    suggested_display_precision: int | None = None
    state_class: SensorStateClass | str | None = None
    last_reset: datetime | None = None
    options: list[str] | None = None
    icon: str | None = None
    entity_category: EntityCategory | None = None


def _raw_attribute(snapshot: Any, key: str) -> Any:
    """Return a raw mower attribute when available."""
    return snapshot.raw_attributes.get(key)


MOWER_STATE_OPTIONS = list(
    dict.fromkeys(
        _mower_terminology(state) or state for state in STATE_CODE_TO_STATE.values()
    )
)


SENSORS = [
    DreameSensorDescription(
        key="activity",
        name="Activity",
        value_fn=lambda snapshot: snapshot.activity,
        icon="mdi:robot-mower",
    ),
    DreameSensorDescription(
        key="state_name",
        name="State Name",
        value_fn=lambda snapshot: snapshot.mower_state_name,
        translation_key="state_name",
        device_class=SensorDeviceClass.ENUM,
        options=MOWER_STATE_OPTIONS,
        icon="mdi:state-machine",
    ),
    DreameSensorDescription(
        key="task_status",
        name="Task Status",
        value_fn=lambda snapshot: snapshot.mowing_task_status_name or "unknown",
        icon="mdi:clipboard-text-clock-outline",
    ),
    DreameSensorDescription(
        key="battery",
        name="Battery",
        value_fn=lambda snapshot: snapshot.battery_level,
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement="%",
    ),
    DreameSensorDescription(
        key="error",
        name="Error",
        value_fn=lambda snapshot: snapshot.error_display or "none",
        icon="mdi:alert-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameSensorDescription(
        key="error_code",
        name="Error Code",
        value_fn=lambda snapshot: (
            "none" if snapshot.error_code in (None, -1) else snapshot.error_code
        ),
        icon="mdi:numeric",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    DreameSensorDescription(
        key="status_notice",
        name="Status Notice",
        value_fn=lambda snapshot: snapshot.status_notice_display or "none",
        icon="mdi:information-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameSensorDescription(
        key="raw_error",
        name="Raw Error",
        value_fn=lambda snapshot: getattr(snapshot, "error_text", None) or "none",
        icon="mdi:text-box-search-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    DreameSensorDescription(
        key="firmware_version",
        name="Firmware Version",
        value_fn=lambda snapshot: snapshot.firmware_version,
        icon="mdi:package-up",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    DreameSensorDescription(
        key="hardware_version",
        name="Hardware Version",
        value_fn=lambda snapshot: snapshot.hardware_version,
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    DreameSensorDescription(
        key="serial_number",
        name="Serial Number",
        value_fn=lambda snapshot: snapshot.serial_number,
        exists_fn=lambda snapshot: bool(snapshot.serial_number),
        icon="mdi:barcode",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    DreameSensorDescription(
        key="cloud_update_time",
        name="Cloud Update Time",
        value_fn=lambda snapshot: snapshot.cloud_update_time,
        exists_fn=lambda snapshot: bool(snapshot.cloud_update_time),
        icon="mdi:clock-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    DreameSensorDescription(
        key="unknown_property_count",
        name="Unknown Property Count",
        value_fn=lambda snapshot: getattr(snapshot, "unknown_property_count", 0),
        icon="mdi:help-box-multiple-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    DreameSensorDescription(
        key="realtime_property_count",
        name="Realtime Property Count",
        value_fn=lambda snapshot: getattr(snapshot, "realtime_property_count", 0),
        icon="mdi:transit-connection-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    DreameSensorDescription(
        key="last_realtime_method",
        name="Last Realtime Method",
        value_fn=lambda snapshot: getattr(snapshot, "last_realtime_method", None),
        icon="mdi:message-badge-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    DreameSensorDescription(
        key="manual_drive_block_reason",
        name="Manual Drive Block Reason",
        value_fn=lambda snapshot: remote_control_block_reason(snapshot) or "none",
        icon="mdi:shield-alert-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    DreameSensorDescription(
        key="cleaning_mode",
        name="Mowing Mode",
        value_fn=lambda snapshot: snapshot.mowing_mode_name,
        exists_fn=lambda snapshot: (
            bool(snapshot.mowing_mode_name) and snapshot.mowing_mode_name != "unknown"
        ),
        icon="mdi:grass",
        entity_registry_enabled_default=False,
    ),
    DreameSensorDescription(
        key="current_cleaned_area",
        name="Current Mowed Area",
        value_fn=lambda snapshot: snapshot.mowed_area,
        exists_fn=lambda snapshot: snapshot.mowed_area is not None,
        icon="mdi:texture-box",
        native_unit_of_measurement="m²",
    ),
    DreameSensorDescription(
        key="current_cleaning_time",
        name="Current Mowing Time",
        value_fn=lambda snapshot: snapshot.mowing_time,
        exists_fn=lambda snapshot: snapshot.mowing_time is not None,
        icon="mdi:timer-sand",
        native_unit_of_measurement="min",
    ),
    DreameSensorDescription(
        key="active_segment_count",
        name="Active Segment Count",
        value_fn=lambda snapshot: getattr(snapshot, "active_segment_count", None),
        exists_fn=lambda snapshot: (
            getattr(snapshot, "active_segment_count", None) is not None
        ),
        icon="mdi:vector-square",
    ),
    DreameSensorDescription(
        key="current_zone",
        name="Current Zone",
        value_fn=_current_zone_label,
        exists_fn=lambda snapshot: bool(_current_zone_label(snapshot)),
        icon="mdi:map-marker-outline",
    ),
    DreameSensorDescription(
        key="mower_state",
        name="Mower State",
        value_fn=lambda snapshot: snapshot.mower_state,
        exists_fn=lambda snapshot: bool(snapshot.mower_state),
        icon="mdi:robot-mower-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up mower sensors."""
    coordinator: DreameLawnMowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [DreameLawnMowerSensor(coordinator, description) for description in SENSORS]
        + [DreameLawnMowerAppMapCountSensor(coordinator)]
        + [DreameLawnMowerAvailableVectorMapCountSensor(coordinator)]
        + [DreameLawnMowerSelectedMowingActionSensor(coordinator)]
        + [DreameLawnMowerSelectedMapSensor(coordinator)]
        + [DreameLawnMowerSelectedMapPreferenceModeSensor(coordinator)]
        + [DreameLawnMowerSelectedMapPreferenceAreaCountSensor(coordinator)]
        + [DreameLawnMowerSelectedMapPreferenceCountSensor(coordinator)]
        + [DreameLawnMowerSelectedTargetSensor(coordinator)]
        + [DreameLawnMowerSelectedZoneMowingHeightSensor(coordinator)]
        + [DreameLawnMowerSelectedZoneEfficiencyModeSensor(coordinator)]
        + [DreameLawnMowerSelectedZoneDirectionModeSensor(coordinator)]
        + [DreameLawnMowerSelectedZoneObstacleAvoidanceSensor(coordinator)]
        + [DreameLawnMowerSelectedZoneObstacleDistanceSensor(coordinator)]
        + [DreameLawnMowerSelectedZoneObstacleHeightSensor(coordinator)]
        + [DreameLawnMowerSelectedZoneObstacleClassSensor(coordinator)]
        + [DreameLawnMowerCurrentAppMapIndexSensor(coordinator)]
        + [DreameLawnMowerCurrentVectorMapNameSensor(coordinator)]
        + [DreameLawnMowerCurrentAppMapAreaSensor(coordinator)]
        + [DreameLawnMowerCurrentAppMapZoneCountSensor(coordinator)]
        + [DreameLawnMowerCurrentAppMapSpotCountSensor(coordinator)]
        + [DreameLawnMowerCurrentVectorMapIdSensor(coordinator)]
        + [DreameLawnMowerCurrentAppMapEdgeCountSensor(coordinator)]
        + [DreameLawnMowerCurrentAppMapTrajectoryPointCountSensor(coordinator)]
        + [DreameLawnMowerCurrentAppMapTrajectoryLengthSensor(coordinator)]
        + [DreameLawnMowerCurrentAppMapCutRelationCountSensor(coordinator)]
        + [DreameLawnMowerCurrentAppMapMowPathPointCountSensor(coordinator)]
        + [DreameLawnMowerCurrentAppMapMowPathLengthSensor(coordinator)]
        + [DreameLawnMowerRuntimeMissionProgressSensor(coordinator)]
        + [DreameLawnMowerRuntimeCurrentAreaSensor(coordinator)]
        + [DreameLawnMowerRuntimeTotalAreaSensor(coordinator)]
        + [DreameLawnMowerRuntimePositionXSensor(coordinator)]
        + [DreameLawnMowerRuntimePositionYSensor(coordinator)]
        + [DreameLawnMowerRuntimeHeadingSensor(coordinator)]
        + [DreameLawnMowerRuntimeTrackPointCountSensor(coordinator)]
        + [DreameLawnMowerRuntimeTrackLengthSensor(coordinator)]
        + [DreameLawnMowerRuntimeTrackSegmentCountSensor(coordinator)]
        + [DreameLawnMowerMowingProgressSensor(coordinator)]
        + [DreameLawnMowerAppMapObjectCountSensor(coordinator)]
        + [DreameLawnMowerFirmwareUpdateStatusSensor(coordinator)]
        + [DreameLawnMowerConfiguredScheduleCountSensor(coordinator)]
        + [DreameLawnMowerPreferenceMapCountSensor(coordinator)]
        + [DreameLawnMowerWeatherProtectionStatusSensor(coordinator)]
        + [DreameLawnMowerRainProtectionDurationSensor(coordinator)]
        + [DreameLawnMowerRainDelayEndTimeSensor(coordinator)]
        + [
            DreameLawnMowerMaintenanceRemainingSensor(coordinator, item)
            for item in MAINTENANCE_ITEMS
        ]
        + [DreameLawnMowerLastMaintenanceResetSensor(coordinator)]
        + [DreameLawnMowerLastPreferenceWriteSensor(coordinator)]
        + [DreameLawnMowerLastScheduleWriteSensor(coordinator)]
        + [DreameLawnMowerLastBatchDeviceDataProbeSensor(coordinator)]
        + [DreameLawnMowerLastScheduleProbeSensor(coordinator)]
        + [DreameLawnMowerLastTaskStatusProbeSensor(coordinator)]
        + [DreameLawnMowerLastPreferenceProbeSensor(coordinator)]
        + [DreameLawnMowerLastWeatherProbeSensor(coordinator)]
    )


class DreameLawnMowerSensor(DreameLawnMowerEntity, SensorEntity):
    """Simple coordinator-backed mower sensor."""

    def __init__(
        self,
        coordinator: DreameLawnMowerCoordinator,
        description: DreameSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{self._descriptor.unique_id}_{description.key}"
        self._attr_name = description.name
        self._attr_device_class = description.device_class
        self._attr_native_unit_of_measurement = description.native_unit_of_measurement
        self._attr_translation_key = description.translation_key
        self._attr_translation_placeholders = description.translation_placeholders
        self._attr_options = description.options
        self._attr_icon = description.icon
        self._attr_entity_category = description.entity_category
        self._attr_entity_registry_enabled_default = (
            description.entity_registry_enabled_default
        )
        self._attr_entity_registry_visible_default = (
            description.entity_registry_visible_default
        )

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        if not self.available:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        """Return whether the sensor currently has meaningful mower data."""
        snapshot = self.coordinator.data
        return snapshot is not None and self.entity_description.exists_fn(snapshot)
