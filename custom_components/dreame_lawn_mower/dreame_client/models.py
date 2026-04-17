"""Reusable mower domain models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

SUPPORTED_ACCOUNT_TYPES = ("dreame", "mova")
SUPPORTED_MODEL_MARKER = ".mower."

MODEL_NAME_MAP = {
    "dreame.mower.p2255": "A1",
    "dreame.mower.g2422": "A1 Pro",
    "dreame.mower.g2408": "A2",
    "dreame.mower.g3255": "A2 Pro",
}

DISPLAY_NAME_ALIASES = {
    "a1": "A1",
    "a1 pro": "A1 Pro",
    "a2": "A2",
    "a2 pro": "A2 Pro",
    "awd 1000": "AWD 1000",
    "lidax ultra 800": "LiDAX Ultra 800",
    "lidax ultra 1200": "LiDAX Ultra 1200",
    "viax 300": "Viax 300",
    "vivax 250": "Vivax 250",
}


def _canonical_display_name(value: str | None) -> str | None:
    """Normalize a model display name from cloud metadata."""
    text = _as_optional_str(value)
    if text is None:
        return None
    normalized = " ".join(text.replace("_", " ").replace("-", " ").split()).lower()
    return DISPLAY_NAME_ALIASES.get(normalized, text)


def _is_supported_model(model: str | None) -> bool:
    """Return whether a raw cloud model identifier looks like a mower."""
    return bool(model and SUPPORTED_MODEL_MARKER in model)


def display_name_for_model(
    model: str | None,
    *,
    fallback_name: str | None = None,
) -> str | None:
    """Return a friendly model name when one is known."""
    if model is None:
        return _canonical_display_name(fallback_name)
    return MODEL_NAME_MAP.get(model) or _canonical_display_name(fallback_name) or model


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _friendly_error_name(value: str | None) -> str | None:
    """Return a cleaner user-facing error label."""
    text = _as_optional_str(value)
    if text is None or text == "no_error":
        return None
    cleaned = text.replace("_", " ")
    # Upstream payloads currently contain a `wheell` typo on the A2.
    cleaned = cleaned.replace("wheell", "wheel")
    return cleaned.capitalize()


def _is_no_error_text(value: str | None) -> bool:
    """Return whether a text error value explicitly means no active error."""
    text = _as_optional_str(value)
    if text is None:
        return True
    return text.replace("_", " ").casefold() in {"no error", "none"}


@dataclass(slots=True, frozen=True)
class DreameLawnMowerDescriptor:
    """Normalized mower discovery information."""

    did: str
    name: str
    model: str
    display_model: str
    account_type: str
    country: str
    host: str | None = None
    mac: str | None = None
    token: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @property
    def unique_id(self) -> str:
        """Stable unique id for Home Assistant entries."""
        return self.did or self.mac or self.model

    @property
    def title(self) -> str:
        """Friendly display title."""
        if self.display_model and self.display_model != self.model:
            return f"{self.name} ({self.display_model})"
        return self.name


@dataclass(slots=True, frozen=True)
class DreameLawnMowerSnapshot:
    """Normalized live mower state."""

    descriptor: DreameLawnMowerDescriptor
    available: bool
    state: str
    state_name: str
    activity: str
    battery_level: int | None = None
    task_status: str | None = None
    task_status_name: str | None = None
    error_code: int | None = None
    error_name: str | None = None
    error_text: str | None = None
    error_display: str | None = None
    firmware_version: str | None = None
    hardware_version: str | None = None
    serial_number: str | None = None
    cloud_update_time: str | None = None
    unknown_property_count: int = 0
    realtime_property_count: int = 0
    last_realtime_method: str | None = None
    online: bool | None = None
    child_lock: bool | None = None
    charging: bool = False
    started: bool = False
    docked: bool = False
    paused: bool = False
    mowing: bool = False
    returning: bool = False
    scheduled_clean: bool = False
    shortcut_task: bool = False
    mapping_available: bool = False
    cleaning_mode: int | None = None
    cleaning_mode_name: str | None = None
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    raw_attributes: Mapping[str, Any] = field(default_factory=dict, repr=False)
    raw_info: Mapping[str, Any] = field(default_factory=dict, repr=False)


@dataclass(slots=True, frozen=True)
class DreameLawnMowerMapSummary:
    """Normalized read-only summary of mower map data."""

    available: bool
    map_id: int | None = None
    frame_id: int | None = None
    timestamp_ms: int | None = None
    rotation: int | None = None
    width: int | None = None
    height: int | None = None
    grid_size: int | None = None
    saved_map: bool = False
    temporary_map: bool = False
    recovery_map: bool = False
    empty_map: bool = False
    segment_count: int = 0
    active_segment_count: int = 0
    active_area_count: int = 0
    active_point_count: int = 0
    path_point_count: int = 0
    no_go_area_count: int = 0
    virtual_wall_count: int = 0
    pathway_count: int = 0
    obstacle_count: int = 0
    charger_present: bool = False
    robot_present: bool = False


@dataclass(slots=True, frozen=True)
class DreameLawnMowerMapView:
    """Reusable read-only map fetch result."""

    source: str
    summary: DreameLawnMowerMapSummary | None = None
    image_png: bytes | None = field(default=None, repr=False)
    error: str | None = None

    @property
    def available(self) -> bool:
        """Return whether map metadata is available and not empty."""
        return bool(self.summary and self.summary.available)

    @property
    def has_image(self) -> bool:
        """Return whether a rendered image is available."""
        return self.image_png is not None

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe map data payload."""
        return {
            "source": self.source,
            "available": self.available,
            "has_image": self.has_image,
            "error": self.error,
            "summary": map_summary_to_dict(self.summary),
        }


