"""Reusable mower domain models."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

SUPPORTED_ACCOUNT_TYPES = ("dreame", "mova")
SUPPORTED_MODEL_MARKER = ".mower."
MIN_REMOTE_CONTROL_BATTERY_LEVEL = 20
REMOTE_CONTROL_STATES = {"remote_control"}
REALTIME_ERROR_PROPERTY_KEY = "2.2"

MODEL_NAME_MAP = {
    "dreame.mower.p2255": "A1",
    "dreame.mower.g2422": "A1 Pro",
    "dreame.mower.g2408": "A2",
}

DISPLAY_NAME_ALIASES = {
    "a1": "A1",
    "a1 pro": "A1 Pro",
    "a2": "A2",
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


def _error_name_from_code(value: int | None) -> str | None:
    """Return the bundled vendor error name for a numeric code, if known."""
    if value in (None, -1, 0):
        return None
    try:
        from .const import ERROR_CODE_TO_ERROR_NAME
        from .types import DreameMowerErrorCode

        return ERROR_CODE_TO_ERROR_NAME.get(DreameMowerErrorCode(value))
    except (ImportError, ValueError):
        return None


def _error_code_from_raw(value: Any) -> int | None:
    """Return a numeric error code from raw app/status values."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _active_error_code_from_raw(value: Any) -> int | None:
    """Return a numeric active error code from raw app/status values."""
    code = _error_code_from_raw(value)
    return None if code in (-1, 0) else code


def _realtime_error_code_from_device(device: Any) -> int | None:
    """Return the app realtime `2.2` error code when it is active."""
    realtime_properties = getattr(device, "realtime_properties", {}) or {}
    entry = realtime_properties.get(REALTIME_ERROR_PROPERTY_KEY)
    value = entry.get("value") if isinstance(entry, Mapping) else entry
    return _active_error_code_from_raw(value)


def _friendly_error_display(
    *,
    error_code: int | None,
    error_name: str | None,
    error_text: str | None,
) -> str | None:
    """Return the best user-facing error while preserving raw text elsewhere."""
    if error_code not in (None, -1, 0):
        return (
            _friendly_error_name(error_name)
            or _friendly_error_name(_error_name_from_code(error_code))
            or (None if _is_no_error_text(error_text) else error_text)
            or f"Error {error_code}"
        )

    return _friendly_error_name(error_name) or error_text


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
    error_source: str | None = None
    raw_error_code: int | None = None
    realtime_error_code: int | None = None
    firmware_version: str | None = None
    hardware_version: str | None = None
    serial_number: str | None = None
    cloud_update_time: str | None = None
    unknown_property_count: int = 0
    realtime_property_count: int = 0
    last_realtime_method: str | None = None
    online: bool | None = None
    device_connected: bool | None = None
    cloud_connected: bool | None = None
    child_lock: bool | None = None
    charging: bool = False
    raw_charging: bool | None = None
    started: bool = False
    raw_started: bool | None = None
    docked: bool = False
    raw_docked: bool | None = None
    paused: bool = False
    mowing: bool = False
    returning: bool = False
    raw_returning: bool | None = None
    scheduled_clean: bool = False
    shortcut_task: bool = False
    mapping_available: bool = False
    cleaning_mode: int | None = None
    cleaning_mode_name: str | None = None
    cleaned_area: int | float | None = None
    cleaning_time: int | None = None
    active_segment_count: int | None = None
    current_zone_id: int | None = None
    current_zone_name: str | None = None
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    raw_attributes: Mapping[str, Any] = field(default_factory=dict, repr=False)
    raw_info: Mapping[str, Any] = field(default_factory=dict, repr=False)


