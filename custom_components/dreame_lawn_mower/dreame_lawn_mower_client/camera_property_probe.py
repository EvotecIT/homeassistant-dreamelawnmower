"""Camera property-probe policy shared by synchronous and native transports."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from .client_camera import _safe_get_device_property
from .payload_utils import _json_safe, _lower_enum_name


def camera_property_probe_plan(
    device: Any,
) -> Generator[list[dict[str, Any]], Any, dict[str, Any]]:
    """Select camera properties and retain diagnostic/application semantics."""
    try:
        from .device_types import DreameMowerProperty
    except ImportError:
        return {"error": "Camera protocol types are unavailable."}

    properties = (
        DreameMowerProperty.STREAM_STATUS,
        DreameMowerProperty.STREAM_AUDIO,
        DreameMowerProperty.STREAM_RECORD,
        DreameMowerProperty.TAKE_PHOTO,
        DreameMowerProperty.STREAM_KEEP_ALIVE,
        DreameMowerProperty.STREAM_FAULT,
        DreameMowerProperty.STREAM_PROPERTY,
        DreameMowerProperty.STREAM_TASK,
        DreameMowerProperty.STREAM_UPLOAD,
        DreameMowerProperty.STREAM_CODE,
    )
    requested = []
    for prop in properties:
        mapping = getattr(device, "property_mapping", {}).get(prop)
        if mapping and "aiid" not in mapping:
            requested.append({"did": str(prop.value), **mapping})

    protocol = getattr(device, "_protocol", None)
    if protocol is None:
        return {
            "requested_property_count": len(requested),
            "requested_properties": requested,
            "error": "Device protocol is unavailable.",
        }

    raw_response = None
    handled = False
    error = None
    try:
        raw_response = yield requested
        if raw_response is None:
            error = "Device protocol returned no property response."
        else:
            handled = bool(device._handle_properties(raw_response))
    except Exception as err:
        error = str(err)

    values = {}
    for prop in properties:
        values[prop.name.lower()] = _json_safe(
            _safe_get_device_property(device, prop)
        )

    status = getattr(device, "status", None)
    return {
        "requested_property_count": len(requested),
        "requested_properties": requested,
        "raw_response": _json_safe(raw_response),
        "handled": handled,
        "error": error,
        "values": values,
        "stream_session_present": bool(getattr(status, "stream_session", None)),
        "stream_status": _lower_enum_name(getattr(status, "stream_status", None)),
    }
