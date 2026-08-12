"""Map and selection sensor entities for Dreame lawn mower."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import EntityCategory

from .coordinator import DreameLawnMowerCoordinator
from .entity import DreameLawnMowerEntity
from .sensor_map_data import (
    _app_map_count,
    _app_map_object_count,
    _available_vector_map_count,
    _current_app_map_cut_relation_count,
    _current_app_map_index,
    _current_app_map_spot_count,
    _current_app_map_total_area,
    _current_app_map_trajectory_length_m,
    _current_app_map_trajectory_point_count,
    _current_app_map_zone_count,
    _current_vector_map_contour_count,
    _current_vector_map_id,
    _current_vector_map_mow_path_length_m,
    _current_vector_map_mow_path_point_count,
    _current_vector_map_name,
    _current_vector_map_zone_count,
    _selected_map_label,
    _selected_map_preference_summary,
    _selected_map_preference_value,
    _selected_mowing_action_label,
    _selected_run_scope_attributes,
    _selected_target_label,
    _selected_zone_preference_summary,
    _selected_zone_preference_value,
    app_map_attributes,
    app_map_object_attributes,
    current_app_map_attributes,
    current_vector_map_attributes,
    vector_map_attributes,
)


class DreameLawnMowerAppMapObjectCountSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose cached 3D app-map object metadata."""

    _attr_name = "3D Map Object Count"
    _attr_icon = "mdi:cube-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_app_map_object_count"

    @property
    def native_value(self) -> int | None:
        """Return the number of cached 3D map objects."""
        return _app_map_object_count(self.coordinator.app_map_objects)

    @property
    def available(self) -> bool:
        """Return whether cached 3D map object data is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached 3D map object attributes."""
        return app_map_object_attributes(self.coordinator.app_map_objects)


class DreameLawnMowerAppMapCountSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the number of cached app maps."""

    _attr_name = "App Map Count"
    _attr_icon = "mdi:map-marker-multiple"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_app_map_count"

    @property
    def native_value(self) -> int | None:
        """Return the number of cached app maps."""
        return _app_map_count(self.coordinator.app_maps)

    @property
    def available(self) -> bool:
        """Return whether cached app-map metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached app-map attributes."""
        return app_map_attributes(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )


class DreameLawnMowerAvailableVectorMapCountSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the number of cached vector maps."""

    _attr_name = "Available Vector Map Count"
    _attr_icon = "mdi:map-marker-multiple-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_available_vector_map_count"
        )

    @property
    def native_value(self) -> int | None:
        """Return the number of cached vector maps."""
        return _available_vector_map_count(self.coordinator.vector_map_details)

    @property
    def available(self) -> bool:
        """Return whether cached vector-map metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached vector-map attributes."""
        return vector_map_attributes(self.coordinator.vector_map_details)


class DreameLawnMowerCurrentAppMapIndexSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the current cached app map index."""

    _attr_name = "Current App Map Index"
    _attr_icon = "mdi:map-marker-path"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_current_app_map_index"

    @property
    def native_value(self) -> int | None:
        """Return the current app map index."""
        return _current_app_map_index(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )

    @property
    def available(self) -> bool:
        """Return whether cached app-map metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached current-map attributes."""
        return current_app_map_attributes(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )


class DreameLawnMowerSelectedMowingActionSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the currently selected mowing action label."""

    _attr_name = "Selected Mowing Action"
    _attr_icon = "mdi:play-box-outline"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_selected_mowing_action"

    @property
    def native_value(self) -> str | None:
        """Return the selected mowing action label."""
        return _selected_mowing_action_label(self.coordinator)

    @property
    def available(self) -> bool:
        """Return whether selected action metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the selected-run metadata used for this sensor."""
        return _selected_run_scope_attributes(self.coordinator)


class DreameLawnMowerSelectedMapSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the currently selected app map label."""

    _attr_name = "Selected Map"
    _attr_icon = "mdi:map-marker-radius"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_selected_map"

    @property
    def native_value(self) -> str | None:
        """Return the selected app map label."""
        return _selected_map_label(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
            getattr(self.coordinator, "selected_map_index", None),
        )

    @property
    def available(self) -> bool:
        """Return whether selected map metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the selected-run metadata used for this sensor."""
        return _selected_run_scope_attributes(self.coordinator)


class DreameLawnMowerSelectedMapPreferenceModeSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the selected/current map preference mode."""

    _attr_name = "Selected Map Preference Mode"
    _attr_icon = "mdi:tune-variant"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_selected_map_preference_mode"
        )

    @property
    def native_value(self) -> str | None:
        """Return the selected/current map preference mode label."""
        value = _selected_map_preference_value(self.coordinator, "mode_name")
        return value if isinstance(value, str) and value.strip() else None

    @property
    def available(self) -> bool:
        """Return whether selected/current map preference data is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the compact selected/current map preference summary."""
        return _selected_map_preference_summary(self.coordinator)


