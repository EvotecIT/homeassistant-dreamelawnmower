"""Reusable client map, point-cloud, and cloud-property operations."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from .app_protocol import (
    MOWER_ERROR_PROPERTY_KEY,
    MOWER_PROPERTY_HINTS,
    MOWER_RAW_STATUS_PROPERTY_KEY,
    MOWER_RUNTIME_STATUS_PROPERTY_KEY,
    MOWER_STATE_PROPERTY_KEY,
    MOWER_TASK_PROPERTY_KEY,
    decode_mower_status_blob,
    decode_mower_task_status,
    key_definition_label,
    mower_error_label,
    mower_state_key,
    mower_state_label,
)
from .client_map_helpers import (
    _app_map_payload_summary,
    _app_map_view_details,
    _app_map_view_summary,
    _app_maps_view_metadata,
    _app_object_extension,
    _coordinate_path_length_m,
    _download_point_cloud_content_with_identity,
    _key_define_from_device_list_page,
    _key_define_from_mapping,
    _map_view_current_app_map_index,
    _map_view_has_live_path,
    _normalize_app_map_entries,
    _point_cloud_action_data,
    _point_cloud_download_url,
    _point_cloud_object_name,
    _PointCloudObjectIdentity,
    _render_app_map_payload_png,
    _runtime_blob_position,
    _select_app_map_payload,
    _validate_app_map_chunk_size,
    _validate_point_cloud_map_index,
    _validate_positive_number,
)
from .client_shared_helpers import (
    _app_action_data,
    _property_entry_received_at,
)
from .exceptions import (
    DeviceException,
    DreameLawnMowerConnectionError,
)
from .exceptions import (
    DreameLawnMowerError as DreameLawnMowerError,
)
from .map_probe import (
    MAP_HISTORY_PROPERTY_KEYS,
    MAP_PROBE_PROPERTY_KEYS,
    build_cloud_property_summary,
    build_map_probe_payload,
)
from .models import (
    DreameLawnMowerMapSummary,
    DreameLawnMowerMapView,
    map_diagnostics_from_device,
    map_summary_from_map_data,
)
from .mowing_tasks import (
    MowingTaskResponseError,
    ensure_mowing_task_succeeded,
)
from .payload_utils import (
    _json_safe,
)
from .point_cloud import (
    DreameLawnMowerPointCloudDownload,
    DreameLawnMowerPointCloudError,
    parse_pcd_metadata,
)
from .vector_map import (
    filter_runtime_track_segments,
    parse_batch_vector_map,
    position_within_vector_map,
    render_vector_map_png,
    vector_map_to_details,
    vector_map_to_summary,
)

if TYPE_CHECKING:
    from .map_visuals import MapRenderStyle

# MOVA reports the fixed 3dmap object as "*.bin" even though the payload is PCD.
_POINT_CLOUD_OBJECT_EXTENSIONS = frozenset({"pcd"})
_MOVA_POINT_CLOUD_OBJECT_EXTENSIONS = frozenset({"pcd", "bin"})
_POINT_CLOUD_ANNOUNCEMENT_EXTENSIONS = frozenset({"pcd", "bin"})
_POINT_CLOUD_ANNOUNCEMENT_PROPERTY_KEY = "99.20"
_POINT_CLOUD_ANNOUNCEMENT_CLOCK_SKEW_MS = 5_000
_POINT_CLOUD_ANNOUNCEMENT_PROBE_TIMEOUT_SECONDS = 8.0
_POINT_CLOUD_ANNOUNCEMENT_MAX_DOWNLOAD_ATTEMPTS = 3
_POINT_CLOUD_STORED_DOWNLOAD_TIMEOUT_SECONDS = 5.0


class _DreameLawnMowerClientMapsMixin:
    def _sync_switch_current_map(self, map_index: int) -> Any:
        """Switch the active mower map by app map index."""
        if map_index < 0:
            raise ValueError("map_index must be zero or greater.")
        try:
            response = self._sync_call_app_action(
                {
                    "m": "a",
                    "p": 0,
                    "o": 200,
                    "d": {"idx": int(map_index)},
                }
            )
            return ensure_mowing_task_succeeded(response, task_name="map switch")
        except (DeviceException, MowingTaskResponseError) as err:
            raise DreameLawnMowerConnectionError(str(err)) from err

    def _sync_get_vector_map_details(self) -> dict[str, Any]:
        """Return parsed batch vector-map details without rendering an image."""
        try:
            batch_data = self._sync_get_vector_map_batch_data()
        except DreameLawnMowerConnectionError as err:
            return {
                "available": False,
                "source": "batch_vector_map",
                "error": str(err),
            }

        vector_map = parse_batch_vector_map(
            batch_data,
            current_map_index=self._sync_get_current_app_map_index(),
        )
        if vector_map is None:
            return {
                "available": False,
                "source": "batch_vector_map",
                "error": "No vector map data returned by the batch map path.",
            }

        details = vector_map_to_details(vector_map)
        details["available"] = True
        details["source"] = "batch_vector_map"
        return details

    def _sync_refresh_map_summary(
        self,
        timeout: float,
        interval: float,
    ) -> DreameLawnMowerMapSummary | None:
        return self._sync_refresh_map_view(timeout, interval).summary

    def _sync_get_map_png(
        self,
        timeout: float,
        interval: float,
        label_scale: float = 1.0,
        style: MapRenderStyle | None = None,
    ) -> bytes | None:
        return self._sync_refresh_map_view(
            timeout,
            interval,
            label_scale,
            style,
        ).image_png

    def _sync_refresh_map_view(
        self,
        timeout: float,
        interval: float,
        label_scale: float = 1.0,
        style: MapRenderStyle | None = None,
    ) -> DreameLawnMowerMapView:
        app_view = self._sync_refresh_app_map_view(
            legacy_error=None,
            legacy_reason="app_action_map_primary",
            label_scale=label_scale,
            style=style,
        )

        vector_view = self._with_fallback_app_maps(
            self._sync_refresh_vector_map_view(
                label_scale=label_scale,
                current_map_index=_map_view_current_app_map_index(app_view),
                style=style,
            ),
            app_view,
        )
        if _map_view_has_live_path(vector_view) or (
            isinstance(vector_view.details, Mapping)
            and vector_view.details.get("runtime_position_valid") is True
        ):
            return vector_view

        if app_view.available and app_view.image_png is not None:
            return self._with_runtime_position_details(app_view, vector_view)

        if vector_view.available and vector_view.image_png is not None:
            return vector_view

        legacy_view = self._sync_refresh_legacy_map_view(
            timeout,
            interval,
            label_scale=label_scale,
            style=style,
        )
        legacy_view = self._with_fallback_app_maps(legacy_view, app_view)
        if legacy_view.available or legacy_view.image_png is not None:
            return legacy_view

        return app_view

    def _sync_refresh_vector_map_view(
        self,
        *,
        label_scale: float = 1.0,
        current_map_index: int | None = None,
        style: MapRenderStyle | None = None,
    ) -> DreameLawnMowerMapView:
        source = "batch_vector_map"
        try:
            batch_data = self._sync_get_vector_map_batch_data()
        except DreameLawnMowerConnectionError as err:
            error = str(err)
            return DreameLawnMowerMapView(
                source=source,
                error=error,
                diagnostics=self._safe_map_diagnostics(
                    source=source,
                    reason=error,
                ),
            )

        vector_map = parse_batch_vector_map(
            batch_data,
            current_map_index=(
                current_map_index
                if current_map_index is not None
                else self._sync_get_current_app_map_index()
            ),
        )
        if vector_map is None:
            return DreameLawnMowerMapView(
                source=source,
                error="No vector map data returned by the batch map path.",
                diagnostics=self._safe_map_diagnostics(
                    source=source,
                    reason="batch_vector_map_empty",
                ),
            )

        runtime_blob = self._latest_runtime_status_blob
        if self._runtime_session_active is False:
            vector_map.mow_paths = ()
        if (
            runtime_blob is not None
            and self._runtime_live_map_index is not None
            and self._runtime_live_map_index != vector_map.map_index
        ):
            self._runtime_live_track_segments = ()
            self._last_runtime_track_blob_hex = None
            self._runtime_live_map_index = vector_map.map_index
        summary = vector_map_to_summary(vector_map)
        details = vector_map_to_details(vector_map)
        runtime_context_matches = self._runtime_live_map_index in (
            None,
            vector_map.map_index,
        )
        runtime_track_segments = (
            filter_runtime_track_segments(
                vector_map,
                self._runtime_live_track_segments,
            )
            if runtime_context_matches
            else ()
        )
        runtime_track_point_count = sum(
            len(segment) for segment in runtime_track_segments
        )
        runtime_pose_x = getattr(runtime_blob, "candidate_runtime_pose_x", None)
        runtime_pose_y = getattr(runtime_blob, "candidate_runtime_pose_y", None)
        runtime_position = (
            _runtime_blob_position(runtime_blob) if runtime_context_matches else None
        )
        runtime_position_valid = position_within_vector_map(
            vector_map,
            runtime_position,
        )
        if runtime_pose_x is not None and runtime_pose_y is not None:
            details["runtime_pose_x"] = runtime_pose_x
            details["runtime_pose_y"] = runtime_pose_y
            details["runtime_position_valid"] = runtime_position_valid
            details["runtime_heading_deg"] = getattr(
                runtime_blob,
                "candidate_runtime_heading_deg",
                None,
            )
            details["runtime_region_id"] = getattr(
                runtime_blob,
                "candidate_runtime_region_id",
                None,
            )
            details["runtime_position_updated_at"] = getattr(
                runtime_blob,
                "received_at",
                None,
            )
        if runtime_track_point_count:
            details["runtime_track_segment_count"] = len(runtime_track_segments)
            details["runtime_track_point_count"] = runtime_track_point_count
            details["runtime_track_length_m"] = round(
                sum(
                    _coordinate_path_length_m(segment)
                    for segment in runtime_track_segments
                ),
                2,
            )
            details["has_live_path"] = True
            if summary is not None:
                summary = replace(
                    summary,
                    path_point_count=summary.path_point_count
                    + runtime_track_point_count,
                )
        try:
            image_png = render_vector_map_png(
                vector_map,
                label_scale=label_scale,
                runtime_track_segments=runtime_track_segments,
                runtime_position=runtime_position if runtime_position_valid else None,
                style=style,
            )
        except Exception as err:  # noqa: BLE001 - diagnostics path
            return DreameLawnMowerMapView(
                source=source,
                summary=summary,
                details=details,
                error=f"Failed to render vector map data: {err}",
                diagnostics=self._safe_map_diagnostics(
                    source=source,
                    reason="batch_vector_map_render_failed",
                ),
            )

        if image_png is None:
            return DreameLawnMowerMapView(
                source=source,
                summary=summary,
                details=details,
                error="Vector map renderer did not produce an image.",
                diagnostics=self._safe_map_diagnostics(
                    source=source,
                    reason="batch_vector_map_render_empty",
                ),
            )

        return DreameLawnMowerMapView(
            source=source,
            summary=summary,
            image_png=image_png,
            details=details,
            diagnostics=self._safe_map_diagnostics(
                source=source,
                reason="batch_vector_map_rendered",
            ),
        )

    def _sync_refresh_legacy_map_view(
        self,
        timeout: float,
        interval: float,
        *,
        label_scale: float = 1.0,
        style: MapRenderStyle | None = None,
    ) -> DreameLawnMowerMapView:
        source = "legacy_current_map"
        try:
            map_data = self._sync_wait_for_map(timeout, interval)
        except DreameLawnMowerConnectionError as err:
            error = str(err)
            return DreameLawnMowerMapView(
                source=source,
                error=error,
                diagnostics=self._safe_map_diagnostics(
                    source=source,
                    reason=error,
                ),
            )

        if map_data is None:
            error = "No map data returned by the legacy current-map path."
            return DreameLawnMowerMapView(
                source=source,
                error=error,
                diagnostics=self._safe_map_diagnostics(
                    source=source,
                    reason="legacy_current_map_empty",
                ),
            )

        summary = map_summary_from_map_data(map_data)
        device = self._ensure_device()
        render_map_data = device.get_map_for_render(map_data) or map_data

        from .legacy_map_visuals import render_legacy_map_png

        try:
            image_png = render_legacy_map_png(
                render_map_data,
                label_scale=label_scale,
                style=style,
            )
        except Exception as err:
            error = f"Failed to render map data: {err}"
            return DreameLawnMowerMapView(
                source=source,
                summary=summary,
                error=error,
                diagnostics=self._safe_map_diagnostics(
                    source=source,
                    reason="legacy_current_map_render_failed",
                ),
            )

        return DreameLawnMowerMapView(
            source=source,
            summary=summary,
            image_png=image_png,
            diagnostics=self._safe_map_diagnostics(
                source=source,
                reason="legacy_current_map_rendered",
            ),
        )

    def _sync_refresh_app_map_view(
        self,
        *,
        legacy_error: str | None,
        legacy_reason: str,
        label_scale: float = 1.0,
        style: MapRenderStyle | None = None,
    ) -> DreameLawnMowerMapView:
        source = "app_action_map"
        try:
            app_maps = self._sync_get_app_maps(
                chunk_size=400,
                include_payload=True,
                include_objects=True,
                include_object_urls=False,
            )
            selected = _select_app_map_payload(app_maps)
            if selected is None:
                error = legacy_error or "No app-map payload was returned."
                return DreameLawnMowerMapView(
                    source=source,
                    error=error,
                    app_maps=_app_maps_view_metadata(app_maps),
                    diagnostics=self._safe_map_diagnostics(
                        source=source,
                        reason=legacy_reason,
                    ),
                )
            payload = selected.get("payload")
            image_png, width, height = _render_app_map_payload_png(
                payload,
                label_scale=label_scale,
                style=style,
            )
            return DreameLawnMowerMapView(
                source=source,
                summary=_app_map_view_summary(selected, payload, width, height),
                image_png=image_png,
                details=_app_map_view_details(selected, payload),
                app_maps=_app_maps_view_metadata(app_maps),
                diagnostics=self._safe_map_diagnostics(
                    source=source,
                    reason="app_action_map_rendered",
                ),
            )
        except Exception as err:  # noqa: BLE001 - map view keeps diagnostics visible
            error = f"{legacy_error or 'Legacy map unavailable'}; app map failed: {err}"
            return DreameLawnMowerMapView(
                source=source,
                error=error,
                diagnostics=self._safe_map_diagnostics(
                    source=source,
                    reason="app_action_map_failed",
                ),
            )

    def _sync_get_vector_map_batch_data(self) -> Mapping[str, Any] | None:
        # An empty property list requests every available batch key. M_PATH
        # history is device-sized and has been observed beyond 28 chunks, so a
        # fixed key range silently truncates long mowing paths.
        return self._sync_get_batch_device_data()

    def _sync_get_app_maps(
        self,
        chunk_size: int = 400,
        include_payload: bool = False,
        include_objects: bool = True,
        include_object_urls: bool = False,
    ) -> dict[str, Any]:
        chunk_size = _validate_app_map_chunk_size(chunk_size)
        map_list_result = self._sync_call_app_action({"m": "g", "t": "MAPL"})
        map_entries = _normalize_app_map_entries(map_list_result)
        result: dict[str, Any] = {
            "source": "app_action_map",
            "available": False,
            "map_count": len(map_entries),
            "current_map_index": None,
            "raw_map_list": _json_safe(map_list_result, max_depth=5),
            "maps": [],
            "errors": [],
        }
        if include_objects:
            try:
                result["objects"] = self._sync_get_app_map_objects(
                    include_urls=include_object_urls,
                )
            except Exception as err:  # noqa: BLE001 - object metadata is diagnostic
                result["objects"] = {"error": str(err)}

        for entry in map_entries:
            if entry.get("current"):
                result["current_map_index"] = entry["idx"]
            if not entry.get("created"):
                result["maps"].append(entry)
                continue

            map_result = dict(entry)
            try:
                info_result = self._sync_call_app_action(
                    {"m": "g", "t": "MAPI", "d": {"idx": entry["idx"]}}
                )
                info = _app_action_data(info_result)
                map_result["info"] = _json_safe(info, max_depth=4)
                size = info.get("size") if isinstance(info, Mapping) else None
                expected_hash = info.get("hash") if isinstance(info, Mapping) else None
                if isinstance(size, int) and size > 0:
                    payload_text, chunk_count, received_size = (
                        self._sync_get_app_map_text(
                            size=size,
                            chunk_size=chunk_size,
                        )
                    )
                    payload_hash = hashlib.md5(payload_text.encode("utf-8")).hexdigest()
                    parsed_payload = json.loads(payload_text)
                    hash_match = (
                        expected_hash == payload_hash
                        if isinstance(expected_hash, str)
                        else None
                    )
                    if hash_match is False:
                        raise DreameLawnMowerConnectionError(
                            "App map payload hash mismatch."
                        )
                    map_result.update(
                        {
                            "available": True,
                            "reported_size": size,
                            "received_size": received_size,
                            "decoded_size": len(payload_text.encode("utf-8")),
                            "chunk_count": chunk_count,
                            "md5": payload_hash,
                            "hash_match": hash_match,
                            "payload_keys": (
                                sorted(str(key) for key in parsed_payload)
                                if isinstance(parsed_payload, Mapping)
                                else []
                            ),
                            "summary": _app_map_payload_summary(parsed_payload),
                        }
                    )
                    if include_payload:
                        map_result["payload"] = _json_safe(
                            parsed_payload,
                            max_depth=12,
                        )
                else:
                    map_result["available"] = False
                    map_result["error"] = "map_info_missing_size"
            except Exception as err:  # noqa: BLE001 - probes keep per-map evidence
                map_result["available"] = False
                map_result["error"] = str(err)
                result["errors"].append({"idx": entry.get("idx"), "error": str(err)})

            result["maps"].append(map_result)

        result["available"] = any(
            isinstance(item, Mapping) and bool(item.get("available"))
            for item in result["maps"]
        )
        return result

    def _sync_get_app_map_objects(
        self,
        include_urls: bool = False,
    ) -> dict[str, Any]:
        object_result = self._sync_call_app_action(
            {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
            redact_response=True,
        )
        data = _app_action_data(object_result)
        names = data.get("name") if isinstance(data, Mapping) else None
        if not isinstance(names, Sequence) or isinstance(
            names,
            str | bytes | bytearray,
        ):
            names = []

        objects: list[dict[str, Any]] = []
        cloud = self._sync_get_cloud_protocol() if include_urls else None
        for raw_name in names:
            name = str(raw_name)
            item: dict[str, Any] = {
                "extension": _app_object_extension(name),
                "url_present": False,
            }
            if include_urls:
                item["name"] = name
                try:
                    url = (
                        cloud.get_interim_file_url(name)
                        if cloud is not None and hasattr(cloud, "get_interim_file_url")
                        else None
                    )
                    item["url_present"] = bool(url)
                    item["url"] = url
                except Exception as err:  # noqa: BLE001 - preserve per-object evidence
                    item["error"] = str(err)
            objects.append(item)

        result = {
            "source": "app_action_obj_3dmap",
            "object_count": len(objects),
            "objects": objects,
            "urls_included": bool(include_urls),
        }
        if include_urls:
            result["raw"] = _json_safe(object_result, max_depth=4)
        return result

    def _sync_download_app_map_point_cloud(
        self,
        map_index: int,
        timeout: float,
        poll_interval: float,
        download_timeout: float,
        max_bytes: int,
        deadline: float | None = None,
        allow_stored: bool = False,
    ) -> DreameLawnMowerPointCloudDownload:
        map_index = _validate_point_cloud_map_index(map_index)
        timeout = _validate_positive_number(timeout, "generation timeout")
        poll_interval = _validate_positive_number(poll_interval, "poll interval")
        download_timeout = _validate_positive_number(
            download_timeout,
            "download timeout",
        )
        if (
            isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or max_bytes <= 0
        ):
            raise DreameLawnMowerPointCloudError(
                "Point-cloud maximum size must be a positive integer.",
                code="point_cloud_invalid_request",
                stage="request",
                retryable=False,
                public_message="The 3D map request contains an invalid size limit.",
            )

        deadline = time.monotonic() + timeout if deadline is None else deadline
        if time.monotonic() >= deadline:
            raise DreameLawnMowerPointCloudError(
                "Point-cloud generation timed out.",
                code="point_cloud_timeout",
                stage="generation",
                public_message=(
                    f"The mower did not finish the 3D map request within "
                    f"{timeout:g} seconds."
                ),
                timeout_seconds=timeout,
                retry_after_seconds=10,
            )
        cloud = self._sync_get_cloud_protocol(deadline=deadline)
        if not hasattr(cloud, "get_interim_file_url"):
            raise DreameLawnMowerPointCloudError(
                "The configured cloud protocol cannot download interim files.",
                code="point_cloud_download_unsupported",
                stage="download",
                retryable=False,
                public_message=(
                    "This Home Assistant connection cannot download the mower's "
                    "generated 3D map."
                ),
            )

        announcement_supported, stored_name, announcement_baseline = (
            self._sync_get_announced_point_cloud_object(
                cloud,
                requested_after_ms=0,
                fallback_reserve_seconds=min(15.0, timeout / 2),
                deadline=deadline,
            )
        )
        use_announcement_path = announcement_supported
        if allow_stored and stored_name is not None:
            stored_deadline = min(
                deadline,
                time.monotonic()
                + _POINT_CLOUD_STORED_DOWNLOAD_TIMEOUT_SECONDS,
            )
            try:
                content, content_type, _ = self._sync_download_point_cloud_object(
                    cloud,
                    stored_name,
                    deadline=stored_deadline,
                    download_timeout=min(
                        download_timeout,
                        _POINT_CLOUD_STORED_DOWNLOAD_TIMEOUT_SECONDS,
                    ),
                    max_bytes=max_bytes,
                )
                metadata = parse_pcd_metadata(
                    content,
                    max_bytes=max_bytes,
                    deadline=stored_deadline,
                )
            except (DeviceException, DreameLawnMowerPointCloudError):
                pass
            else:
                return DreameLawnMowerPointCloudDownload(
                    map_index=map_index,
                    content=content,
                    metadata=metadata,
                    content_type=content_type,
                )
        baseline_name = None
        if not use_announcement_path:
            baseline_result = self._sync_call_point_cloud_action(
                {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
                operation="read the existing point-cloud object state",
                deadline=deadline,
                require_data=True,
            )
            baseline_name = _point_cloud_object_name(
                baseline_result,
                map_index,
            )

        accepted_extensions = (
            _MOVA_POINT_CLOUD_OBJECT_EXTENSIONS
            if self._account_type == "mova"
            else _POINT_CLOUD_OBJECT_EXTENSIONS
        )
        baseline_identity = None
        baseline_extension = (
            _app_object_extension(baseline_name)
            if baseline_name is not None
            else None
        )
        fixed_mova_baseline = (
            self._account_type == "mova"
            and baseline_name is not None
            and baseline_extension is not None
            and baseline_extension.casefold() in accepted_extensions
        )
        if fixed_mova_baseline:
            try:
                _, _, baseline_identity = self._sync_download_point_cloud_object(
                    cloud,
                    baseline_name,
                    deadline=deadline,
                    download_timeout=download_timeout,
                    max_bytes=max_bytes,
                )
            except (DeviceException, DreameLawnMowerPointCloudError):
                baseline_identity = None

        generation_requested_at_ms = int(time.time() * 1000)
        self._sync_call_point_cloud_action(
            {"m": "a", "p": 0, "o": 10, "d": {"idx": map_index}},
            operation="start point-cloud generation",
            deadline=deadline,
            require_data=False,
        )

        observed_clear = baseline_name is None
        saw_unusable_point_cloud = False
        saw_stale_point_cloud = False
        saw_unverified_fixed_object = False
        rejected_object_names: set[str] = set()
        object_download_attempts: dict[str, int] = {}
        announcement_download_attempts: dict[tuple[str, int], int] = {}
        while time.monotonic() < deadline:
            announced_name = None
            announced_identity = None
            if use_announcement_path:
                _, announced_name, announced_identity = (
                    self._sync_get_announced_point_cloud_object(
                        cloud,
                        requested_after_ms=generation_requested_at_ms,
                        baseline=announcement_baseline,
                        deadline=deadline,
                    )
                )
            if announced_name is not None:
                attempt_key = announced_identity or (announced_name, 0)
                attempts = announcement_download_attempts.get(attempt_key, 0)
                if (
                    attempts
                    >= _POINT_CLOUD_ANNOUNCEMENT_MAX_DOWNLOAD_ATTEMPTS
                ):
                    saw_unusable_point_cloud = True
                    remaining = deadline - time.monotonic()
                    if remaining > 0:
                        time.sleep(min(poll_interval, remaining))
                    continue
                announcement_download_attempts[attempt_key] = attempts + 1
                try:
                    content, content_type, _ = (
                        self._sync_download_point_cloud_object(
                            cloud,
                            announced_name,
                            deadline=deadline,
                            download_timeout=download_timeout,
                            max_bytes=max_bytes,
                        )
                    )
                    metadata = parse_pcd_metadata(
                        content,
                        max_bytes=max_bytes,
                        deadline=deadline,
                    )
                except (DeviceException, DreameLawnMowerPointCloudError):
                    # The mower announces the object before upload progress
                    # reaches 100%, so the signer can briefly return no URL.
                    saw_unusable_point_cloud = True
                else:
                    return DreameLawnMowerPointCloudDownload(
                        map_index=map_index,
                        content=content,
                        metadata=metadata,
                        content_type=content_type,
                    )

                remaining = deadline - time.monotonic()
                if remaining > 0:
                    time.sleep(min(poll_interval, remaining))
                continue

            remaining = deadline - time.monotonic()
            if use_announcement_path:
                # A2-class firmware can take a few seconds to upload the LiDAR
                # object after accepting o:10. Avoid slow routed OBJ reads while
                # its dedicated announcement channel is active. Do not accept
                # an unverified legacy OBJ as fresh when no OBJ baseline exists.
                if remaining > 0:
                    time.sleep(min(poll_interval, remaining))
                continue

            object_result = self._sync_call_point_cloud_action(
                {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
                operation="read the generated point-cloud object state",
                deadline=deadline,
                require_data=True,
            )
            object_name = _point_cloud_object_name(
                object_result,
                map_index,
            )
            if object_name is None:
                observed_clear = True
            object_extension = (
                _app_object_extension(object_name) if object_name is not None else None
            )
            fixed_mova_object = (
                self._account_type == "mova"
                and object_name is not None
                and object_name == baseline_name
                and not observed_clear
            )
            object_ready = (
                object_name != baseline_name
                or observed_clear
                or fixed_mova_object
            )
            attempt_allowed = fixed_mova_object or (
                object_name not in rejected_object_names
                and object_download_attempts.get(object_name, 0) < 2
            )
            if (
                object_name is not None
                and object_extension is not None
                and object_extension.casefold() in accepted_extensions
                and object_ready
                and attempt_allowed
            ):
                if not fixed_mova_object:
                    object_download_attempts[object_name] = (
                        object_download_attempts.get(object_name, 0) + 1
                    )
                try:
                    content, content_type, object_identity = (
                        self._sync_download_point_cloud_object(
                            cloud,
                            object_name,
                            deadline=deadline,
                            download_timeout=download_timeout,
                            max_bytes=max_bytes,
                        )
                    )
                except (DeviceException, DreameLawnMowerPointCloudError):
                    saw_unusable_point_cloud = True
                else:
                    if fixed_mova_object and baseline_identity is None:
                        saw_unverified_fixed_object = True
                    elif (
                        fixed_mova_object
                        and not object_identity.differs_from(baseline_identity)
                    ):
                        saw_stale_point_cloud = True
                    else:
                        try:
                            metadata = parse_pcd_metadata(
                                content,
                                max_bytes=max_bytes,
                                deadline=deadline,
                            )
                        except DreameLawnMowerPointCloudError:
                            saw_unusable_point_cloud = True
                            if not fixed_mova_object:
                                rejected_object_names.add(object_name)
                        else:
                            return DreameLawnMowerPointCloudDownload(
                                map_index=map_index,
                                content=content,
                                metadata=metadata,
                                content_type=content_type,
                            )

            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(poll_interval, remaining))

        if saw_unverified_fixed_object:
            raise DreameLawnMowerPointCloudError(
                "The MOVA fixed point-cloud object could not be compared with "
                "its pre-generation version before the timeout.",
                code="point_cloud_download_invalid",
                stage="download_validation",
                public_message=(
                    "Home Assistant could not verify that the mower refreshed "
                    f"its 3D map within {timeout:g} seconds."
                ),
                timeout_seconds=timeout,
                retry_after_seconds=10,
            )

        if saw_unusable_point_cloud:
            raise DreameLawnMowerPointCloudError(
                "The mower published a point cloud, but it could not be downloaded "
                "and validated before the timeout.",
                code="point_cloud_download_invalid",
                stage="download_validation",
                public_message=(
                    "The mower published a 3D map, but Home Assistant could not "
                    f"download and validate it within {timeout:g} seconds."
                ),
                timeout_seconds=timeout,
                retry_after_seconds=10,
            )

        if saw_stale_point_cloud:
            raise DreameLawnMowerPointCloudError(
                "The mower's fixed point-cloud object did not refresh before "
                "the timeout.",
                code="point_cloud_not_published",
                stage="generation",
                public_message=(
                    f"The mower did not publish a fresh 3D map within {timeout:g} "
                    "seconds."
                ),
                timeout_seconds=timeout,
                retry_after_seconds=10,
            )

        raise DreameLawnMowerPointCloudError(
            "The mower did not publish or refresh a point cloud before the timeout.",
            code="point_cloud_not_published",
            stage="generation",
            public_message=(
                f"The mower did not publish a fresh 3D map within {timeout:g} seconds."
            ),
            timeout_seconds=timeout,
            retry_after_seconds=10,
        )

    def _sync_get_announced_point_cloud_object(
        self,
        cloud: Any,
        *,
        requested_after_ms: int,
        baseline: tuple[str, int] | None = None,
        fallback_reserve_seconds: float = 0,
        deadline: float,
    ) -> tuple[bool, str | None, tuple[str, int] | None]:
        """Return support state and a fresh cloud-property 99.20 LiDAR object."""
        remaining = deadline - time.monotonic()
        probe_budget = remaining - max(0.0, fallback_reserve_seconds)
        if probe_budget <= 0:
            return False, None, None
        get_properties = getattr(cloud, "get_properties", None)
        if not callable(get_properties):
            return False, None, None
        probe_timeout = min(
            probe_budget,
            _POINT_CLOUD_ANNOUNCEMENT_PROBE_TIMEOUT_SECONDS,
        )
        probe_deadline = min(
            deadline,
            time.monotonic() + probe_timeout,
        )
        try:
            payload = get_properties(
                _POINT_CLOUD_ANNOUNCEMENT_PROPERTY_KEY,
                retry_count=0,
                timeout=probe_timeout,
                deadline=probe_deadline,
            )
        except DeviceException:
            return False, None, None

        for entry in self._normalize_cloud_property_entries(payload):
            if entry.get("key") != _POINT_CLOUD_ANNOUNCEMENT_PROPERTY_KEY:
                continue
            object_name = entry.get("value")
            updated_at = entry.get("updateDate")
            if (
                not isinstance(object_name, str)
                or not object_name.strip()
                or isinstance(updated_at, bool)
                or not isinstance(updated_at, int | float | str)
            ):
                continue
            try:
                updated_at_ms = int(updated_at)
            except (TypeError, ValueError):
                continue
            extension = _app_object_extension(object_name)
            if (
                extension is None
                or extension.casefold()
                not in _POINT_CLOUD_ANNOUNCEMENT_EXTENSIONS
            ):
                continue
            normalized_name = object_name.strip()
            observed = (normalized_name, updated_at_ms)
            fresh = (
                (
                    normalized_name != baseline[0]
                    or updated_at_ms > baseline[1]
                )
                if baseline is not None
                else (
                    updated_at_ms
                    >= requested_after_ms
                    - _POINT_CLOUD_ANNOUNCEMENT_CLOCK_SKEW_MS
                )
            )
            return True, normalized_name if fresh else None, observed
        return False, None, None

    def _sync_download_point_cloud_object(
        self,
        cloud: Any,
        object_name: str,
        *,
        deadline: float,
        download_timeout: float,
        max_bytes: int,
    ) -> tuple[bytes, str, _PointCloudObjectIdentity]:
        raw_url = self._sync_get_point_cloud_download_url(
            cloud,
            object_name,
            deadline=deadline,
        )
        url = _point_cloud_download_url(raw_url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DreameLawnMowerPointCloudError(
                "Point-cloud generation timed out.",
                code="point_cloud_timeout",
                stage="download",
                public_message="The mower did not finish the 3D map request in time.",
                timeout_seconds=download_timeout,
                retry_after_seconds=10,
            )
        return _download_point_cloud_content_with_identity(
            url,
            timeout=min(download_timeout, remaining),
            max_bytes=max_bytes,
        )

    def _sync_call_point_cloud_action(
        self,
        payload: Mapping[str, Any],
        *,
        operation: str,
        deadline: float,
        require_data: bool,
    ) -> Any:
        """Call one point-cloud action within the shared generation deadline."""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DreameLawnMowerPointCloudError(
                "Point-cloud generation timed out.",
                code="point_cloud_timeout",
                stage="mower_request",
                public_message="The mower did not finish the 3D map request in time.",
                retry_after_seconds=10,
            )
        try:
            response = self._sync_call_app_action(
                payload,
                retry_count=0,
                timeout=remaining,
                deadline=deadline,
                redact_response=True,
            )
        except DreameLawnMowerConnectionError as err:
            if time.monotonic() >= deadline:
                raise DreameLawnMowerPointCloudError(
                    "Point-cloud generation timed out.",
                    code="point_cloud_timeout",
                    stage="mower_request",
                    public_message=(
                        "The mower did not finish the 3D map request in time."
                    ),
                    retry_after_seconds=10,
                ) from err
            raise DreameLawnMowerPointCloudError(
                f"The mower could not {operation}.",
                code="point_cloud_mower_request_failed",
                stage="mower_request",
                public_message=(
                    "The mower rejected or could not complete the 3D map request."
                ),
                retry_after_seconds=10,
            ) from err
        if time.monotonic() >= deadline:
            raise DreameLawnMowerPointCloudError(
                "Point-cloud generation timed out.",
                code="point_cloud_timeout",
                stage="mower_request",
                public_message="The mower did not finish the 3D map request in time.",
                retry_after_seconds=10,
            )
        try:
            return _point_cloud_action_data(
                response,
                operation,
                require_data=require_data,
            )
        except DreameLawnMowerPointCloudError as err:
            raise DreameLawnMowerPointCloudError(
                str(err),
                code="point_cloud_mower_response_invalid",
                stage="mower_response",
                public_message=(
                    "The mower returned an invalid response for the 3D map request."
                ),
                retry_after_seconds=10,
            ) from err

    def _sync_get_point_cloud_download_url(
        self,
        cloud: Any,
        object_name: str,
        *,
        deadline: float,
    ) -> str:
        """Resolve a signed point-cloud URL within the generation deadline."""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DreameLawnMowerPointCloudError("Point-cloud generation timed out.")
        raw_url = cloud.get_interim_file_url(
            object_name,
            retry_count=0,
            timeout=remaining,
            deadline=deadline,
        )
        if time.monotonic() >= deadline:
            raise DreameLawnMowerPointCloudError("Point-cloud generation timed out.")
        return raw_url

    def _sync_get_app_map_text(
        self,
        *,
        size: int,
        chunk_size: int,
    ) -> tuple[str, int, int]:
        chunks = bytearray()
        offset = 0
        chunk_count = 0
        while offset < size:
            requested_size = min(size - offset, chunk_size)
            chunk_result = self._sync_call_app_action(
                {
                    "m": "g",
                    "t": "MAPD",
                    "d": {"start": offset, "size": requested_size},
                }
            )
            data = _app_action_data(chunk_result)
            if not isinstance(data, Mapping):
                raise DreameLawnMowerConnectionError(
                    f"MAPD returned invalid chunk at offset {offset}."
                )
            text = data.get("data")
            returned_size = data.get("size")
            if not isinstance(text, str) or text == "":
                raise DreameLawnMowerConnectionError(
                    f"MAPD returned empty data at offset {offset}."
                )
            chunk_bytes = text.encode("utf-8")
            actual_size = len(chunk_bytes)
            if actual_size > requested_size:
                raise DreameLawnMowerConnectionError(
                    f"MAPD returned too much data at offset {offset}."
                )
            chunks.extend(chunk_bytes)
            offset += (
                returned_size
                if isinstance(returned_size, int) and returned_size > 0
                else actual_size
            )
            chunk_count += 1
        return chunks.decode("utf-8"), chunk_count, offset

    def _sync_call_app_action(
        self,
        payload: Mapping[str, Any],
        *,
        siid: int = 2,
        aiid: int = 50,
        retry_count: int | None = None,
        timeout: float | None = None,
        deadline: float | None = None,
        redact_response: bool = False,
    ) -> Any:
        cloud = (
            self._sync_get_cloud_protocol(deadline=deadline)
            if deadline is not None
            else self._sync_get_cloud_protocol()
        )
        if not getattr(cloud, "_host", None):
            try:
                preflight_options: dict[str, Any] = {}
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise DreameLawnMowerConnectionError(
                            "Point-cloud cloud setup timed out."
                        )
                    preflight_options = {
                        "retry_count": 0,
                        "timeout": remaining,
                        "deadline": deadline,
                    }
                if hasattr(cloud, "get_device_info_v2"):
                    cloud.get_device_info_v2("en", **preflight_options)
                elif hasattr(cloud, "get_device_info"):
                    cloud.get_device_info(**preflight_options)
            except DeviceException as err:
                raise DreameLawnMowerConnectionError(str(err)) from err
        try:
            request_options: dict[str, Any] = {}
            if retry_count is not None:
                request_options["retry_count"] = retry_count
            if timeout is not None:
                request_options["timeout"] = timeout
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DreameLawnMowerConnectionError(
                        "Point-cloud cloud request timed out."
                    )
                request_options["timeout"] = (
                    min(timeout, remaining) if timeout is not None else remaining
                )
                request_options["deadline"] = deadline
            if redact_response:
                request_options["redact_response"] = True
            if hasattr(cloud, "call_app_action"):
                response = cloud.call_app_action(
                    payload,
                    siid=siid,
                    aiid=aiid,
                    **request_options,
                )
            else:
                response = cloud.send(
                    "action",
                    {
                        "did": str(cloud.device_id),
                        "siid": siid,
                        "aiid": aiid,
                        "in": [payload],
                    },
                    **request_options,
                )
        except DeviceException as err:
            raise DreameLawnMowerConnectionError(str(err)) from err

        out = response.get("out") if isinstance(response, Mapping) else None
        if isinstance(out, Sequence) and not isinstance(out, str | bytes | bytearray):
            return out[0] if out else None
        return response

    def _sync_get_cloud_properties(
        self,
        keys: str | Sequence[str],
    ) -> Any:
        cloud = self._sync_get_cloud_protocol()
        normalized_keys = self._normalize_cloud_property_keys(keys)
        try:
            return cloud.get_properties(normalized_keys)
        except DeviceException as err:
            raise DreameLawnMowerConnectionError(str(err)) from err

    def _sync_get_cloud_property_history(
        self,
        key: str,
        *,
        limit: int = 3,
        time_start: int = 0,
    ) -> Any:
        cloud = self._sync_get_cloud_protocol()
        try:
            return cloud.get_device_property(
                key,
                limit=limit,
                time_start=time_start,
            )
        except DeviceException as err:
            raise DreameLawnMowerConnectionError(str(err)) from err

    def _sync_scan_cloud_properties(
        self,
        keys: str | Sequence[str] | None,
        siids: Sequence[int] | None,
        piid_start: int,
        piid_end: int,
        chunk_size: int,
        language: str,
        only_values: bool,
        include_key_definition: bool = True,
        key_definition: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_keys = self._build_cloud_property_keys(
            keys=keys,
            siids=siids,
            piid_start=piid_start,
            piid_end=piid_end,
        )
        if not normalized_keys:
            result = {
                "requested_key_count": 0,
                "returned_entry_count": 0,
                "displayed_entry_count": 0,
                "entries": [],
            }
            result["summary"] = build_cloud_property_summary(result)
            return result

        all_entries: list[dict[str, Any]] = []
        for offset in range(0, len(normalized_keys), max(chunk_size, 1)):
            chunk = normalized_keys[offset : offset + max(chunk_size, 1)]
            response = self._sync_get_cloud_properties(chunk)
            all_entries.extend(self._normalize_cloud_property_entries(response))

        cloud_key_definition = key_definition
        if include_key_definition and cloud_key_definition is None:
            try:
                cloud_key_definition = self._sync_get_cloud_key_definition(language)
            except DreameLawnMowerConnectionError:
                cloud_key_definition = None

        rendered = all_entries
        if only_values:
            rendered = [
                entry for entry in rendered if self._entry_has_meaningful_value(entry)
            ]

        rendered = [
            self._annotate_cloud_property_entry(
                entry,
                language=language,
                key_definition=cloud_key_definition,
                model=self._descriptor.model,
            )
            for entry in sorted(
                rendered,
                key=lambda item: str(item.get("key", "")),
            )
        ]
        result = {
            "requested_key_count": len(normalized_keys),
            "returned_entry_count": len(all_entries),
            "displayed_entry_count": len(rendered),
            "entries": rendered,
        }
        result["summary"] = build_cloud_property_summary(result)
        return result

    def _sync_get_cloud_device_list_page(
        self,
        current: int,
        size: int,
        language: str | None,
        master: bool | None,
        shared_status: int | None,
    ) -> dict[str, Any] | None:
        cloud = self._sync_get_cloud_protocol()
        try:
            return cloud.get_device_list_v2(
                current=current,
                size=size,
                lang=language,
                master=master,
                shared_status=shared_status,
            )
        except DeviceException as err:
            raise DreameLawnMowerConnectionError(str(err)) from err

    def _sync_get_cloud_key_definition(
        self,
        language: str | None = None,
        device_info: Mapping[str, Any] | None = None,
        device_list_page: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        cloud = self._sync_get_cloud_protocol()
        device_info = device_info or self._sync_get_cloud_device_info(language) or {}
        key_define = _key_define_from_mapping(device_info)
        source = "device_info"
        if not key_define.get("url"):
            if device_list_page is None:
                try:
                    device_list_page = self._sync_get_cloud_device_list_page(
                        current=1,
                        size=20,
                        language=language,
                        master=None,
                        shared_status=None,
                    )
                except DreameLawnMowerConnectionError:
                    device_list_page = None
            list_key_define = _key_define_from_device_list_page(
                self._descriptor.did,
                device_list_page,
            )
            if list_key_define.get("url"):
                key_define = list_key_define
                source = "device_list_v2"
        url = key_define.get("url") if isinstance(key_define, Mapping) else None
        result: dict[str, Any] = {
            "url": url,
            "url_present": bool(url),
            "ver": key_define.get("ver") if isinstance(key_define, Mapping) else None,
            "source": source if url else None,
            "fetched": False,
            "payload": None,
            "error": None,
        }
        if not url:
            result["error"] = "key_define_url_missing"
            return result

        try:
            content = cloud.get_file(str(url), retry_count=1)
        except DeviceException as err:
            raise DreameLawnMowerConnectionError(str(err)) from err
        except Exception as err:
            result["error"] = str(err)
            return result

        if not content:
            result["error"] = "key_definition_fetch_failed"
            return result

        try:
            text = (
                content.decode("utf-8") if isinstance(content, bytes) else str(content)
            )
            result["payload"] = json.loads(text)
            result["fetched"] = True
        except (UnicodeDecodeError, json.JSONDecodeError) as err:
            result["error"] = f"key_definition_parse_failed: {err}"
        return result

    def _sync_probe_map_sources(
        self,
        timeout: float,
        interval: float,
        language: str,
    ) -> dict[str, Any]:
        selected_map_view = self._sync_refresh_map_view(timeout, interval)
        cloud_device_info = self._sync_get_cloud_device_info(language)
        cloud_device_list_page = self._sync_get_cloud_device_list_page(
            current=1,
            size=20,
            language=language,
            master=None,
            shared_status=None,
        )
        try:
            cloud_key_definition = self._sync_get_cloud_key_definition(
                language,
                cloud_device_info,
                cloud_device_list_page,
            )
        except DreameLawnMowerConnectionError as err:
            cloud_key_definition = {"error": str(err)}
        cloud_properties = self._sync_scan_cloud_properties(
            keys=MAP_PROBE_PROPERTY_KEYS,
            siids=None,
            piid_start=1,
            piid_end=1,
            chunk_size=50,
            language=language,
            only_values=False,
            include_key_definition=False,
            key_definition=(
                cloud_key_definition
                if isinstance(cloud_key_definition, Mapping)
                else None
            ),
        )
        cloud_property_history: dict[str, Any] = {}
        for key in MAP_HISTORY_PROPERTY_KEYS:
            try:
                cloud_property_history[key] = self._sync_get_cloud_property_history(
                    key,
                    limit=3,
                    time_start=0,
                )
            except DreameLawnMowerConnectionError as err:
                cloud_property_history[key] = {"error": str(err)}
        try:
            cloud_user_features = self._sync_get_cloud_user_features(language)
        except DreameLawnMowerConnectionError as err:
            cloud_user_features = {"error": str(err)}
        try:
            cloud_device_otc_info = self._sync_get_cloud_device_otc_info(language)
        except DreameLawnMowerConnectionError as err:
            cloud_device_otc_info = {"error": str(err)}
        try:
            app_maps = self._sync_get_app_maps(
                chunk_size=400,
                include_payload=False,
                include_objects=True,
                include_object_urls=False,
            )
        except DreameLawnMowerConnectionError as err:
            app_maps = {"error": str(err)}
        legacy_map_view = self._sync_refresh_legacy_map_view(timeout, interval)
        vector_map_view = self._sync_refresh_vector_map_view()

        return build_map_probe_payload(
            descriptor=self._descriptor,
            map_view=self._map_view_with_cloud_summary(
                selected_map_view, cloud_properties
            ),
            legacy_map_view=self._map_view_with_cloud_summary(
                legacy_map_view, cloud_properties
            ),
            vector_map_view=self._map_view_with_cloud_summary(
                vector_map_view, cloud_properties
            ),
            cloud_properties=cloud_properties,
            cloud_device_info=cloud_device_info,
            cloud_device_list_page=cloud_device_list_page,
            cloud_property_history=cloud_property_history,
            cloud_user_features=cloud_user_features,
            cloud_device_otc_info=cloud_device_otc_info,
            cloud_key_definition=cloud_key_definition,
            app_maps=app_maps,
        )

    def _safe_map_diagnostics(
        self,
        *,
        source: str,
        reason: str | None = None,
        cloud_property_summary: Mapping[str, Any] | None = None,
    ):
        try:
            device = self._ensure_device()
            return map_diagnostics_from_device(
                device,
                source=source,
                reason=reason,
                cloud_property_summary=cloud_property_summary,
            )
        except Exception:
            return None

    def _map_view_with_cloud_summary(
        self,
        map_view: DreameLawnMowerMapView,
        cloud_properties: Mapping[str, Any] | None,
    ) -> DreameLawnMowerMapView:
        from .map_probe import build_cloud_property_summary

        diagnostics = self._safe_map_diagnostics(
            source=map_view.source,
            reason=(
                map_view.diagnostics.reason
                if map_view.diagnostics is not None
                else map_view.error
            ),
            cloud_property_summary=build_cloud_property_summary(cloud_properties),
        )
        return DreameLawnMowerMapView(
            source=map_view.source,
            summary=map_view.summary,
            image_png=map_view.image_png,
            error=map_view.error,
            diagnostics=diagnostics or map_view.diagnostics,
            app_maps=map_view.app_maps,
        )

    def _sync_wait_for_map(self, timeout: float, interval: float):
        device = self._sync_update_device()
        if getattr(device, "current_map", None) is not None:
            return device.current_map

        if getattr(device, "_map_manager", None) is None:
            return None

        try:
            device.update_map()
        except DeviceException as err:
            raise DreameLawnMowerConnectionError(str(err)) from err

        deadline = time.monotonic() + max(timeout, 0)
        while time.monotonic() <= deadline:
            current_map = getattr(device, "current_map", None)
            if current_map is not None:
                return current_map
            time.sleep(max(interval, 0.1))

        return getattr(device, "current_map", None)

    @staticmethod
    def _normalize_cloud_property_keys(keys: str | Sequence[str]) -> str:
        if isinstance(keys, str):
            return keys
        return ",".join(str(key).strip() for key in keys if str(key).strip())

    @staticmethod
    def _build_cloud_property_keys(
        *,
        keys: str | Sequence[str] | None,
        siids: Sequence[int] | None,
        piid_start: int,
        piid_end: int,
    ) -> list[str]:
        if keys is not None:
            if isinstance(keys, str):
                return [item.strip() for item in keys.split(",") if item.strip()]
            return [str(item).strip() for item in keys if str(item).strip()]

        if piid_end < piid_start:
            raise ValueError("piid_end must be greater than or equal to piid_start")

        normalized_siids = list(siids) if siids is not None else list(range(1, 9))
        return [
            f"{siid}.{piid}"
            for siid in normalized_siids
            for piid in range(piid_start, piid_end + 1)
        ]

    @staticmethod
    def _normalize_cloud_property_entries(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]

        if isinstance(payload, dict):
            for key in ("data", "result", "records", "list"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def _coerce_property_bool(value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "on", "yes"}:
                return True
            if normalized in {"false", "0", "off", "no"}:
                return False
        return None

    @staticmethod
    def _entry_has_meaningful_value(entry: dict[str, Any]) -> bool:
        value = entry.get("value")
        if value not in (None, "", [], {}):
            return True

        for nested_key in ("values", "data", "raw", "content"):
            nested = entry.get(nested_key)
            if nested not in (None, "", [], {}):
                return True
        return False

    @staticmethod
    def _property_value_blob_preview(value: Any) -> tuple[int, str] | None:
        raw = value
        if isinstance(raw, str):
            text = raw.strip()
            if not (text.startswith("[") and text.endswith("]")):
                return None
            try:
                raw = json.loads(text)
            except json.JSONDecodeError:
                return None

        if not isinstance(raw, list) or not raw:
            return None
        if not all(isinstance(item, int) and 0 <= item <= 255 for item in raw):
            return None

        blob = bytes(raw)
        return len(blob), blob.hex()

    @classmethod
    def _annotate_cloud_property_entry(
        cls,
        entry: dict[str, Any],
        *,
        language: str,
        key_definition: Mapping[str, Any] | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        rendered = dict(entry)
        key = str(rendered.get("key", ""))
        value = rendered.get("value")
        property_hint = MOWER_PROPERTY_HINTS.get(key)
        if property_hint:
            rendered["property_hint"] = property_hint

        label = key_definition_label(
            key_definition,
            key,
            value,
            language=language,
        )
        if label:
            rendered["decoded_label"] = label
            rendered["decoded_label_source"] = "cloud_key_definition"

        if key == MOWER_STATE_PROPERTY_KEY:
            state_key = mower_state_key(value)
            if state_key:
                rendered["state_key"] = state_key
            if not rendered.get("decoded_label"):
                label = mower_state_label(value, language=language)
                if label:
                    rendered["decoded_label"] = label
                    rendered["decoded_label_source"] = "bundled_mower_protocol"
        elif key == MOWER_ERROR_PROPERTY_KEY and not rendered.get("decoded_label"):
            label = mower_error_label(value, model=model)
            if label:
                rendered["decoded_label"] = label
                rendered["decoded_label_source"] = "bundled_mower_errors"
        elif key in {MOWER_RAW_STATUS_PROPERTY_KEY, MOWER_RUNTIME_STATUS_PROPERTY_KEY}:
            status_blob = decode_mower_status_blob(value)
            if status_blob is not None:
                status_blob = replace(
                    status_blob,
                    received_at=_property_entry_received_at(rendered),
                )
                rendered["status_blob"] = status_blob.as_dict()
        elif key == MOWER_TASK_PROPERTY_KEY:
            task_status = decode_mower_task_status(value)
            if task_status is not None:
                rendered["task_status"] = task_status

        blob_preview = cls._property_value_blob_preview(value)
        if blob_preview is not None:
            blob_len, blob_hex = blob_preview
            rendered["value_bytes_len"] = blob_len
            rendered["value_bytes_hex"] = blob_hex

        return rendered