@dataclass(slots=True, frozen=True)
class DreameLawnMowerRemoteControlSupport:
    """Read-only description of the mower remote-control surface."""

    supported: bool
    active: bool = False
    siid: int | None = None
    piid: int | None = None
    state: str | None = None
    status: str | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe support payload."""
        return asdict(self)


@dataclass(slots=True, frozen=True)
class DreameLawnMowerCameraFeatureSupport:
    """Read-only description of the mower camera/photo protocol surface."""

    supported: bool
    advertised: bool
    camera_streaming: bool = False
    camera_light: bool | None = None
    ai_detection: bool = False
    obstacles: bool = False
    permit: str | None = None
    feature: str | None = None
    extend_sc_type: tuple[str, ...] = field(default_factory=tuple)
    video_status: Any | None = None
    video_dynamic_vendor: bool | None = None
    live_key_count: int = 0
    stream_session_present: bool = False
    stream_status: str | None = None
    stream_status_raw: Any | None = None
    property_mappings: Mapping[str, Mapping[str, int]] = field(default_factory=dict)
    action_mappings: Mapping[str, Mapping[str, int]] = field(default_factory=dict)
    cloud_user_features: Mapping[str, Any] | None = None
    cloud_user_features_error: str | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe support payload."""
        return asdict(self)


def map_summary_to_dict(
    summary: DreameLawnMowerMapSummary | None,
) -> dict[str, Any] | None:
    """Return a JSON-safe dictionary for a map summary."""
    if summary is None:
        return None
    return asdict(summary)


def descriptor_from_cloud_record(
    raw: Mapping[str, Any],
    *,
    account_type: str,
    country: str,
) -> DreameLawnMowerDescriptor | None:
    """Convert a raw cloud device record into a normalized descriptor."""

    model = _as_optional_str(raw.get("model"))
    if not _is_supported_model(model):
        return None
    device_info = raw.get("deviceInfo", {}) or {}

    name = (
        _as_optional_str(raw.get("customName"))
        or _as_optional_str(raw.get("name"))
        or _as_optional_str(device_info.get("displayName"))
        or MODEL_NAME_MAP.get(model)
        or model
    )
    display_model = (
        display_name_for_model(
            model,
            fallback_name=_as_optional_str(device_info.get("displayName")),
        )
        or model
    )

    return DreameLawnMowerDescriptor(
        did=str(raw.get("did") or ""),
        name=name,
        model=model,
        display_model=display_model,
        account_type=account_type,
        country=country,
        host=_as_optional_str(raw.get("bindDomain") or raw.get("localip")),
        mac=_as_optional_str(raw.get("mac")),
        token=_as_optional_str(raw.get("token")) or " ",
        raw=raw,
    )


