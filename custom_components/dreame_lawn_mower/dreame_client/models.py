"""Reusable mower domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
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
    error_name = _as_optional_str(getattr(device.status, "error_name", None))
    error_text = _as_optional_str(status_attributes.get("error"))
    error_code = getattr(error_obj, "value", None)
    has_error = bool(
        getattr(device.status, "has_error", False)
        or error_text
        or (error_code not in (None, -1))
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
