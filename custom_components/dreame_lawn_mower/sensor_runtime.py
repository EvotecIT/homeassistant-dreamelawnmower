"""Runtime telemetry sensors for Dreame lawn mower."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import EntityCategory

from .coordinator import DreameLawnMowerCoordinator, runtime_tracking_active
from .entity import DreameLawnMowerEntity
from .runtime_cache import DreameLawnMowerRuntimeTelemetryCache
from .sensor_map_data import (
    _coordinate_path_length_m,
    _current_app_map_summary,
    _current_app_map_total_area,
    _current_vector_map_runtime_track_length_m,
    _current_vector_map_runtime_track_point_count,
    _current_vector_map_runtime_track_segment_count,
    current_vector_map_attributes,
)


def _current_zone_label(snapshot: Any) -> str | None:
    """Return a friendly current-zone label for live mower state."""
    zone_name = getattr(snapshot, "current_zone_name", None)
    if isinstance(zone_name, str) and zone_name.strip():
        return zone_name.strip()

    zone_id = getattr(snapshot, "current_zone_id", None)
    if isinstance(zone_id, int):
        return f"Zone {zone_id}"
    return None


class DreameLawnMowerMowingProgressSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose a calculated mowing progress percentage for the active map."""

    _attr_name = "Mowing Progress"
    _attr_icon = "mdi:progress-check"
    _attr_native_unit_of_measurement = "%"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_mowing_progress"

    @property
    def native_value(self) -> float | int | None:
        """Return the current mowing progress percentage."""
        snapshot = self.coordinator.data
        mowed_area = None if snapshot is None else snapshot.mowed_area
        current_map_area = _current_app_map_total_area(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )
        if mowed_area is None or current_map_area in (None, 0):
            return None
        progress = (float(mowed_area) / float(current_map_area)) * 100
        return round(max(0.0, min(progress, 100.0)), 1)

    @property
    def available(self) -> bool:
        """Return whether enough live/session metadata is present."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the values used to calculate the percentage."""
        snapshot = self.coordinator.data
        if snapshot is None:
            return {}
        attributes: dict[str, Any] = {
            "mowed_area": snapshot.mowed_area,
            "mowing_time": snapshot.mowing_time,
            # Compatibility aliases for existing dashboards and automations.
            "cleaned_area": snapshot.cleaned_area,
            "cleaning_time": snapshot.cleaning_time,
            "current_zone": _current_zone_label(snapshot),
            "active_segment_count": getattr(snapshot, "active_segment_count", None),
        }
        current_map = _current_app_map_summary(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )
        if isinstance(current_map, dict):
            attributes["current_app_map"] = current_map
        runtime_summary = _runtime_status_blob_summary(
            getattr(self.coordinator, "runtime_status_blob", None)
        )
        if runtime_summary:
            attributes["runtime_status_blob"] = runtime_summary
        return {
            key: value
            for key, value in attributes.items()
            if value not in (None, [], {})
        }


class DreameLawnMowerRuntimeMissionProgressSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose mission progress decoded from the runtime `1.4` payload."""

    _attr_name = "Runtime Mission Progress"
    _attr_icon = "mdi:map-clock-outline"
    _attr_native_unit_of_measurement = "%"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_runtime_mission_progress"

    @property
    def native_value(self) -> float | int | None:
        """Return live or last-known mission progress from runtime telemetry."""
        return _runtime_status_blob_progress_percent(
            _runtime_session_blob(self.coordinator)
        )

    @property
    def available(self) -> bool:
        """Return whether runtime mission telemetry is available and relevant."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the decoded runtime progress details used for the percentage."""
        return _runtime_session_attributes(self.coordinator)


class DreameLawnMowerRuntimeCurrentAreaSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the current mission area decoded from runtime `1.4` telemetry."""

    _attr_name = "Runtime Current Area"
    _attr_icon = "mdi:texture-box"
    _attr_native_unit_of_measurement = "m²"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_runtime_current_area"

    @property
    def native_value(self) -> float | int | None:
        """Return the live or last-known completed mission area."""
        return _runtime_status_blob_current_area_sqm(
            _runtime_session_blob(self.coordinator)
        )

    @property
    def available(self) -> bool:
        """Return whether runtime mission-area telemetry is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the decoded runtime mission details."""
        return _runtime_session_attributes(self.coordinator)


class DreameLawnMowerRuntimeTotalAreaSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the total mission area decoded from runtime `1.4` telemetry."""

    _attr_name = "Runtime Total Area"
    _attr_icon = "mdi:map"
    _attr_native_unit_of_measurement = "m²"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_runtime_total_area"

    @property
    def native_value(self) -> float | int | None:
        """Return the live or last-known total mission area."""
        return _runtime_status_blob_total_area_sqm(
            _runtime_session_blob(self.coordinator)
        )

    @property
    def available(self) -> bool:
        """Return whether runtime total-area telemetry is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the decoded runtime mission details."""
        return _runtime_session_attributes(self.coordinator)


class DreameLawnMowerRuntimePositionXSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the current runtime mower X coordinate."""

    _attr_name = "Runtime Position X"
    _attr_icon = "mdi:axis-x-arrow"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_runtime_position_x"

    @property
    def native_value(self) -> int | None:
        """Return the current runtime X coordinate in map units."""
        snapshot = self.coordinator.data
        if snapshot is None or not _runtime_progress_available_for_snapshot(snapshot):
            return None
        return _runtime_status_blob_pose_x(
            getattr(self.coordinator, "runtime_status_blob", None)
        )

    @property
    def available(self) -> bool:
        """Return whether runtime position telemetry is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the decoded runtime mission details."""
        return _runtime_status_blob_summary(
            getattr(self.coordinator, "runtime_status_blob", None)
        )


class DreameLawnMowerRuntimePositionYSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the current runtime mower Y coordinate."""

    _attr_name = "Runtime Position Y"
    _attr_icon = "mdi:axis-y-arrow"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_runtime_position_y"

    @property
    def native_value(self) -> int | None:
        """Return the current runtime Y coordinate in map units."""
        snapshot = self.coordinator.data
        if snapshot is None or not _runtime_progress_available_for_snapshot(snapshot):
            return None
        return _runtime_status_blob_pose_y(
            getattr(self.coordinator, "runtime_status_blob", None)
        )

    @property
    def available(self) -> bool:
        """Return whether runtime position telemetry is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the decoded runtime mission details."""
        return _runtime_status_blob_summary(
            getattr(self.coordinator, "runtime_status_blob", None)
        )


class DreameLawnMowerRuntimeHeadingSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the current runtime mower heading."""

    _attr_name = "Runtime Heading"
    _attr_icon = "mdi:compass-outline"
    _attr_native_unit_of_measurement = "°"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_runtime_heading"

    @property
    def native_value(self) -> float | int | None:
        """Return the current runtime heading in degrees."""
        snapshot = self.coordinator.data
        if snapshot is None or not _runtime_progress_available_for_snapshot(snapshot):
            return None
        return _runtime_status_blob_heading_deg(
            getattr(self.coordinator, "runtime_status_blob", None)
        )

    @property
    def available(self) -> bool:
        """Return whether runtime heading telemetry is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the decoded runtime mission details."""
        return _runtime_status_blob_summary(
            getattr(self.coordinator, "runtime_status_blob", None)
        )


class DreameLawnMowerRuntimeTrackPointCountSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the current runtime live-track point count."""

    _attr_name = "Runtime Live Track Point Count"
    _attr_icon = "mdi:map-marker-path"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_runtime_live_track_point_count"
        )

    @property
    def native_value(self) -> int | None:
        """Return the current runtime live-track point count."""
        return _current_vector_map_runtime_track_point_count(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )

    @property
    def available(self) -> bool:
        """Return whether current runtime live-track metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached current runtime-track attributes."""
        return current_vector_map_attributes(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )


class DreameLawnMowerRuntimeTrackLengthSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the current runtime live-track length."""

    _attr_name = "Runtime Live Track Length"
    _attr_icon = "mdi:ruler"
    _attr_native_unit_of_measurement = "m"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_runtime_live_track_length"

    @property
    def native_value(self) -> float | int | None:
        """Return the current runtime live-track length in meters."""
        return _current_vector_map_runtime_track_length_m(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )

    @property
    def available(self) -> bool:
        """Return whether current runtime live-track metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached current runtime-track attributes."""
        return current_vector_map_attributes(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )


class DreameLawnMowerRuntimeTrackSegmentCountSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the current runtime live-track segment count."""

    _attr_name = "Runtime Live Track Segment Count"
    _attr_icon = "mdi:vector-polyline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_runtime_live_track_segment_count"
        )

    @property
    def native_value(self) -> int | None:
        """Return the current runtime live-track segment count."""
        return _current_vector_map_runtime_track_segment_count(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )

    @property
    def available(self) -> bool:
        """Return whether current runtime live-track metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached current runtime-track attributes."""
        return current_vector_map_attributes(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )


def _runtime_progress_available_for_snapshot(snapshot: Any) -> bool:
    return runtime_tracking_active(snapshot)