def snapshot_from_device(
    descriptor: DreameLawnMowerDescriptor,
    device: Any,
) -> DreameLawnMowerSnapshot:
    """Convert the upstream mower device object into a normalized snapshot."""

    state_obj = getattr(device.status, "state", None)
    task_obj = getattr(device.status, "task_status", None)
    error_obj = getattr(device.status, "error", None)
    state = state_obj.name.lower() if state_obj is not None else "unknown"
    state_name = getattr(device.status, "state_name", None) or state.replace("_", " ").title()
    status_attributes = getattr(device.status, "attributes", {}) or {}
    info_raw = getattr(getattr(device, "info", None), "raw", {}) or {}
    last_realtime_message = getattr(device, "last_realtime_message", None) or {}
    last_realtime_payload = last_realtime_message.get("message", {})
    last_realtime_method = _as_optional_str(last_realtime_payload.get("method"))
    error_name = _as_optional_str(getattr(device.status, "error_name", None))
    error_text = _as_optional_str(status_attributes.get("error"))
    error_code = getattr(error_obj, "value", None)
    has_error = bool(
        getattr(device.status, "has_error", False)
        or not _is_no_error_text(error_text)
        or (error_code not in (None, -1, 0))
    )
    capability_list = status_attributes.get("capabilities") or getattr(
        getattr(device, "capability", None),
        "list",
        [],
    )
    capabilities = tuple(str(item) for item in capability_list or [])
    cleaning_mode = getattr(device.status, "cleaning_mode", None)

    paused_states = {"paused", "monitoring_paused"}
    returning_states = {"returning"}
    mowing_states = {
        "mowing",
        "remote_control",
        "clean_summon",
        "second_cleaning",
        "human_following",
        "spot_cleaning",
        "shortcut",
        "monitoring",
    }
    docked_states = {
        "idle",
        "charging",
        "charging_completed",
        "building",
        "upgrading",
        "station_reset",
        "smart_charging",
        "waiting_for_task",
    }

    if has_error:
        activity = "error"
    elif state in paused_states:
        activity = "paused"
    elif state in returning_states:
        activity = "returning"
    elif state in mowing_states:
        activity = "mowing"
    elif state in docked_states:
        activity = "docked"
    elif getattr(device.status, "paused", False):
        activity = "paused"
    elif getattr(device.status, "returning", False):
        activity = "returning"
    elif getattr(device.status, "docked", False):
        activity = "docked"
    elif getattr(device.status, "running", False):
        activity = "mowing"
    else:
        activity = "idle"

    child_lock = None
    try:
        from .types import DreameMowerProperty

        child_lock_value = device.get_property(DreameMowerProperty.CHILD_LOCK)
        if child_lock_value is not None:
            child_lock = bool(child_lock_value)
    except Exception:
        child_lock = None

    return DreameLawnMowerSnapshot(
        descriptor=descriptor,
        available=bool(getattr(device, "available", False)),
        state=state,
        state_name=state_name,
        activity=activity,
        battery_level=getattr(device.status, "battery_level", None),
        task_status=task_obj.name.lower() if task_obj is not None else None,
        task_status_name=getattr(device.status, "task_status_name", None),
        error_code=error_code,
        error_name=error_name,
        error_text=error_text,
        error_display=_friendly_error_name(error_name) or error_text,
        firmware_version=getattr(getattr(device, "info", None), "firmware_version", None),
        hardware_version=getattr(getattr(device, "info", None), "hardware_version", None),
        serial_number=_as_optional_str(info_raw.get("sn")),
        cloud_update_time=_as_optional_str(info_raw.get("updateTime")),
        unknown_property_count=len(getattr(device, "unknown_properties", {}) or {}),
        realtime_property_count=len(getattr(device, "realtime_properties", {}) or {}),
        last_realtime_method=last_realtime_method,
        online=info_raw.get("online"),
        child_lock=child_lock,
        charging=bool(status_attributes.get("charging", getattr(device.status, "charging", False))),
        started=bool(status_attributes.get("started", getattr(device.status, "started", False))),
        docked=bool(getattr(device.status, "docked", False)),
        paused=bool(getattr(device.status, "paused", False)),
        mowing=bool(getattr(device.status, "running", False)),
        returning=bool(getattr(device.status, "returning", False)),
        scheduled_clean=bool(getattr(device.status, "scheduled_clean", False)),
        shortcut_task=bool(getattr(device.status, "shortcut_task", False)),
        mapping_available=bool(
            status_attributes.get(
                "mapping_available",
                getattr(device.status, "mapping_available", False),
            )
        ),
        cleaning_mode=getattr(cleaning_mode, "value", cleaning_mode),
        cleaning_mode_name=getattr(device.status, "cleaning_mode_name", None),
        capabilities=capabilities,
        raw_attributes=status_attributes,
        raw_info=info_raw,
    )


def map_summary_from_map_data(map_data: Any) -> DreameLawnMowerMapSummary | None:
    """Convert raw mower map data into a small reusable summary."""
    if map_data is None:
        return None

    dimensions = getattr(map_data, "dimensions", None)
    segments = getattr(map_data, "segments", None) or {}
    active_segments = getattr(map_data, "active_segments", None) or []
    active_areas = getattr(map_data, "active_areas", None) or []
    active_points = getattr(map_data, "active_points", None) or []
    path = getattr(map_data, "path", None) or []
    no_go_areas = getattr(map_data, "no_go_areas", None) or []
    virtual_walls = getattr(map_data, "virtual_walls", None) or []
    pathways = getattr(map_data, "pathways", None) or []
    obstacles = getattr(map_data, "obstacles", None) or {}

    return DreameLawnMowerMapSummary(
        available=not bool(getattr(map_data, "empty_map", False)),
        map_id=getattr(map_data, "map_id", None),
        frame_id=getattr(map_data, "frame_id", None),
        timestamp_ms=getattr(map_data, "timestamp_ms", None),
        rotation=getattr(map_data, "rotation", None),
        width=getattr(dimensions, "width", None),
        height=getattr(dimensions, "height", None),
        grid_size=getattr(dimensions, "grid_size", None),
        saved_map=bool(getattr(map_data, "saved_map", False)),
        temporary_map=bool(getattr(map_data, "temporary_map", False)),
        recovery_map=bool(getattr(map_data, "recovery_map", False)),
        empty_map=bool(getattr(map_data, "empty_map", False)),
        segment_count=len(segments),
        active_segment_count=len(active_segments),
        active_area_count=len(active_areas),
        active_point_count=len(active_points),
        path_point_count=len(path),
        no_go_area_count=len(no_go_areas),
        virtual_wall_count=len(virtual_walls),
        pathway_count=len(pathways),
        obstacle_count=len(obstacles),
        charger_present=getattr(map_data, "charger_position", None) is not None,
        robot_present=getattr(map_data, "robot_position", None) is not None,
    )
