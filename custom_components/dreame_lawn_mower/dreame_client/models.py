"""Reusable mower domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

SUPPORTED_ACCOUNT_TYPES = ("dreame", "mova")
SUPPORTED_MODEL_PREFIXES = ("dreame.mower.", "mova.mower.")

MODEL_NAME_MAP = {
    "dreame.mower.p2255": "A1",
    "dreame.mower.g2422": "A1 Pro",
    "dreame.mower.g2408": "A2",
    "dreame.mower.g3255": "A2 Pro",
}


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


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
    firmware_version: str | None = None
    hardware_version: str | None = None
    child_lock: bool | None = None
    docked: bool = False
    paused: bool = False
    mowing: bool = False
    returning: bool = False
    raw_attributes: Mapping[str, Any] = field(default_factory=dict, repr=False)


def descriptor_from_cloud_record(
    raw: Mapping[str, Any],
    *,
    account_type: str,
    country: str,
) -> DreameLawnMowerDescriptor | None:
    """Convert a raw cloud device record into a normalized descriptor."""

    model = _as_optional_str(raw.get("model"))
    if model is None or not any(model.startswith(prefix) for prefix in SUPPORTED_MODEL_PREFIXES):
        return None

    name = (
        _as_optional_str(raw.get("customName"))
        or _as_optional_str(raw.get("name"))
        or _as_optional_str(raw.get("deviceInfo", {}).get("displayName"))
        or MODEL_NAME_MAP.get(model)
        or model
    )
    display_model = MODEL_NAME_MAP.get(model, model)

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

    if getattr(device.status, "has_error", False):
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
        error_code=getattr(error_obj, "value", None),
        error_name=getattr(device.status, "error_name", None),
        firmware_version=getattr(getattr(device, "info", None), "firmware_version", None),
        hardware_version=getattr(getattr(device, "info", None), "hardware_version", None),
        child_lock=child_lock,
        docked=bool(getattr(device.status, "docked", False)),
        paused=bool(getattr(device.status, "paused", False)),
        mowing=bool(getattr(device.status, "running", False)),
        returning=bool(getattr(device.status, "returning", False)),
        raw_attributes=getattr(device.status, "attributes", {}) or {},
    )