def _runtime_session_blob(coordinator: Any) -> Any:
    """Return live telemetry during a mission and cached telemetry afterward."""
    snapshot = getattr(coordinator, "data", None)
    if snapshot is not None and _runtime_progress_available_for_snapshot(snapshot):
        return getattr(coordinator, "runtime_status_blob", None)
    cache = getattr(coordinator, "runtime_telemetry_cache", None)
    if isinstance(cache, DreameLawnMowerRuntimeTelemetryCache):
        return cache.blob
    return None


def _runtime_session_attributes(coordinator: Any) -> dict[str, Any]:
    """Return runtime metrics with explicit live-versus-cached metadata."""
    snapshot = getattr(coordinator, "data", None)
    live = snapshot is not None and _runtime_progress_available_for_snapshot(snapshot)
    blob = _runtime_session_blob(coordinator)
    attributes = _runtime_status_blob_summary(blob)
    if not attributes:
        return {}
    cache = getattr(coordinator, "runtime_telemetry_cache", None)
    captured_at = (
        cache.captured_at
        if not live and isinstance(cache, DreameLawnMowerRuntimeTelemetryCache)
        else None
    )
    if not live:
        for key in (
            "pose_x",
            "pose_y",
            "heading_deg",
            "track_segment_count",
            "track_point_count",
            "track_length_m",
        ):
            attributes.pop(key, None)
        attributes["cached"] = True
        attributes["captured_at"] = (
            captured_at.isoformat() if captured_at is not None else None
        )
    return {key: value for key, value in attributes.items() if value is not None}


def _runtime_status_blob_summary(blob: Any) -> dict[str, Any]:
    if blob is None:
        return {}
    track_segments = getattr(blob, "candidate_runtime_track_segments", ()) or ()
    track_point_count = sum(
        len(segment) for segment in track_segments if isinstance(segment, (list, tuple))
    )
    track_length_m = (
        round(
            sum(
                _coordinate_path_length_m(segment)
                for segment in track_segments
                if isinstance(segment, (list, tuple))
            ),
            2,
        )
        if track_point_count
        else None
    )
    attributes = {
        "source": getattr(blob, "source", None),
        "length": getattr(blob, "length", None),
        "frame_valid": getattr(blob, "frame_valid", None),
        "progress_percent": getattr(blob, "candidate_runtime_progress_percent", None),
        "area_progress_percent": getattr(
            blob,
            "candidate_runtime_area_progress_percent",
            None,
        ),
        "current_area_sqm": getattr(blob, "candidate_runtime_current_area_sqm", None),
        "total_area_sqm": getattr(blob, "candidate_runtime_total_area_sqm", None),
        "region_id": getattr(blob, "candidate_runtime_region_id", None),
        "task_id": getattr(blob, "candidate_runtime_task_id", None),
        "pose_x": getattr(blob, "candidate_runtime_pose_x", None),
        "pose_y": getattr(blob, "candidate_runtime_pose_y", None),
        "heading_deg": getattr(blob, "candidate_runtime_heading_deg", None),
        "track_segment_count": len(track_segments) if track_point_count else None,
        "track_point_count": track_point_count or None,
        "track_length_m": track_length_m,
        "notes": list(getattr(blob, "notes", ()) or ()),
    }
    return {
        key: value for key, value in attributes.items() if value not in (None, [], {})
    }


def _runtime_status_blob_progress_percent(blob: Any) -> float | int | None:
    area_progress = getattr(blob, "candidate_runtime_area_progress_percent", None)
    if isinstance(area_progress, int | float):
        return area_progress
    progress = getattr(blob, "candidate_runtime_progress_percent", None)
    return progress if isinstance(progress, int | float) else None


def _runtime_status_blob_current_area_sqm(blob: Any) -> float | int | None:
    value = getattr(blob, "candidate_runtime_current_area_sqm", None)
    return value if isinstance(value, int | float) else None


def _runtime_status_blob_total_area_sqm(blob: Any) -> float | int | None:
    value = getattr(blob, "candidate_runtime_total_area_sqm", None)
    return value if isinstance(value, int | float) else None


def _runtime_status_blob_pose_x(blob: Any) -> int | None:
    value = getattr(blob, "candidate_runtime_pose_x", None)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _runtime_status_blob_pose_y(blob: Any) -> int | None:
    value = getattr(blob, "candidate_runtime_pose_y", None)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _runtime_status_blob_heading_deg(blob: Any) -> float | int | None:
    value = getattr(blob, "candidate_runtime_heading_deg", None)
    return value if isinstance(value, int | float) else None
