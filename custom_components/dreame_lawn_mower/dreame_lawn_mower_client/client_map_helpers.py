"""Map, point-cloud, rendering, and map-view helpers."""

from __future__ import annotations

import hashlib
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from .client_shared_helpers import (
    _app_action_data,
    _operation_value_type,
    _positive_int,
)
from .deadline import DeadlineExceededError, run_with_deadline
from .exceptions import (
    DreameLawnMowerError as DreameLawnMowerError,
)
from .map_visuals import (
    MapRenderStyle,
    line_width,
    map_font,
    map_render_style,
    marker_radius,
)
from .models import (
    DreameLawnMowerMapSummary,
    DreameLawnMowerMapView,
    DreameLawnMowerStatusBlob,
)
from .point_cloud import (
    DreameLawnMowerPointCloudError,
)


@dataclass(frozen=True, slots=True)
class _PointCloudObjectIdentity:
    """Stable response evidence used to detect a fixed-key object overwrite."""

    content_sha256: str
    etag: str | None
    last_modified: str | None

    def differs_from(self, other: _PointCloudObjectIdentity) -> bool:
        """Return whether content or an object-store validator changed."""
        if self.content_sha256 != other.content_sha256:
            return True
        if self.etag is not None and other.etag is not None:
            if self.etag != other.etag:
                return True
        return (
            self.last_modified is not None
            and other.last_modified is not None
            and self.last_modified != other.last_modified
        )