@dataclass(slots=True, frozen=True)
class DreameLawnMowerStatusBlob:
    """Structured, conservative view of the app realtime `1.1` status blob."""

    supported: bool
    source: str | None = None
    raw: tuple[int, ...] = field(default_factory=tuple)
    length: int = 0
    hex: str | None = None
    frame_start: int | None = None
    frame_end: int | None = None
    frame_valid: bool = False
    payload: tuple[int, ...] = field(default_factory=tuple)
    bytes_by_index: Mapping[str, int] = field(default_factory=dict)
    candidate_battery_level: int | None = None
    candidate_runtime_region_id: int | None = None
    candidate_runtime_task_id: int | None = None
    candidate_runtime_progress_percent: float | None = None
    candidate_runtime_area_progress_percent: float | None = None
    candidate_runtime_current_area_sqm: float | None = None
    candidate_runtime_total_area_sqm: float | None = None
    candidate_runtime_pose_x: int | None = None
    candidate_runtime_pose_y: int | None = None
    candidate_runtime_heading_deg: float | None = None
    candidate_runtime_track_segments: tuple[
        tuple[tuple[int, int], ...],
        ...,
    ] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe status blob payload."""
        return {
            key: value for key, value in asdict(self).items() if value is not None
        }


@dataclass(slots=True, frozen=True)
class DreameLawnMowerFirmwareUpdateSupport:
    """Read-only firmware/update evidence from device and cloud metadata."""

    current_version: str | None = None
    latest_version: str | None = None
    hardware_version: str | None = None
    cloud_update_time: str | None = None
    release_summary: str | None = None
    release_summary_available: bool | None = None
    latest_status: int | str | None = None
    plugin_force_update: bool | None = None
    plugin_force_update_sources: Mapping[str, bool] = field(default_factory=dict)
    plugin_status: str | None = None
    firmware_develop_type: str | None = None
    device_info_release_at: str | None = None
    device_info_updated_at: str | None = None
    cloud_check_available: bool | None = None
    cloud_check_update_available: bool | None = None
    batch_ota_available: bool | None = None
    auto_upgrade_enabled: bool | None = None
    ota_status: int | str | None = None
    debug_catalog_available: bool | None = None
    debug_catalog_current_version_present: bool | None = None
    debug_catalog_changelog_available: bool | None = None
    debug_catalog_latest_release_candidates: Sequence[Mapping[str, Any]] = field(
        default_factory=tuple
    )
    update_state: str | None = None
    update_available: bool | None = None
    cloud_error: str | None = None
    candidate_update_fields: Mapping[str, Any] = field(default_factory=dict)
    evidence: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe firmware/update payload."""
        return asdict(self)


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
    spot_area_count: int = 0
    virtual_wall_count: int = 0
    pathway_count: int = 0
    obstacle_count: int = 0
    charger_present: bool = False
    robot_present: bool = False


