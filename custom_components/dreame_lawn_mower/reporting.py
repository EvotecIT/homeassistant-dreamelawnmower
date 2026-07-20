"""Build privacy-aware integration report sections."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from .debug import sanitize_debug_data, sanitize_diagnostic_text

_SYSTEM_INFO_KEYS = (
    "installation_type",
    "version",
    "dev",
    "hassio",
    "virtualenv",
    "python_version",
    "docker",
    "arch",
    "container_arch",
    "os_name",
    "os_version",
    "host_os",
    "supervisor",
    "docker_version",
    "chassis",
)

_SENSITIVE_ENTITY_STATE_NAMES = frozenset(
    {
        "runtimepositionx",
        "runtimepositiony",
        "serialnumber",
    }
)


def build_report_context(
    *,
    system_info: Mapping[str, Any],
    integration_version: object,
    config_entry: object,
) -> dict[str, Any]:
    """Return version and host facts needed to reproduce integration issues."""
    return {
        "integration_version": str(integration_version),
        "home_assistant": {
            key: system_info[key] for key in _SYSTEM_INFO_KEYS if key in system_info
        },
        "config_entry": {
            "state": _enum_value(getattr(config_entry, "state", None)),
            "disabled_by": _enum_value(getattr(config_entry, "disabled_by", None)),
            "version": getattr(config_entry, "version", None),
            "minor_version": getattr(config_entry, "minor_version", None),
        },
    }


def build_coordinator_diagnostics(coordinator: object) -> dict[str, Any]:
    """Return the latest coordinator health without exposing account data."""
    update_interval = getattr(coordinator, "update_interval", None)
    last_exception = getattr(coordinator, "last_exception", None)
    return {
        "last_update_success": getattr(coordinator, "last_update_success", None),
        "last_exception_type": (
            type(last_exception).__name__ if last_exception is not None else None
        ),
        "last_exception": (
            sanitize_diagnostic_text(last_exception)
            if last_exception is not None
            else None
        ),
        "update_interval_seconds": (
            update_interval.total_seconds()
            if hasattr(update_interval, "total_seconds")
            else None
        ),
    }


def build_entity_diagnostics(
    registry_entries: Iterable[object],
    state_getter: Callable[[str], object | None],
) -> list[dict[str, Any]]:
    """Return sanitized state for every entity owned by one config entry."""
    entities: list[dict[str, Any]] = []
    for registry_entry in registry_entries:
        entity_id = str(getattr(registry_entry, "entity_id", ""))
        domain = entity_id.partition(".")[0] or None
        state = state_getter(entity_id) if entity_id else None
        state_value = getattr(state, "state", None)
        attributes = getattr(state, "attributes", {}) if state is not None else {}
        original_name = getattr(registry_entry, "original_name", None)
        translation_key = getattr(registry_entry, "translation_key", None)
        unique_id = getattr(registry_entry, "unique_id", None)
        if _entity_state_is_sensitive(original_name, translation_key, unique_id):
            state_value = "**REDACTED**" if state_value is not None else None
        entities.append(
            {
                "domain": domain,
                "original_name": original_name,
                "translation_key": translation_key,
                "entity_category": _enum_value(
                    getattr(registry_entry, "entity_category", None)
                ),
                "disabled_by": _enum_value(
                    getattr(registry_entry, "disabled_by", None)
                ),
                "loaded": state is not None,
                "available": state_value not in {None, "unavailable"},
                "state": state_value,
                "attributes": sanitize_debug_data(attributes),
            }
        )

    entities.sort(
        key=lambda item: (
            str(item.get("domain") or ""),
            str(item.get("original_name") or item.get("translation_key") or ""),
        )
    )
    return entities


def _enum_value(value: object) -> object:
    return getattr(value, "value", value)


def _entity_state_is_sensitive(
    original_name: object,
    translation_key: object,
    unique_id: object,
) -> bool:
    """Return whether an entity's scalar state contains private report data."""
    normalized_values = (
        "".join(character for character in str(value).casefold() if character.isalnum())
        for value in (original_name, translation_key, unique_id)
        if value is not None
    )
    return any(
        value.endswith(sensitive_name)
        for value in normalized_values
        for sensitive_name in _SENSITIVE_ENTITY_STATE_NAMES
    )