class DreameLawnMowerSelectedMapPreferenceAreaCountSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the selected/current map preference area count."""

    _attr_name = "Selected Map Preference Area Count"
    _attr_icon = "mdi:map-marker-multiple-outline"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_selected_map_preference_area_count"
        )

    @property
    def native_value(self) -> int | None:
        """Return the selected/current map preference area count."""
        value = _selected_map_preference_value(self.coordinator, "area_count")
        return value if isinstance(value, int) else None

    @property
    def available(self) -> bool:
        """Return whether selected/current map preference data is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the compact selected/current map preference summary."""
        return _selected_map_preference_summary(self.coordinator)


class DreameLawnMowerSelectedMapPreferenceCountSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the selected/current map decoded preference count."""

    _attr_name = "Selected Map Preference Count"
    _attr_icon = "mdi:format-list-numbered"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_selected_map_preference_count"
        )

    @property
    def native_value(self) -> int | None:
        """Return the selected/current map decoded preference count."""
        value = _selected_map_preference_value(self.coordinator, "preference_count")
        return value if isinstance(value, int) else None

    @property
    def available(self) -> bool:
        """Return whether selected/current map preference data is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the compact selected/current map preference summary."""
        return _selected_map_preference_summary(self.coordinator)


class DreameLawnMowerSelectedTargetSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the currently selected scoped mowing target label."""

    _attr_name = "Selected Target"
    _attr_icon = "mdi:crosshairs-gps"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_selected_target"

    @property
    def native_value(self) -> str | None:
        """Return the selected zone, spot, or edge label."""
        return _selected_target_label(self.coordinator)

    @property
    def available(self) -> bool:
        """Return whether a scoped target is selected."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the selected-run metadata used for this sensor."""
        return _selected_run_scope_attributes(self.coordinator)


class DreameLawnMowerSelectedZoneMowingHeightSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the selected/current zone mowing height."""

    _attr_name = "Selected Zone Mowing Height"
    _attr_icon = "mdi:ruler"
    _attr_native_unit_of_measurement = "cm"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_selected_zone_mowing_height"
        )

    @property
    def native_value(self) -> float | int | None:
        """Return the selected/current zone mowing height in centimeters."""
        value = _selected_zone_preference_value(self.coordinator, "mowing_height_cm")
        return value if isinstance(value, int | float) else None

    @property
    def available(self) -> bool:
        """Return whether selected/current zone preference data is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the compact selected/current zone preference summary."""
        return _selected_zone_preference_summary(self.coordinator)


class DreameLawnMowerSelectedZoneEfficiencyModeSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the selected/current zone efficiency mode."""

    _attr_name = "Selected Zone Efficiency Mode"
    _attr_icon = "mdi:run-fast"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_selected_zone_efficiency_mode"
        )

    @property
    def native_value(self) -> str | None:
        """Return the selected/current zone efficiency mode label."""
        value = _selected_zone_preference_value(self.coordinator, "efficient_mode_name")
        return value if isinstance(value, str) and value.strip() else None

    @property
    def available(self) -> bool:
        """Return whether selected/current zone preference data is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the compact selected/current zone preference summary."""
        return _selected_zone_preference_summary(self.coordinator)


class DreameLawnMowerSelectedZoneDirectionModeSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the selected/current zone mowing direction mode."""

    _attr_name = "Selected Zone Direction Mode"
    _attr_icon = "mdi:compass-rose"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_selected_zone_direction_mode"
        )

    @property
    def native_value(self) -> str | None:
        """Return the selected/current zone mowing direction mode label."""
        value = _selected_zone_preference_value(
            self.coordinator,
            "mowing_direction_mode_name",
        )
        return value if isinstance(value, str) and value.strip() else None

    @property
    def available(self) -> bool:
        """Return whether selected/current zone preference data is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the compact selected/current zone preference summary."""
        return _selected_zone_preference_summary(self.coordinator)


class DreameLawnMowerSelectedZoneObstacleAvoidanceSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose whether obstacle avoidance is enabled for the selected/current zone."""

    _attr_name = "Selected Zone Obstacle Avoidance"
    _attr_icon = "mdi:shield-check-outline"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_selected_zone_obstacle_avoidance"
        )

    @property
    def native_value(self) -> str | None:
        """Return whether obstacle avoidance is enabled for the zone."""
        value = _selected_zone_preference_value(
            self.coordinator,
            "obstacle_avoidance_enabled",
        )
        if isinstance(value, bool):
            return "enabled" if value else "disabled"
        return None

    @property
    def available(self) -> bool:
        """Return whether selected/current zone preference data is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the compact selected/current zone preference summary."""
        return _selected_zone_preference_summary(self.coordinator)