def _validate_app_map_chunk_size(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("chunk_size must be an integer")
    if value <= 0:
        raise ValueError("chunk_size must be greater than zero")
    return value


def _app_object_extension(value: str) -> str | None:
    name = value.rsplit("/", 1)[-1]
    if "." not in name:
        return None
    extension = name.rsplit(".", 1)[-1].strip()
    return extension or None


def _point_cloud_action_data(
    value: Any,
    operation: str,
    *,
    require_data: bool,
) -> Any:
    """Normalize point-cloud app-action failures without exposing raw payloads."""
    if not isinstance(value, Mapping) or value.get("r") != 0:
        raise DreameLawnMowerPointCloudError(f"The mower could not {operation}.")
    data = value.get("d")
    if require_data and not isinstance(data, Mapping):
        raise DreameLawnMowerPointCloudError(f"The mower could not {operation}.")
    if require_data:
        names = data.get("name")
        if not isinstance(names, Sequence) or isinstance(
            names,
            str | bytes | bytearray,
        ):
            raise DreameLawnMowerPointCloudError(f"The mower could not {operation}.")
    return data


def _point_cloud_object_name(value: Any, map_index: int) -> str | None:
    """Return one indexed object name without exposing the surrounding response."""
    names = value.get("name") if isinstance(value, Mapping) else None
    if (
        not isinstance(names, Sequence)
        or isinstance(names, str | bytes | bytearray)
        or map_index >= len(names)
    ):
        return None
    candidate = names[map_index]
    if not isinstance(candidate, str) or not candidate.strip():
        return None
    return candidate.strip()


def _validate_point_cloud_map_index(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255:
        raise DreameLawnMowerPointCloudError(
            "Point-cloud map index must be an integer between 0 and 255.",
            code="point_cloud_invalid_request",
            stage="request",
            retryable=False,
            public_message="The 3D map request contains an invalid map index.",
        )
    return value


def _validate_positive_number(value: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise DreameLawnMowerPointCloudError(
            f"Point-cloud {label} must be a positive number.",
            code="point_cloud_invalid_request",
            stage="request",
            retryable=False,
            public_message=f"The 3D map request contains an invalid {label}.",
        )
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0:
        raise DreameLawnMowerPointCloudError(
            f"Point-cloud {label} must be a positive number.",
            code="point_cloud_invalid_request",
            stage="request",
            retryable=False,
            public_message=f"The 3D map request contains an invalid {label}.",
        )
    return normalized


def _point_cloud_download_url(value: Any) -> str:
    candidate = value
    for _ in range(4):
        if isinstance(candidate, str):
            stripped = candidate.strip()
            if stripped.startswith("{"):
                try:
                    candidate = json.loads(stripped)
                except json.JSONDecodeError:
                    candidate = stripped
                else:
                    continue
            candidate = stripped
            break
        if isinstance(candidate, Mapping):
            candidate = next(
                (
                    candidate[key]
                    for key in ("url", "downloadUrl", "download_url", "data")
                    if key in candidate
                ),
                None,
            )
            continue
        break

    if not isinstance(candidate, str) or not candidate:
        raise DreameLawnMowerPointCloudError(
            "The cloud did not return a point-cloud download URL."
        )
    parsed = urllib.parse.urlsplit(candidate)
    if parsed.scheme.casefold() != "https" or not parsed.netloc:
        raise DreameLawnMowerPointCloudError(
            "The cloud returned an invalid point-cloud download URL."
        )
    return candidate


def _download_point_cloud_content(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
) -> tuple[bytes, str]:
    content, content_type, _ = _download_point_cloud_content_with_identity(
        url,
        timeout=timeout,
        max_bytes=max_bytes,
    )
    return content, content_type


def _download_point_cloud_content_with_identity(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
) -> tuple[bytes, str, _PointCloudObjectIdentity]:
    deadline = time.monotonic() + timeout
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/octet-stream, application/pcd, */*",
            "User-Agent": "homeassistant-dreamelawnmower/point-cloud",
        },
    )
    try:
        with _open_point_cloud_response(
            request,
            timeout=timeout,
            deadline=deadline,
        ) as response:
            final_url = response.geturl()
            if urllib.parse.urlsplit(final_url).scheme.casefold() != "https":
                raise DreameLawnMowerPointCloudError(
                    "The point-cloud download redirected to an insecure URL."
                )
            content_length = response.headers.get("Content-Length")
            declared_bytes: int | None = None
            if content_length is not None:
                try:
                    declared_bytes = int(content_length)
                except ValueError as err:
                    raise DreameLawnMowerPointCloudError(
                        "The point-cloud download returned an invalid size."
                    ) from err
                if declared_bytes < 0 or declared_bytes > max_bytes:
                    raise DreameLawnMowerPointCloudError(
                        "The point-cloud download exceeds the configured size limit."
                    )
            content_parts: list[bytes] = []
            received_bytes = 0
            # Once every declared byte has been read, http.client has already
            # consumed and may auto-close the underlying socket, so stop
            # before attempting one more (doomed) timeout-bounded read whose
            # only purpose would be to observe an EOF we already know about.
            while declared_bytes is None or received_bytes < declared_bytes:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DreameLawnMowerPointCloudError(
                        "The point-cloud download timed out."
                    )
                _set_point_cloud_response_timeout(response, remaining)
                read_bytes = min(64 * 1024, max_bytes + 1 - received_bytes)
                if declared_bytes is not None:
                    read_bytes = min(read_bytes, declared_bytes - received_bytes)
                chunk = response.read(read_bytes)
                if time.monotonic() >= deadline:
                    raise DreameLawnMowerPointCloudError(
                        "The point-cloud download timed out."
                    )
                if not chunk:
                    break
                content_parts.append(chunk)
                received_bytes += len(chunk)
                if received_bytes > max_bytes:
                    raise DreameLawnMowerPointCloudError(
                        "The point-cloud download exceeds the configured size limit."
                    )
            if declared_bytes is not None and received_bytes != declared_bytes:
                raise DreameLawnMowerPointCloudError(
                    "The point-cloud download ended before its declared size."
                )
            content = b"".join(content_parts)
            content_type = (
                response.headers.get_content_type()
                if hasattr(response.headers, "get_content_type")
                else "application/octet-stream"
            )
            identity = _PointCloudObjectIdentity(
                content_sha256=hashlib.sha256(content).hexdigest(),
                etag=response.headers.get("ETag"),
                last_modified=response.headers.get("Last-Modified"),
            )
    except DreameLawnMowerPointCloudError:
        raise
    except urllib.error.HTTPError as err:
        raise DreameLawnMowerPointCloudError(
            f"The point-cloud download failed with HTTP status {err.code}."
        ) from err
    except TimeoutError as err:
        raise DreameLawnMowerPointCloudError(
            "The point-cloud download timed out."
        ) from err
    except (urllib.error.URLError, OSError) as err:
        raise DreameLawnMowerPointCloudError(
            "The point-cloud download could not be completed."
        ) from err

    return content, content_type, identity


class _HttpsOnlyPointCloudRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject a redirect before urllib can issue a non-HTTPS request."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        target = urllib.parse.urljoin(req.full_url, newurl)
        parsed = urllib.parse.urlsplit(target)
        if parsed.scheme.casefold() != "https" or not parsed.netloc:
            raise DreameLawnMowerPointCloudError(
                "The point-cloud download redirected to an insecure URL."
            )
        return super().redirect_request(
            req,
            fp,
            code,
            msg,
            headers,
            target,
        )


def _open_point_cloud_response(
    request: urllib.request.Request,
    *,
    timeout: float,
    deadline: float,
) -> Any:
    """Open one point-cloud URL with HTTPS-only redirect handling."""
    opener = urllib.request.build_opener(_HttpsOnlyPointCloudRedirectHandler())
    try:
        return run_with_deadline(
            lambda: opener.open(request, timeout=timeout),
            deadline=deadline,
        )
    except DeadlineExceededError as err:
        raise DreameLawnMowerPointCloudError(
            "The point-cloud download timed out."
        ) from err


def _set_point_cloud_response_timeout(response: Any, timeout: float) -> None:
    """Apply the remaining overall deadline to the active response socket."""
    response_fp = getattr(response, "fp", None)
    response_raw = getattr(response_fp, "raw", None)
    candidates = (
        response,
        getattr(response, "_sock", None),
        response_fp,
        getattr(response_fp, "_sock", None),
        response_raw,
        getattr(response_raw, "_sock", None),
    )
    for candidate in candidates:
        set_timeout = getattr(candidate, "settimeout", None)
        if callable(set_timeout):
            set_timeout(timeout)
            return
    raise DreameLawnMowerPointCloudError(
        "The point-cloud download deadline could not be enforced."
    )


def _normalize_app_map_entries(value: Any) -> list[dict[str, Any]]:
    entries = _app_action_data(value)
    if not isinstance(entries, Sequence) or isinstance(
        entries,
        str | bytes | bytearray,
    ):
        return []

    result: list[dict[str, Any]] = []
    for item in entries:
        if not isinstance(item, Sequence) or isinstance(item, str | bytes | bytearray):
            continue
        values = list(item)
        if len(values) < 4:
            continue
        result.append(
            {
                "idx": values[0],
                "current": bool(values[1]),
                "created": bool(values[2]),
                "has_backup": bool(values[3]),
                "force_load": bool(values[4]) if len(values) > 4 else False,
            }
        )
    return result


def _app_map_payload_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"payload_type": _operation_value_type(value)}

    maps = value.get("map") if isinstance(value.get("map"), list) else []
    spots = value.get("spot") if isinstance(value.get("spot"), list) else []
    point_entries = value.get("point") if isinstance(value.get("point"), list) else []
    semantic = value.get("semantic") if isinstance(value.get("semantic"), list) else []
    trajectories = (
        value.get("trajectory") if isinstance(value.get("trajectory"), list) else []
    )
    cut_relation = (
        value.get("cut_relation") if isinstance(value.get("cut_relation"), list) else []
    )

    boundary_point_count = 0
    spot_boundary_point_count = 0
    semantic_boundary_point_count = 0
    trajectory_point_count = 0
    trajectory_length_m = 0.0
    semantic_key_counts: dict[str, int] = {}
    total_area = value.get("total_area")
    map_area_total = 0.0
    for item in maps:
        if not isinstance(item, Mapping):
            continue
        coordinates = item.get("data")
        if isinstance(coordinates, Sequence) and not isinstance(
            coordinates,
            str | bytes | bytearray,
        ):
            boundary_point_count += len(coordinates)
        area = item.get("area")
        if isinstance(area, int | float):
            map_area_total += float(area)
    for item in spots:
        if not isinstance(item, Mapping):
            continue
        coordinates = item.get("data")
        if isinstance(coordinates, Sequence) and not isinstance(
            coordinates,
            str | bytes | bytearray,
        ):
            spot_boundary_point_count += len(coordinates)
    for item in semantic:
        if not isinstance(item, Mapping):
            continue
        for key in item:
            semantic_key_counts[str(key)] = semantic_key_counts.get(str(key), 0) + 1
        coordinates = item.get("data")
        if isinstance(coordinates, Sequence) and not isinstance(
            coordinates,
            str | bytes | bytearray,
        ):
            semantic_boundary_point_count += len(coordinates)
    for item in trajectories:
        if not isinstance(item, Mapping):
            continue
        coordinates = item.get("data")
        if isinstance(coordinates, Sequence) and not isinstance(
            coordinates,
            str | bytes | bytearray,
        ):
            trajectory_points = _app_map_points(coordinates)
            trajectory_point_count += len(trajectory_points)
            trajectory_length_m += _coordinate_path_length_m(trajectory_points)

    return {
        "name": value.get("name"),
        "total_area": total_area,
        "map_area_total": round(map_area_total, 2),
        "map_area_count": len(maps),
        "boundary_point_count": boundary_point_count,
        "spot_count": len(spots),
        "spot_boundary_point_count": spot_boundary_point_count,
        "point_count": len(point_entries),
        "point_entry_shapes": _app_map_point_entry_shapes(point_entries),
        "semantic_count": len(semantic),
        "semantic_boundary_point_count": semantic_boundary_point_count,
        "semantic_key_counts": dict(sorted(semantic_key_counts.items())),
        "trajectory_count": len(trajectories),
        "trajectory_point_count": trajectory_point_count,
        "trajectory_length_m": round(trajectory_length_m, 2),
        "cut_relation_count": len(cut_relation),
    }


def _app_map_point_entry_shapes(entries: Sequence[Any]) -> list[dict[str, Any]]:
    """Describe maintenance-point records without exposing coordinates."""
    grouped: dict[tuple[Any, ...], int] = {}
    for entry in entries:
        if isinstance(entry, Mapping):
            shape: tuple[Any, ...] = (
                "object",
                tuple(sorted(str(key) for key in entry)),
            )
        elif isinstance(entry, Sequence) and not isinstance(
            entry,
            str | bytes | bytearray,
        ):
            shape = (
                "array",
                len(entry),
                tuple(sorted({_operation_value_type(item) for item in entry})),
            )
        else:
            shape = (_operation_value_type(entry),)
        grouped[shape] = grouped.get(shape, 0) + 1

    result: list[dict[str, Any]] = []
    for shape, count in sorted(grouped.items(), key=lambda item: repr(item[0])):
        entry: dict[str, Any] = {"kind": shape[0], "count": count}
        if shape[0] == "object":
            entry["keys"] = list(shape[1])
        elif shape[0] == "array":
            entry["length"] = shape[1]
            entry["item_types"] = list(shape[2])
        result.append(entry)
    return result


def _select_app_map_payload(app_maps: Mapping[str, Any]) -> Mapping[str, Any] | None:
    maps = app_maps.get("maps") if isinstance(app_maps, Mapping) else None
    if not isinstance(maps, Sequence) or isinstance(maps, str | bytes | bytearray):
        return None
    current_idx = app_maps.get("current_map_index")
    available_maps = [
        item
        for item in maps
        if isinstance(item, Mapping)
        and bool(item.get("available"))
        and isinstance(item.get("payload"), Mapping)
    ]
    for item in available_maps:
        if item.get("idx") == current_idx:
            return item
    return available_maps[0] if available_maps else None


def _map_view_current_app_map_index(view: DreameLawnMowerMapView) -> int | None:
    app_maps = view.app_maps
    if not isinstance(app_maps, Mapping):
        return None
    return _positive_int(app_maps.get("current_map_index"))


def _app_maps_view_metadata(app_maps: Mapping[str, Any]) -> dict[str, Any]:
    maps = app_maps.get("maps") if isinstance(app_maps, Mapping) else None
    if not isinstance(maps, Sequence) or isinstance(maps, str | bytes | bytearray):
        maps = []

    entries = [
        _app_map_entry_view_metadata(item) for item in maps if isinstance(item, Mapping)
    ]
    objects = _app_map_objects_view_metadata(app_maps.get("objects"))
    return {
        "source": app_maps.get("source"),
        "available": bool(app_maps.get("available")),
        "map_count": app_maps.get("map_count", len(entries)),
        "current_map_index": app_maps.get("current_map_index"),
        "available_map_count": sum(1 for item in entries if item.get("available")),
        "created_map_count": sum(1 for item in entries if item.get("created")),
        "maps": entries,
        "objects": objects.get("objects"),
        "object_count": objects.get("object_count"),
        "object_error": objects.get("error"),
        "error_count": len(app_maps.get("errors", []) or []),
    }


def _app_map_objects_view_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"objects": None, "object_count": None, "error": None}
    objects = value.get("objects")
    if not isinstance(objects, Sequence) or isinstance(
        objects,
        str | bytes | bytearray,
    ):
        objects = []
    entries = [
        {
            key: item.get(key)
            for key in ("extension", "url_present", "error")
            if item.get(key) is not None
        }
        for item in objects
        if isinstance(item, Mapping)
    ]
    return {
        "objects": entries,
        "object_count": value.get("object_count", len(entries)),
        "error": value.get("error"),
    }


def _app_map_entry_view_metadata(entry: Mapping[str, Any]) -> dict[str, Any]:
    summary = entry.get("summary") if isinstance(entry.get("summary"), Mapping) else {}
    info = entry.get("info") if isinstance(entry.get("info"), Mapping) else {}
    result = {
        "idx": entry.get("idx"),
        "current": bool(entry.get("current")),
        "created": bool(entry.get("created")),
        "available": bool(entry.get("available")),
        "has_backup": bool(entry.get("has_backup")),
        "force_load": bool(entry.get("force_load")),
        "reported_size": entry.get("reported_size") or info.get("size"),
        "received_size": entry.get("received_size"),
        "chunk_count": entry.get("chunk_count"),
        "hash_match": entry.get("hash_match"),
        "payload_keys": entry.get("payload_keys"),
        "name": summary.get("name"),
        "total_area": summary.get("total_area"),
        "map_area_count": summary.get("map_area_count"),
        "map_area_total": summary.get("map_area_total"),
        "boundary_point_count": summary.get("boundary_point_count"),
        "spot_count": summary.get("spot_count"),
        "point_count": summary.get("point_count"),
        "point_entry_shapes": summary.get("point_entry_shapes"),
        "trajectory_count": summary.get("trajectory_count"),
        "trajectory_point_count": summary.get("trajectory_point_count"),
        "trajectory_length_m": summary.get("trajectory_length_m"),
        "semantic_count": summary.get("semantic_count"),
        "cut_relation_count": summary.get("cut_relation_count"),
        "error": entry.get("error"),
    }
    return {key: value for key, value in result.items() if value not in (None, [], {})}


def _app_map_view_summary(
    selected: Mapping[str, Any],
    payload: Any,
    width: int,
    height: int,
) -> DreameLawnMowerMapSummary:
    payload_summary = _app_map_payload_summary(payload)
    map_id = selected.get("idx")
    return DreameLawnMowerMapSummary(
        available=True,
        map_id=map_id if isinstance(map_id, int) else None,
        width=width,
        height=height,
        saved_map=bool(selected.get("created")),
        segment_count=int(payload_summary.get("map_area_count") or 0),
        active_area_count=int(payload_summary.get("map_area_count") or 0),
        active_point_count=int(payload_summary.get("point_count") or 0),
        path_point_count=int(payload_summary.get("trajectory_point_count") or 0),
        spot_area_count=int(payload_summary.get("spot_count") or 0),
    )


def _app_map_view_details(
    selected: Mapping[str, Any],
    payload: Any,
) -> dict[str, Any]:
    payload_summary = _app_map_payload_summary(payload)
    return {
        "map_name": payload_summary.get("name"),
        "map_index": selected.get("idx"),
        "total_area": payload_summary.get("total_area"),
        "map_area_total": payload_summary.get("map_area_total"),
        "zone_count": payload_summary.get("map_area_count"),
        "spot_area_count": payload_summary.get("spot_count"),
        "clean_point_count": payload_summary.get("point_count"),
        "trajectory_count": payload_summary.get("trajectory_count"),
        "trajectory_point_count": payload_summary.get("trajectory_point_count"),
        "trajectory_length_m": payload_summary.get("trajectory_length_m"),
        "cut_relation_count": payload_summary.get("cut_relation_count"),
        "has_live_path": bool(payload_summary.get("trajectory_point_count")),
        "current": bool(selected.get("current")),
        "created": bool(selected.get("created")),
    }


def render_app_map_payload_png(
    payload: Any,
    *,
    label_scale: float = 1.0,
    style: MapRenderStyle | None = None,
) -> tuple[bytes, int, int]:
    """Render a mower-native app map payload to PNG bytes."""
    return _render_app_map_payload_png(
        payload,
        label_scale=label_scale,
        style=style,
    )


def _render_app_map_payload_png(
    payload: Any,
    *,
    label_scale: float = 1.0,
    style: MapRenderStyle | None = None,
) -> tuple[bytes, int, int]:
    if not isinstance(payload, Mapping):
        raise ValueError("App map payload is missing.")

    map_entries = _app_map_coordinate_entries(payload.get("map"), "Area")
    spot_entries = _app_map_coordinate_entries(payload.get("spot"), "Spot")
    map_polygons = [entry["points"] for entry in map_entries]
    spot_polygons = [entry["points"] for entry in spot_entries]
    trajectories = _app_map_coordinate_sets(payload.get("trajectory"))
    points = _app_map_points(payload.get("point"))
    all_points = [
        point
        for group in [*map_polygons, *spot_polygons, *trajectories, points]
        for point in group
    ]
    if not all_points:
        raise ValueError("App map payload does not contain drawable coordinates.")

    min_x = min(point[0] for point in all_points)
    max_x = max(point[0] for point in all_points)
    min_y = min(point[1] for point in all_points)
    max_y = max(point[1] for point in all_points)
    span_x = max(max_x - min_x, 1)
    span_y = max(max_y - min_y, 1)
    padding = 48
    canvas = 900
    scale = min((canvas - padding * 2) / span_x, (canvas - padding * 2) / span_y)
    width = max(int(span_x * scale) + padding * 2, 320)
    height = max(int(span_y * scale) + padding * 2, 320)

    def project(point: tuple[float, float]) -> tuple[int, int]:
        x, y = point
        return (
            int(round((x - min_x) * scale + padding)),
            int(round((max_y - y) * scale + padding)),
        )

    from PIL import Image, ImageDraw

    style = style or map_render_style()
    image = Image.new("RGBA", (width, height), style.background)
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for index, polygon in enumerate(sorted(map_polygons, key=len, reverse=True)):
        projected = [project(point) for point in polygon]
        if len(projected) >= 3:
            fill = style.zone_fills[index % len(style.zone_fills)]
            outline = style.zone_outlines[index % len(style.zone_outlines)]
            draw.polygon(
                projected,
                fill=fill,
                outline=outline,
            )
            draw.line(
                projected + [projected[0]],
                fill=outline,
                width=line_width(style, 4),
            )

    for polygon in spot_polygons:
        projected = [project(point) for point in polygon]
        if len(projected) >= 3:
            draw.polygon(
                projected,
                fill=style.spot_fill,
                outline=style.spot_outline,
            )
            draw.line(
                projected + [projected[0]],
                fill=style.spot_outline,
                width=line_width(style, 3),
            )

    for trajectory in trajectories:
        projected = [project(point) for point in trajectory]
        if len(projected) >= 2:
            draw.line(
                projected,
                fill=style.live_path,
                width=line_width(style, 4),
                joint="curve",
            )

    for point in points:
        x, y = project(point)
        radius = marker_radius(style, 6)
        draw.ellipse(
            (x - radius, y - radius, x + radius, y + radius),
            fill=style.point,
        )

    font = _app_map_label_font(label_scale)
    for entry in [*map_entries, *spot_entries]:
        label = entry.get("label")
        polygon = entry.get("points")
        if not isinstance(label, str) or not polygon:
            continue
        center = project(_app_map_polygon_center(polygon))
        _draw_app_map_label(draw, center, label, font, style=style)

    image = Image.alpha_composite(image, overlay).convert("RGB")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue(), width, height


def _app_map_coordinate_entries(
    value: Any,
    label_prefix: str,
) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        data = item.get("data") if isinstance(item, Mapping) else item
        points = _app_map_points(data)
        if points:
            result.append(
                {
                    "points": points,
                    "label": _app_map_entry_label(item, label_prefix),
                }
            )
    return result


def _app_map_coordinate_sets(value: Any) -> list[list[tuple[float, float]]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    result: list[list[tuple[float, float]]] = []
    for item in value:
        data = item.get("data") if isinstance(item, Mapping) else item
        points = _app_map_points(data)
        if points:
            result.append(points)
    return result


def _app_map_entry_label(item: Any, label_prefix: str) -> str:
    if not isinstance(item, Mapping):
        return label_prefix

    name = item.get("name")
    if isinstance(name, str) and name.strip():
        label = name.strip()
    else:
        entry_id = item.get("id")
        label = (
            f"{label_prefix} #{entry_id}"
            if entry_id not in (None, "")
            else label_prefix
        )

    area = _app_map_area_label(item.get("area"))
    return f"{label}\n{area}" if area else label


def _app_map_area_label(value: Any) -> str | None:
    if not isinstance(value, int | float) or value <= 0:
        return None
    if value >= 100:
        area = f"{value:.0f}"
    else:
        area = f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{area} m2"


def _app_map_polygon_center(
    points: Sequence[tuple[float, float]],
) -> tuple[float, float]:
    if not points:
        return (0.0, 0.0)
    return (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )


def _app_map_label_font(label_scale: float) -> Any:
    size = max(8, int(round(18 * _normalize_app_map_label_scale(label_scale))))
    return map_font(size, bold=True)


def _normalize_app_map_label_scale(label_scale: float) -> float:
    if not isinstance(label_scale, int | float) or math.isnan(float(label_scale)):
        return 1.0
    return max(0.5, min(float(label_scale), 4.0))


def _draw_app_map_label(
    draw: Any,
    center: tuple[int, int],
    label: str,
    font: Any,
    *,
    style: MapRenderStyle,
) -> None:
    for offset_x, offset_y in (
        (-2, 0),
        (2, 0),
        (0, -2),
        (0, 2),
        (-1, -1),
        (1, -1),
        (-1, 1),
        (1, 1),
    ):
        draw.multiline_text(
            (center[0] + offset_x, center[1] + offset_y),
            label,
            fill=style.label_halo,
            font=font,
            anchor="mm",
            align="center",
            spacing=2,
        )
    draw.multiline_text(
        center,
        label,
        fill=style.label,
        font=font,
        anchor="mm",
        align="center",
        spacing=2,
    )


def _app_map_points(value: Any) -> list[tuple[float, float]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    points: list[tuple[float, float]] = []
    for item in value:
        if not isinstance(item, Sequence) or isinstance(item, str | bytes | bytearray):
            continue
        if len(item) < 2:
            continue
        x, y = item[0], item[1]
        if isinstance(x, int | float) and isinstance(y, int | float):
            points.append((float(x), float(y)))
    return points


def _coordinate_path_length_m(points: Sequence[tuple[float, float]]) -> float:
    """Return an approximate path length in meters for centimeter coordinates."""
    if len(points) < 2:
        return 0.0

    total = 0.0
    previous = points[0]
    for current in points[1:]:
        total += math.hypot(current[0] - previous[0], current[1] - previous[1])
        previous = current
    return total / 100.0


def _runtime_blob_position(
    blob: DreameLawnMowerStatusBlob | None,
) -> tuple[int, int] | None:
    if blob is None:
        return None
    x = getattr(blob, "candidate_runtime_pose_x", None)
    y = getattr(blob, "candidate_runtime_pose_y", None)
    if (
        isinstance(x, int)
        and not isinstance(x, bool)
        and isinstance(y, int)
        and not isinstance(y, bool)
    ):
        return (x, y)
    return None


def _key_define_from_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    key_define = value.get("keyDefine")
    return key_define if isinstance(key_define, Mapping) else {}


def _device_list_records(value: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    if not isinstance(value, Mapping):
        return []
    result = value.get("result", value)
    page = result.get("page", result) if isinstance(result, Mapping) else {}
    records = page.get("records", []) if isinstance(page, Mapping) else []
    return [record for record in records if isinstance(record, Mapping)]


def _key_define_from_device_list_page(
    did: str,
    device_list_page: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    for record in _device_list_records(device_list_page):
        if record.get("did") == did:
            return _key_define_from_mapping(record)
    return {}


def _map_view_has_live_path(map_view: DreameLawnMowerMapView) -> bool:
    """Return whether a map view exposes an active live mowing trail."""
    if not map_view.available or map_view.image_png is None:
        return False

    details = map_view.details
    if not isinstance(details, Mapping):
        return False

    return bool(details.get("has_live_path"))