@dataclass(slots=True, frozen=True)
class DreameLawnMowerMapDiagnostics:
    """Structured details explaining a map fetch result."""

    source: str
    reason: str | None = None
    state: str | None = None
    state_name: str | None = None
    capability_map: bool | None = None
    capability_lidar_navigation: bool | None = None
    map_manager_present: bool = False
    map_manager_ready: bool | None = None
    map_request_count: int | None = None
    map_request_needed: bool | None = None
    current_map_present: bool = False
    selected_map_present: bool = False
    map_list_count: int | None = None
    saved_map_count: int | None = None
    has_saved_map: bool | None = None
    has_temporary_map: bool | None = None
    has_new_map: bool | None = None
    mapping_available: bool | None = None
    raw_status_flags: Mapping[str, Any] = field(default_factory=dict)
    cloud_property_summary: Mapping[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe diagnostics payload."""
        return asdict(self)


@dataclass(slots=True, frozen=True)
class DreameLawnMowerMapView:
    """Reusable read-only map fetch result."""

    source: str
    summary: DreameLawnMowerMapSummary | None = None
    image_png: bytes | None = field(default=None, repr=False)
    error: str | None = None
    diagnostics: DreameLawnMowerMapDiagnostics | None = None
    app_maps: Mapping[str, Any] | None = None
    details: Mapping[str, Any] | None = None

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
            "diagnostics": (
                self.diagnostics.as_dict() if self.diagnostics is not None else None
            ),
            "app_maps": dict(self.app_maps or {}),
            "details": dict(self.details or {}),
        }


@dataclass(slots=True, frozen=True)
class DreameLawnMowerRemoteControlSupport:
    """Read-only description of the mower remote-control surface."""

    supported: bool
    active: bool = False
    state_safe: bool | None = None
    state_block_reason: str | None = None
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


def remote_control_block_reason(snapshot: Any) -> str | None:
    """Return why manual remote control is blocked for the snapshot state."""
    if snapshot is None:
        return "Mower state is not available yet."

    raw_attributes = getattr(snapshot, "raw_attributes", None) or {}
    state = str(getattr(snapshot, "state", None) or "").casefold()
    activity = str(getattr(snapshot, "activity", None) or "").casefold()
    remote_control_active = state in REMOTE_CONTROL_STATES
    raw_running = bool(raw_attributes.get("running"))
    mapping = bool(raw_attributes.get("mapping"))
    battery_level = getattr(snapshot, "battery_level", None)

    if mapping:
        return "Remote control is blocked while mapping."
    if bool(raw_attributes.get("fast_mapping")):
        return "Remote control is blocked while fast mapping."
    if (
        isinstance(battery_level, int | float)
        and battery_level < MIN_REMOTE_CONTROL_BATTERY_LEVEL
    ):
        return "Remote control is blocked while battery is low."
    if activity == "error":
        return "Remote control is blocked while error is active."
    if getattr(snapshot, "returning", False) and not remote_control_active:
        return "Remote control is blocked while returning to dock."

    mower_active = (
        bool(getattr(snapshot, "mowing", False))
        or activity == "mowing"
        or raw_running
    )
    if mower_active and not remote_control_active:
        return "Remote control is blocked while the mower is active."

    return None


def remote_control_state_safe(snapshot: Any) -> bool:
    """Return whether the snapshot state allows a manual-drive step."""
    return remote_control_block_reason(snapshot) is None


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
    state_name = getattr(device.status, "state_name", None) or state.replace(
        "_",
        " ",
    ).title()
    status_attributes = getattr(device.status, "attributes", {}) or {}
    info_raw = getattr(getattr(device, "info", None), "raw", {}) or {}
    last_realtime_message = getattr(device, "last_realtime_message", None) or {}
    last_realtime_payload = last_realtime_message.get("message", {})
    last_realtime_method = _as_optional_str(last_realtime_payload.get("method"))
    error_name = _as_optional_str(getattr(device.status, "error_name", None))
    error_text = _as_optional_str(status_attributes.get("error"))
    raw_error_code = _error_code_from_raw(getattr(error_obj, "value", None))
    realtime_error_code = _realtime_error_code_from_device(device)
    error_code = raw_error_code
    status_has_error = bool(getattr(device.status, "has_error", False))
    error_code_active = _active_error_code_from_raw(error_code) is not None
    error_name_active = not _is_no_error_text(error_name)
    error_text_active = not _is_no_error_text(error_text)
    has_only_bare_error_flag = (
        status_has_error
        and error_code is None
        and error_name is None
        and error_text is None
    )
    has_error = bool(
        error_code_active
        or error_name_active
        or error_text_active
        or has_only_bare_error_flag
    )
    error_source: str | None = "status" if has_error else None
    if not has_error and realtime_error_code is not None:
        error_code = realtime_error_code
        has_error = True
        error_source = f"realtime_property_{REALTIME_ERROR_PROPERTY_KEY}"
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
    charging_states = {
        "charging",
        "smart_charging",
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
    raw_docked = bool(getattr(device.status, "docked", False))
    effective_docked = bool(
        raw_docked or state in docked_states or activity == "docked"
    )
    raw_charging_source = status_attributes.get(
        "charging",
        getattr(device.status, "charging", None),
    )
    raw_charging = (
        None if raw_charging_source is None else bool(raw_charging_source)
    )
    effective_charging = bool(raw_charging or state in charging_states)
    raw_started = bool(
        status_attributes.get("started", getattr(device.status, "started", False))
    )
    effective_started = bool(raw_started and activity not in {"docked", "idle"})
    raw_returning = bool(getattr(device.status, "returning", False))
    effective_returning = bool(activity == "returning")
    effective_mowing = bool(activity == "mowing")

    child_lock = None
    try:
        from .types import DreameMowerProperty

        child_lock_value = device.get_property(DreameMowerProperty.CHILD_LOCK)
        if child_lock_value is not None:
            child_lock = bool(child_lock_value)
    except Exception:
        child_lock = None

    current_map = getattr(device.status, "current_map", None)
    current_zone = getattr(device.status, "current_zone", None)
    cleaned_area = _first_number(
        status_attributes.get("cleaned_area"),
        getattr(device.status, "cleaned_area", None),
        getattr(current_map, "cleaned_area", None),
    )
    cleaning_time = _first_int(
        status_attributes.get("cleaning_time"),
        getattr(device.status, "cleaning_time", None),
        getattr(current_map, "cleaning_time", None),
    )
    active_segments = _coerce_sequence(
        status_attributes.get("active_segments"),
        getattr(device.status, "active_segments", None),
        getattr(current_map, "active_segments", None),
    )
    active_segment_count = len(active_segments) if active_segments is not None else None
    current_zone_id = _first_int(
        status_attributes.get("current_segment"),
        getattr(current_zone, "segment_id", None),
        getattr(current_map, "robot_segment", None),
    )
    current_zone_name = _as_optional_str(getattr(current_zone, "name", None))

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
        error_display=_friendly_error_display(
            error_code=error_code,
            error_name=error_name,
            error_text=error_text,
        ),
        error_source=error_source,
        raw_error_code=raw_error_code,
        realtime_error_code=realtime_error_code,
        firmware_version=getattr(
            getattr(device, "info", None),
            "firmware_version",
            None,
        ),
        hardware_version=getattr(
            getattr(device, "info", None),
            "hardware_version",
            None,
        ),
        serial_number=_as_optional_str(info_raw.get("sn")),
        cloud_update_time=_as_optional_str(info_raw.get("updateTime")),
        unknown_property_count=len(getattr(device, "unknown_properties", {}) or {}),
        realtime_property_count=len(getattr(device, "realtime_properties", {}) or {}),
        last_realtime_method=last_realtime_method,
        online=info_raw.get("online"),
        device_connected=bool(getattr(device, "device_connected", False))
        if hasattr(device, "device_connected")
        else None,
        cloud_connected=bool(getattr(device, "cloud_connected", False))
        if hasattr(device, "cloud_connected")
        else None,
        child_lock=child_lock,
        charging=effective_charging,
        raw_charging=raw_charging,
        started=effective_started,
        raw_started=raw_started,
        docked=effective_docked,
        raw_docked=raw_docked,
        paused=bool(getattr(device.status, "paused", False)),
        mowing=effective_mowing,
        returning=effective_returning,
        raw_returning=raw_returning,
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
        cleaned_area=cleaned_area,
        cleaning_time=cleaning_time,
        active_segment_count=active_segment_count,
        current_zone_id=current_zone_id,
        current_zone_name=current_zone_name,
        capabilities=capabilities,
        raw_attributes=status_attributes,
        raw_info=info_raw,
    )


def firmware_update_support_from_device(
    device: Any,
    *,
    cloud_device_info: Mapping[str, Any] | None = None,
    cloud_device_list_page: Mapping[str, Any] | None = None,
    cloud_firmware_check: Mapping[str, Any] | None = None,
    batch_ota_info: Mapping[str, Any] | None = None,
    debug_ota_catalog: Mapping[str, Any] | None = None,
    cloud_error: str | None = None,
) -> DreameLawnMowerFirmwareUpdateSupport:
    """Build firmware/update evidence without guessing OTA availability."""

    info = getattr(device, "info", None)
    info_raw = getattr(info, "raw", {}) or {}
    device_info = info_raw.get("deviceInfo", {}) or {}
    status = getattr(device, "status", None)
    state = getattr(status, "state", None)
    state_name = _as_optional_str(getattr(state, "name", None))
    update_state = state_name.lower() if state_name else None
    if update_state not in {"upgrading", "updating"}:
        update_state = None

    plugin_force_update = _optional_bool_from_raw(device_info.get("pluginForceUpdate"))
    evidence: dict[str, Any] = {
        "info": {
            "ver": _as_optional_str(info_raw.get("ver"))
            or _as_optional_str(getattr(info, "firmware_version", None)),
            "updateTime": _as_optional_str(info_raw.get("updateTime")),
            "latestStatus": info_raw.get("latestStatus"),
            "status": _as_optional_str(info_raw.get("status")),
            "featureCode": info_raw.get("featureCode"),
            "featureCode2": info_raw.get("featureCode2"),
        },
        "deviceInfo": {
            "pluginForceUpdate": plugin_force_update,
            "firmwareDevelopType": _as_optional_str(
                device_info.get("firmwareDevelopType")
            ),
            "releaseAt": _as_optional_str(device_info.get("releaseAt")),
            "updatedAt": _as_optional_str(device_info.get("updatedAt")),
            "status": _as_optional_str(device_info.get("status")),
        },
    }
    if cloud_device_info is not None:
        evidence["cloud_device_info"] = _compact_mapping_evidence(cloud_device_info)
    if cloud_device_list_page is not None:
        evidence["cloud_device_list_page"] = _compact_mapping_evidence(
            cloud_device_list_page
        )
    if cloud_firmware_check is not None:
        evidence["cloud_firmware_check"] = {
            key: _compact_update_candidate_value(cloud_firmware_check.get(key))
            for key in (
                "source",
                "available",
                "update_available",
                "current_version",
                "latest_version",
                "firmware_type",
                "force_update",
                "status",
                "changelog",
                "changelog_available",
                "changelog_error",
                "errors",
            )
            if key in cloud_firmware_check
        }
    if batch_ota_info is not None:
        evidence["batch_ota_info"] = {
            key: batch_ota_info.get(key)
            for key in (
                "source",
                "available",
                "update_available",
                "auto_upgrade_enabled",
                "ota_status",
            )
            if key in batch_ota_info
        }
    if debug_ota_catalog is not None:
        evidence["debug_ota_catalog"] = {
            key: _compact_update_candidate_value(debug_ota_catalog.get(key))
            for key in (
                "source",
                "available",
                "model_name",
                "current_version",
                "current_version_present",
                "changelog_available",
                "latest_release_candidates",
                "warnings",
                "errors",
            )
            if key in debug_ota_catalog
        }

    candidate_update_fields = _collect_update_candidate_fields(
        {
            "info": info_raw,
            "deviceInfo": device_info,
            "cloud_device_info": cloud_device_info,
            "cloud_device_list_page": cloud_device_list_page,
            "cloud_firmware_check": cloud_firmware_check,
            "batch_ota_info": batch_ota_info,
            "debug_ota_catalog": debug_ota_catalog,
        }
    )

    warnings: list[str] = []
    plugin_force_update_sources = _collect_plugin_force_update_sources(
        cached_device_info=device_info,
        cloud_device_info=cloud_device_info,
        cloud_device_list_page=cloud_device_list_page,
    )
    if plugin_force_update_sources:
        evidence["pluginForceUpdateSources"] = plugin_force_update_sources
        unique_plugin_values = {
            item
            for item in plugin_force_update_sources.values()
            if isinstance(item, bool)
        }
        if len(unique_plugin_values) > 1:
            warnings.append("plugin_force_update_conflict")

    batch_ota_available = None
    auto_upgrade_enabled = None
    ota_status = None
    latest_version = None
    release_summary = None
    release_summary_available = None
    cloud_check_available = None
    cloud_check_update_available = None
    debug_catalog_available = None
    debug_catalog_current_version_present = None
    debug_catalog_changelog_available = None
    debug_catalog_latest_release_candidates: tuple[Mapping[str, Any], ...] = ()
    update_available = None
    if isinstance(cloud_firmware_check, Mapping):
        available = cloud_firmware_check.get("available")
        if isinstance(available, bool):
            cloud_check_available = available

        check_update_available = cloud_firmware_check.get("update_available")
        if isinstance(check_update_available, bool):
            cloud_check_update_available = check_update_available
            update_available = check_update_available

        latest_version = _as_optional_str(cloud_firmware_check.get("latest_version"))
        release_summary = _as_optional_str(cloud_firmware_check.get("changelog"))

        changelog_available = cloud_firmware_check.get("changelog_available")
        if isinstance(changelog_available, bool):
            release_summary_available = changelog_available

    if isinstance(batch_ota_info, Mapping):
        available = batch_ota_info.get("available")
        if isinstance(available, bool):
            batch_ota_available = available

        auto_upgrade = batch_ota_info.get("auto_upgrade_enabled")
        if isinstance(auto_upgrade, bool):
            auto_upgrade_enabled = auto_upgrade

        ota_status = batch_ota_info.get("ota_status")
        batch_update_available = batch_ota_info.get("update_available")
        if isinstance(batch_update_available, bool) and update_available is None:
            update_available = batch_update_available
    if isinstance(debug_ota_catalog, Mapping):
        available = debug_ota_catalog.get("available")
        if isinstance(available, bool):
            debug_catalog_available = available

        current_version_present = debug_ota_catalog.get("current_version_present")
        if isinstance(current_version_present, bool):
            debug_catalog_current_version_present = current_version_present

        changelog_available = debug_ota_catalog.get("changelog_available")
        if isinstance(changelog_available, bool):
            debug_catalog_changelog_available = changelog_available

        latest_candidates = debug_ota_catalog.get("latest_release_candidates")
        if isinstance(latest_candidates, Sequence) and not isinstance(
            latest_candidates, str | bytes | bytearray
        ):
            debug_catalog_latest_release_candidates = tuple(
                item for item in latest_candidates if isinstance(item, Mapping)
            )

    reason = "No verified mower firmware update availability signal was found."
    if cloud_check_update_available is True and batch_ota_info is not None:
        reason = (
            "Cloud checkDeviceVersion and batch OTA info both report that a mower "
            "firmware update is available."
        )
    elif cloud_check_update_available is True:
        reason = (
            "Cloud checkDeviceVersion reports that a mower firmware update is "
            "available."
        )
    elif cloud_check_update_available is False and batch_ota_info is None:
        reason = (
            "Cloud checkDeviceVersion reports that no mower firmware update is "
            "available."
        )
    elif update_available is True:
        reason = "Batch OTA info reports that a mower firmware update is available."
    elif update_available is False:
        reason = "Batch OTA info reports that no mower firmware update is available."
    elif "plugin_force_update_conflict" in warnings:
        reason = (
            "pluginForceUpdate differs across cloud metadata sources, so it is "
            "not treated as verified mower firmware update availability."
        )
    elif plugin_force_update:
        reason = (
            "Cloud metadata advertises pluginForceUpdate, which appears to be "
            "mobile-app/plugin metadata, not a verified mower firmware update."
        )
    if update_state is not None:
        reason = "Mower reports an update-related state."

    return DreameLawnMowerFirmwareUpdateSupport(
        current_version=_as_optional_str(info_raw.get("ver"))
        or _as_optional_str(getattr(info, "firmware_version", None)),
        latest_version=latest_version,
        hardware_version=_as_optional_str(getattr(info, "hardware_version", None)),
        cloud_update_time=_as_optional_str(info_raw.get("updateTime")),
        release_summary=release_summary,
        release_summary_available=release_summary_available,
        latest_status=info_raw.get("latestStatus"),
        plugin_force_update=plugin_force_update,
        plugin_force_update_sources=plugin_force_update_sources,
        plugin_status=_as_optional_str(device_info.get("status")),
        firmware_develop_type=_as_optional_str(
            device_info.get("firmwareDevelopType")
        ),
        device_info_release_at=_as_optional_str(device_info.get("releaseAt")),
        device_info_updated_at=_as_optional_str(device_info.get("updatedAt")),
        cloud_check_available=cloud_check_available,
        cloud_check_update_available=cloud_check_update_available,
        batch_ota_available=batch_ota_available,
        auto_upgrade_enabled=auto_upgrade_enabled,
        ota_status=ota_status,
        debug_catalog_available=debug_catalog_available,
        debug_catalog_current_version_present=debug_catalog_current_version_present,
        debug_catalog_changelog_available=debug_catalog_changelog_available,
        debug_catalog_latest_release_candidates=debug_catalog_latest_release_candidates,
        update_state=update_state,
        update_available=update_available,
        cloud_error=cloud_error,
        candidate_update_fields=candidate_update_fields,
        evidence=evidence,
        warnings=tuple(warnings),
        reason=reason,
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
    spot_areas = getattr(map_data, "spot_areas", None) or []
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
        spot_area_count=len(spot_areas),
        virtual_wall_count=len(virtual_walls),
        pathway_count=len(pathways),
        obstacle_count=len(obstacles),
        charger_present=getattr(map_data, "charger_position", None) is not None,
        robot_present=getattr(map_data, "robot_position", None) is not None,
    )


def map_diagnostics_from_device(
    device: Any,
    *,
    source: str,
    reason: str | None = None,
    cloud_property_summary: Mapping[str, Any] | None = None,
) -> DreameLawnMowerMapDiagnostics:
    """Return map diagnostics from the current device and map-manager state."""

    status = getattr(device, "status", None)
    capability = getattr(device, "capability", None)
    map_manager = getattr(device, "_map_manager", None)
    current_map = getattr(device, "current_map", None)
    selected_map = getattr(device, "selected_map", None)
    map_list = _safe_len(getattr(device, "map_list", None))
    map_data_list = getattr(device, "map_data_list", None)

    return DreameLawnMowerMapDiagnostics(
        source=source,
        reason=reason,
        state=_lower_optional_name(getattr(status, "state", None)),
        state_name=_as_optional_str(getattr(status, "state_name", None)),
        capability_map=_optional_bool_from_raw(getattr(capability, "map", None)),
        capability_lidar_navigation=_optional_bool_from_raw(
            getattr(capability, "lidar_navigation", None)
        ),
        map_manager_present=map_manager is not None,
        map_manager_ready=_optional_bool_from_raw(getattr(map_manager, "ready", None)),
        map_request_count=getattr(map_manager, "_map_request_count", None),
        map_request_needed=getattr(map_manager, "_need_map_request", None),
        current_map_present=current_map is not None,
        selected_map_present=selected_map is not None,
        map_list_count=map_list,
        saved_map_count=_safe_len(map_data_list),
        has_saved_map=_optional_bool_from_raw(getattr(status, "has_saved_map", None)),
        has_temporary_map=_optional_bool_from_raw(
            getattr(status, "has_temporary_map", None)
        ),
        has_new_map=_optional_bool_from_raw(getattr(status, "has_new_map", None)),
        mapping_available=_optional_bool_from_raw(
            getattr(status, "mapping_available", None)
        ),
        raw_status_flags={
            key: value
            for key, value in {
                "running": getattr(status, "running", None),
                "returning": getattr(status, "returning", None),
                "docked": getattr(status, "docked", None),
                "started": getattr(status, "started", None),
            }.items()
            if value is not None
        },
        cloud_property_summary=cloud_property_summary,
    )


def _optional_bool_from_raw(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _optional_int_from_raw(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_number_from_raw(value: Any) -> int | float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def _first_int(*values: Any) -> int | None:
    for value in values:
        parsed = _optional_int_from_raw(value)
        if parsed is not None:
            return parsed
    return None


def _first_number(*values: Any) -> int | float | None:
    for value in values:
        parsed = _optional_number_from_raw(value)
        if parsed is not None:
            return parsed
    return None


def _coerce_sequence(*values: Any) -> list[Any] | None:
    for value in values:
        if isinstance(value, list):
            return value
        if isinstance(value, tuple):
            return list(value)
    return None


def _lower_optional_name(value: Any) -> str | None:
    name = getattr(value, "name", None)
    if name:
        return str(name).lower()
    return _as_optional_str(value)


def _safe_len(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return len(value)
    except TypeError:
        return None


def _compact_mapping_evidence(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return useful keys without storing full noisy cloud payloads in support."""
    page = value.get("page") if isinstance(value, Mapping) else None
    records = page.get("records") if isinstance(page, Mapping) else None
    if not isinstance(records, list) and isinstance(value.get("records"), list):
        records = value.get("records")
    if isinstance(records, list):
        key_name = "page" if isinstance(page, Mapping) else "root"
        summary: dict[str, Any] = {
            key_name: {"record_count": len(records)},
            "records": [
                _compact_mapping_evidence(item)
                for item in records[:5]
                if isinstance(item, Mapping)
            ],
        }
        for key in ("current", "size", "total"):
            if isinstance(value, Mapping) and key in value:
                summary[key] = value.get(key)
        return summary

    interesting_keys = (
        "status",
        "latestStatus",
        "ver",
        "updateTime",
        "featureCode",
        "featureCode2",
        "pluginForceUpdate",
    )
    result = {
        key: value.get(key)
        for key in interesting_keys
        if isinstance(value, Mapping) and key in value
    }
    device_info = value.get("deviceInfo") if isinstance(value, Mapping) else None
    if isinstance(device_info, Mapping):
        result["deviceInfo"] = {
            key: device_info.get(key)
            for key in (
                "status",
                "pluginForceUpdate",
                "firmwareDevelopType",
                "releaseAt",
                "updatedAt",
            )
            if key in device_info
        }
    return result


def _collect_plugin_force_update_sources(
    *,
    cached_device_info: Mapping[str, Any],
    cloud_device_info: Mapping[str, Any] | None,
    cloud_device_list_page: Mapping[str, Any] | None,
) -> dict[str, bool]:
    sources: dict[str, bool] = {}
    if "pluginForceUpdate" in cached_device_info:
        sources["cached_device_info"] = bool(
            cached_device_info.get("pluginForceUpdate")
        )

    if isinstance(cloud_device_info, Mapping):
        device_info = cloud_device_info.get("deviceInfo")
        if isinstance(device_info, Mapping) and "pluginForceUpdate" in device_info:
            sources["cloud_device_info"] = bool(device_info.get("pluginForceUpdate"))

    if isinstance(cloud_device_list_page, Mapping):
        records = cloud_device_list_page.get("records")
        page = cloud_device_list_page.get("page")
        if not isinstance(records, list) and isinstance(page, Mapping):
            records = page.get("records")
        if isinstance(records, list):
            for index, record in enumerate(records[:5]):
                if not isinstance(record, Mapping):
                    continue
                device_info = record.get("deviceInfo")
                if (
                    isinstance(device_info, Mapping)
                    and "pluginForceUpdate" in device_info
                ):
                    sources[f"cloud_device_list_page.records[{index}]"] = bool(
                        device_info.get("pluginForceUpdate")
                    )
    return sources


_UPDATE_CANDIDATE_KEY_TOKENS = (
    "firmware",
    "update",
    "upgrade",
    "version",
)
_UPDATE_CANDIDATE_EXACT_KEYS = {
    "lateststatus",
    "ota",
    "otastatus",
    "pluginforceupdate",
    "releaseat",
    "ver",
}
_UPDATE_CANDIDATE_FIELD_LIMIT = 50
_UPDATE_CANDIDATE_LIST_LIMIT = 5


def _collect_update_candidate_fields(
    sources: Mapping[str, Any],
) -> dict[str, Any]:
    """Return compact update-looking fields for cross-model diagnostics."""

    result: dict[str, Any] = {}
    for source, value in sources.items():
        if value is None:
            continue
        _walk_update_candidate_fields(value, str(source), result)
        if len(result) >= _UPDATE_CANDIDATE_FIELD_LIMIT:
            break
    return result


def _walk_update_candidate_fields(
    value: Any,
    path: str,
    result: dict[str, Any],
) -> None:
    if len(result) >= _UPDATE_CANDIDATE_FIELD_LIMIT:
        return

    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}" if path else key_text
            if _is_update_candidate_key(key_text):
                compact_value = _compact_update_candidate_value(item)
                if compact_value is not None:
                    result[child_path] = compact_value
                    if len(result) >= _UPDATE_CANDIDATE_FIELD_LIMIT:
                        return
            _walk_update_candidate_fields(item, child_path, result)
            if len(result) >= _UPDATE_CANDIDATE_FIELD_LIMIT:
                return
        return

    if isinstance(value, list | tuple):
        for index, item in enumerate(value[:_UPDATE_CANDIDATE_LIST_LIMIT]):
            _walk_update_candidate_fields(item, f"{path}[{index}]", result)
            if len(result) >= _UPDATE_CANDIDATE_FIELD_LIMIT:
                return


def _is_update_candidate_key(key: str) -> bool:
    normalized = key.replace("_", "").replace("-", "").casefold()
    return normalized in _UPDATE_CANDIDATE_EXACT_KEYS or any(
        token in normalized for token in _UPDATE_CANDIDATE_KEY_TOKENS
    )


def _compact_update_candidate_value(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        return text if len(text) <= 160 else f"{text[:157]}..."
    if isinstance(value, Mapping):
        return {"type": "object", "keys": [str(key) for key in list(value)[:10]]}
    if isinstance(value, list | tuple):
        return {"type": "array", "count": len(value)}
    return str(value)
