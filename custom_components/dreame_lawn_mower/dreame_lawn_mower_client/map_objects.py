"""Shared privacy-preserving map-object descriptions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .client_map_helpers import _app_object_extension
from .client_shared_helpers import _app_action_data
from .payload_utils import _json_safe
from .point_cloud_diagnostics import value_shape


def map_object_names(response: Any) -> Sequence[Any]:
    """Read the object list without coercing invalid private names."""
    data = _app_action_data(response)
    names = data.get("name") if isinstance(data, Mapping) else None
    return names if isinstance(names, Sequence) and not isinstance(
        names, str | bytes | bytearray,
    ) else []


def normalized_object_names(names: Sequence[Any]) -> tuple[str | None, ...]:
    """Retain positional slots while normalizing valid private names."""
    return tuple(name.strip() if isinstance(name, str) and name.strip() else None
                 for name in names)


def map_object_description(raw_name: Any, *, include_urls: bool) -> dict[str, Any]:
    """Expose names only when the caller explicitly opts into URL diagnostics."""
    item = {
        "extension": _app_object_extension(str(raw_name)), "url_present": False,
        "name_shape": value_shape(raw_name),
        "name_present": isinstance(raw_name, str) and bool(raw_name.strip()),
        "url_checked": False,
    }
    if include_urls:
        item["name"] = str(raw_name)
    return item


def map_objects_result(
    response: Any, objects: list[dict[str, Any]], *, include_urls: bool,
) -> dict[str, Any]:
    """Build the common public result and opt-in raw evidence."""
    result = {
        "source": "app_action_obj_3dmap", "object_count": len(objects),
        "named_object_count": sum(item["name_present"] for item in objects),
        "objects": objects, "urls_included": bool(include_urls),
    }
    if include_urls:
        result["raw"] = _json_safe(response, max_depth=4)
    return result