class DreameLawnMowerSelectedZoneObstacleDistanceSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose obstacle avoidance distance for the selected/current zone."""

    _attr_name = "Selected Zone Obstacle Distance"
    _attr_icon = "mdi:map-marker-distance"
    _attr_native_unit_of_measurement = "cm"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_selected_zone_obstacle_distance"
        )

    @property
    def native_value(self) -> float | int | None:
        """Return obstacle avoidance distance in centimeters."""
        value = _selected_zone_preference_value(
            self.coordinator,
            "obstacle_avoidance_distance_cm",
        )
        return value if isinstance(value, int | float) else None

    @property
    def available(self) -> bool:
        """Return whether selected/current zone preference data is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the compact selected/current zone preference summary."""
        return _selected_zone_preference_summary(self.coordinator)


class DreameLawnMowerSelectedZoneObstacleHeightSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose obstacle avoidance height for the selected/current zone."""

    _attr_name = "Selected Zone Obstacle Height"
    _attr_icon = "mdi:arrow-expand-vertical"
    _attr_native_unit_of_measurement = "cm"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_selected_zone_obstacle_height"
        )

    @property
    def native_value(self) -> float | int | None:
        """Return obstacle avoidance height in centimeters."""
        value = _selected_zone_preference_value(
            self.coordinator,
            "obstacle_avoidance_height_cm",
        )
        return value if isinstance(value, int | float) else None

    @property
    def available(self) -> bool:
        """Return whether selected/current zone preference data is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the compact selected/current zone preference summary."""
        return _selected_zone_preference_summary(self.coordinator)


class DreameLawnMowerSelectedZoneObstacleClassSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the enabled AI obstacle classes for the selected/current zone."""

    _attr_name = "Selected Zone Obstacle Classes"
    _attr_icon = "mdi:shape-outline"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_selected_zone_obstacle_classes"
        )

    @property
    def native_value(self) -> str | None:
        """Return a comma-separated list of enabled AI obstacle classes."""
        value = _selected_zone_preference_value(
            self.coordinator,
            "obstacle_avoidance_ai_classes",
        )
        if not isinstance(value, list):
            return None
        labels = [
            item.replace("_", " ").strip().title()
            for item in value
            if isinstance(item, str) and item.strip()
        ]
        return ", ".join(labels) if labels else None

    @property
    def available(self) -> bool:
        """Return whether selected/current zone preference data is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the compact selected/current zone preference summary."""
        return _selected_zone_preference_summary(self.coordinator)


class DreameLawnMowerCurrentVectorMapNameSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the current vector-map name."""

    _attr_name = "Current Vector Map Name"
    _attr_icon = "mdi:map-search-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_current_vector_map_name"

    @property
    def native_value(self) -> str | None:
        """Return the current vector-map name."""
        return _current_vector_map_name(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )

    @property
    def available(self) -> bool:
        """Return whether current vector-map metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached current vector-map attributes."""
        return current_vector_map_attributes(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )


class DreameLawnMowerCurrentVectorMapIdSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the current vector-map id."""

    _attr_name = "Current Vector Map ID"
    _attr_icon = "mdi:identifier"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_current_vector_map_id"

    @property
    def native_value(self) -> int | None:
        """Return the current vector-map id."""
        return _current_vector_map_id(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )

    @property
    def available(self) -> bool:
        """Return whether current vector-map metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached current vector-map attributes."""
        return current_vector_map_attributes(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )


class DreameLawnMowerCurrentAppMapAreaSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the total area of the current cached app map."""

    _attr_name = "Current App Map Area"
    _attr_icon = "mdi:texture-box"
    _attr_native_unit_of_measurement = "m²"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_current_app_map_area"

    @property
    def native_value(self) -> float | int | None:
        """Return the total area of the current app map."""
        return _current_app_map_total_area(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )

    @property
    def available(self) -> bool:
        """Return whether current-map area metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached current-map attributes."""
        return current_app_map_attributes(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )


class DreameLawnMowerCurrentAppMapZoneCountSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the zone count of the current cached app map."""

    _attr_name = "Current App Map Zone Count"
    _attr_icon = "mdi:vector-square"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_current_app_map_zone_count"
        )

    @property
    def native_value(self) -> int | None:
        """Return the zone count of the current app map."""
        vector_count = _current_vector_map_zone_count(
            getattr(self.coordinator, "vector_map_details", None),
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )
        if vector_count is not None:
            return vector_count
        return _current_app_map_zone_count(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )

    @property
    def available(self) -> bool:
        """Return whether current-map zone metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached current-map attributes."""
        attributes = current_app_map_attributes(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )
        if (
            _current_vector_map_zone_count(
                getattr(self.coordinator, "vector_map_details", None),
                self.coordinator.app_maps,
                self.coordinator.batch_device_data,
            )
            is not None
        ):
            vector_attributes = current_vector_map_attributes(
                self.coordinator.vector_map_details,
                self.coordinator.app_maps,
                self.coordinator.batch_device_data,
            )
            attributes["zone_count_source"] = "vector_map"
            if "current_vector_map" in vector_attributes:
                attributes["current_vector_map"] = vector_attributes[
                    "current_vector_map"
                ]
        return attributes


class DreameLawnMowerCurrentAppMapSpotCountSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the spot count of the current cached app map."""

    _attr_name = "Current App Map Spot Count"
    _attr_icon = "mdi:map-marker-radius-outline"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_current_app_map_spot_count"
        )

    @property
    def native_value(self) -> int | None:
        """Return the spot count of the current app map."""
        return _current_app_map_spot_count(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )

    @property
    def available(self) -> bool:
        """Return whether current-map spot metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached current-map attributes."""
        return current_app_map_attributes(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )


class DreameLawnMowerCurrentAppMapEdgeCountSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the edge contour count of the current vector map."""

    _attr_name = "Current App Map Edge Count"
    _attr_icon = "mdi:vector-polyline"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_current_app_map_edge_count"
        )

    @property
    def native_value(self) -> int | None:
        """Return the edge contour count of the current vector map."""
        return _current_vector_map_contour_count(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )

    @property
    def available(self) -> bool:
        """Return whether current-map vector contour metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached current vector-map attributes."""
        return current_vector_map_attributes(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )


class DreameLawnMowerCurrentAppMapTrajectoryPointCountSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the live trajectory point count of the current cached app map."""

    _attr_name = "Current App Map Trajectory Point Count"
    _attr_icon = "mdi:map-marker-path"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_current_app_map_trajectory_point_count"
        )

    @property
    def native_value(self) -> int | None:
        """Return the trajectory point count of the current app map."""
        return _current_app_map_trajectory_point_count(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )

    @property
    def available(self) -> bool:
        """Return whether current-map trajectory metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached current-map attributes."""
        return current_app_map_attributes(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )


class DreameLawnMowerCurrentAppMapTrajectoryLengthSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the live trajectory length of the current cached app map."""

    _attr_name = "Current App Map Trajectory Length"
    _attr_icon = "mdi:ruler"
    _attr_native_unit_of_measurement = "m"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_current_app_map_trajectory_length"
        )

    @property
    def native_value(self) -> float | int | None:
        """Return the approximate trajectory length in meters."""
        return _current_app_map_trajectory_length_m(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )

    @property
    def available(self) -> bool:
        """Return whether current-map trajectory metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached current-map attributes."""
        return current_app_map_attributes(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )


class DreameLawnMowerCurrentAppMapCutRelationCountSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the cut-relation count of the current cached app map."""

    _attr_name = "Current App Map Cut Relation Count"
    _attr_icon = "mdi:vector-polyline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_current_app_map_cut_relation_count"
        )

    @property
    def native_value(self) -> int | None:
        """Return the cut-relation count of the current app map."""
        return _current_app_map_cut_relation_count(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )

    @property
    def available(self) -> bool:
        """Return whether current-map cut-relation metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached current-map attributes."""
        return current_app_map_attributes(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )


class DreameLawnMowerCurrentAppMapMowPathPointCountSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the live mow-path point count of the current vector map."""

    _attr_name = "Current App Map Mow Path Point Count"
    _attr_icon = "mdi:route"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_current_app_map_mow_path_point_count"
        )

    @property
    def native_value(self) -> int | None:
        """Return the live mow-path point count of the current vector map."""
        return _current_vector_map_mow_path_point_count(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )

    @property
    def available(self) -> bool:
        """Return whether current-map vector mow-path metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached current vector-map attributes."""
        return current_vector_map_attributes(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )


class DreameLawnMowerCurrentAppMapMowPathLengthSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the live mow-path length of the current vector map."""

    _attr_name = "Current App Map Mow Path Length"
    _attr_icon = "mdi:ruler-square"
    _attr_native_unit_of_measurement = "m"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_current_app_map_mow_path_length"
        )

    @property
    def native_value(self) -> float | int | None:
        """Return the approximate live mow-path length in meters."""
        return _current_vector_map_mow_path_length_m(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )

    @property
    def available(self) -> bool:
        """Return whether current-map vector mow-path metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached current vector-map attributes."""
        return current_vector_map_attributes(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )
