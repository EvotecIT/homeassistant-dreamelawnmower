"""Reusable client map, point-cloud, and cloud-property operations."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from threading import Lock
from typing import TYPE_CHECKING, Any

from requests.exceptions import Timeout as RequestsTimeout

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
from .client_app_map_view import app_map_view, preferred_map_view
from .client_app_maps import _DreameLawnMowerClientAppMapsMixin
from .client_map_helpers import (
    _current_app_map_index,
    _key_define_from_device_list_page,
    _key_define_from_mapping,
    _map_view_current_app_map_index,
    _PointCloudObjectIdentity,
)
from .client_mowing_map import _DreameLawnMowerClientMowingMapMixin
from .client_property_scan import cloud_property_scan_result
from .client_shared_helpers import (
    _property_entry_received_at,
)
from .client_vector_map_view import vector_map_details, vector_map_view
from .exceptions import (
    DeviceException,
    DreameLawnMowerCloudAPIError,
    DreameLawnMowerCommandRejectedError,
    DreameLawnMowerConnectionError,
)
from .exceptions import (
    DreameLawnMowerError as DreameLawnMowerError,
)
from .map_objects import (
    map_object_description,
    map_object_names,
    map_objects_result,
    normalized_object_names,
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
)
from .point_cloud_diagnostics import (
    value_shape,
)
from .point_cloud_trace import record_point_cloud_stage

if TYPE_CHECKING:
    from .map_visuals import MapRenderStyle

from .point_cloud_policy import (
    _POINT_CLOUD_ANNOUNCEMENT_PROBE_TIMEOUT_SECONDS,
    _POINT_CLOUD_ANNOUNCEMENT_PROPERTY_KEY,
)


def _app_map_inventory_identity(
    maps: Sequence[Mapping[str, Any]],
) -> str | None:
    """Return a stable identity only when every created map has MAPI data."""
    if not maps:
        return None
    inventory: list[dict[str, Any]] = []
    for item in maps:
        created = bool(item.get("created"))
        info = item.get("info")
        map_hash = info.get("hash") if isinstance(info, Mapping) else None
        map_size = info.get("size") if isinstance(info, Mapping) else None
        if created and (
            not isinstance(map_hash, str)
            or not map_hash
            or isinstance(map_size, bool)
            or not isinstance(map_size, int)
            or map_size <= 0
        ):
            return None
        inventory.append(
            {
                "idx": _json_safe(item.get("idx"), max_depth=1),
                "current": bool(item.get("current")),
                "created": created,
                "hash": map_hash if created else None,
                "size": map_size if created else None,
            }
        )
    return json.dumps(
        inventory,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


class _DreameLawnMowerClientMapsMixin(
    _DreameLawnMowerClientAppMapsMixin, _DreameLawnMowerClientMowingMapMixin
):
    _app_map_object_cache_lock: Lock
    _latest_app_map_inventory_identity: str | None
    _latest_app_map_object_inventory_identity: str | None
    _latest_app_map_object_names: tuple[str | None, ...]

    def _sync_get_current_app_map_index_readback(self) -> int | None:
        """Read only MAPL and return its unambiguous current map index."""
        try:
            map_list_result = self._sync_call_app_action({"m": "g", "t": "MAPL"})
        except DeviceException as err:
            raise DreameLawnMowerConnectionError(str(err)) from err
        return _current_app_map_index(map_list_result)

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
        except DeviceException as err:
            raise DreameLawnMowerConnectionError(str(err)) from err
        except MowingTaskResponseError as err:
            error_type = (
                DreameLawnMowerCommandRejectedError
                if isinstance(response, Mapping)
                else DreameLawnMowerConnectionError
            )
            raise error_type(str(err)) from err

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

        return vector_map_details(batch_data, self._sync_get_current_app_map_index())

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
        preferred = preferred_map_view(self, app_view, vector_view)
        if preferred is not None:
            return preferred

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

        return vector_map_view(
            self, batch_data, label_scale=label_scale, style=style,
            current_map_index=(current_map_index if current_map_index is not None
                               else self._sync_get_current_app_map_index()),
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
            return app_map_view(
                self, app_maps, legacy_error=legacy_error, legacy_reason=legacy_reason,
                label_scale=label_scale, style=style,
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

    def _sync_update_app_map_inventory_identity(
        self,
        maps: Sequence[Mapping[str, Any]],
    ) -> None:
        """Invalidate private object names when the owning map changes."""
        identity = _app_map_inventory_identity(maps)
        with self._app_map_object_cache_lock:
            self._set_app_map_inventory_identity(identity)

    def _set_app_map_inventory_identity(self, identity: str | None) -> None:
        """Apply inventory identity while the caller owns the object-cache lock."""
        if identity != self._latest_app_map_inventory_identity:
            self._latest_app_map_object_names = ()
            self._latest_app_map_object_inventory_identity = None
        self._latest_app_map_inventory_identity = identity

    def _sync_get_app_map_objects(
        self,
        include_urls: bool = False,
    ) -> dict[str, Any]:
        with self._app_map_object_cache_lock:
            inventory_identity = self._latest_app_map_inventory_identity
        object_result = self._sync_call_app_action(
            {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
            redact_response=True,
        )
        names = map_object_names(object_result)
        normalized_names = normalized_object_names(names)
        with self._app_map_object_cache_lock:
            if (
                inventory_identity is not None
                and inventory_identity == self._latest_app_map_inventory_identity
            ):
                self._latest_app_map_object_names = normalized_names
                self._latest_app_map_object_inventory_identity = inventory_identity

        objects: list[dict[str, Any]] = []
        cloud = self._sync_get_cloud_protocol() if include_urls else None
        for raw_name in names:
            name = str(raw_name)
            item = map_object_description(raw_name, include_urls=include_urls)
            if include_urls:
                try:
                    item["url_checked"] = (
                        cloud is not None and hasattr(cloud, "get_interim_file_url")
                    )
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

        return map_objects_result(object_result, objects, include_urls=include_urls)

    def _sync_download_app_map_point_cloud(
        self,
        map_index: int,
        timeout: float,
        poll_interval: float,
        download_timeout: float,
        max_bytes: int,
        deadline: float | None = None,
        allow_stored: bool = False,
        allow_unscoped_stored: bool | None = None,
    ) -> DreameLawnMowerPointCloudDownload:
        from .client_point_cloud_transport import run_sync_point_cloud
        from .point_cloud_generation_plan import point_cloud_generation

        return run_sync_point_cloud(self, point_cloud_generation(
            self._account_type, str(self._descriptor.model), map_index, timeout,
            poll_interval, download_timeout, max_bytes, deadline,
            allow_stored, allow_unscoped_stored,
        ), setup=self._sync_get_cloud_protocol)

    def _sync_try_download_stored_point_cloud(
        self,
        cloud: Any,
        object_name: str,
        *,
        map_index: int,
        deadline: float,
        download_timeout: float,
        max_bytes: int,
        observation: dict[str, Any] | None = None,
    ) -> DreameLawnMowerPointCloudDownload | None:
        from .client_point_cloud_transport import run_sync_point_cloud
        from .point_cloud_object_plan import stored_object

        return run_sync_point_cloud(
            self,
            stored_object(
                object_name=object_name,
                map_index=map_index,
                deadline=deadline,
                download_timeout=download_timeout,
                max_bytes=max_bytes,
                observation=observation,
            ),
            setup=self._sync_get_cloud_protocol,
            cloud=cloud,
        )

    def _sync_get_announced_point_cloud_object(
        self,
        cloud: Any,
        *,
        requested_after_ms: int,
        baseline: tuple[str, int] | None = None,
        require_post_request: bool = False,
        fallback_reserve_seconds: float = 0,
        deadline: float,
        observation: dict[str, Any] | None = None,
    ) -> tuple[bool | None, str | None, tuple[str, int] | None]:
        """Return capability state and a fresh cloud-property 99.20 object.

        A ``None`` capability means the probe was inconclusive, so callers may
        retry it without treating the firmware as unsupported.
        """
        observation = {} if observation is None else observation
        observation["status"] = "budget_exhausted"
        record_point_cloud_stage("announcement_read")
        remaining = deadline - time.monotonic()
        probe_budget = remaining - max(0.0, fallback_reserve_seconds)
        if probe_budget <= 0:
            return None, None, None
        get_properties = getattr(cloud, "get_properties", None)
        if not callable(get_properties):
            observation["status"] = "unavailable"
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
        except (DeviceException, RequestsTimeout, json.JSONDecodeError):
            observation["status"] = "transport_error"
            return None, None, None
        if payload is None:
            observation["status"] = "no_response"
            return None, None, None

        entries = self._normalize_cloud_property_entries(payload)
        from .point_cloud_announcement import announcement_result

        return announcement_result(
            entries,
            payload_shape=value_shape(payload),
            requested_after_ms=requested_after_ms,
            baseline=baseline,
            require_post_request=require_post_request,
            observation=observation,
        )

    def _sync_probe_point_cloud_object_identity(
        self,
        cloud: Any,
        object_name: str,
        *,
        deadline: float,
        download_timeout: float,
        max_bytes: int,
    ) -> tuple[bool, _PointCloudObjectIdentity | None]:
        from .client_point_cloud_transport import run_sync_point_cloud
        from .point_cloud_object_plan import object_identity

        return run_sync_point_cloud(
            self,
            object_identity(
                object_name=object_name,
                deadline=deadline,
                download_timeout=download_timeout,
                max_bytes=max_bytes,
            ),
            setup=self._sync_get_cloud_protocol,
            cloud=cloud,
        )

    def _sync_download_point_cloud_object(
        self,
        cloud: Any,
        object_name: str,
        *,
        deadline: float,
        download_timeout: float,
        max_bytes: int,
        observation: dict[str, Any] | None = None,
    ) -> tuple[bytes, str, _PointCloudObjectIdentity]:
        from .client_point_cloud_transport import run_sync_point_cloud
        from .point_cloud_object_plan import download_object

        return run_sync_point_cloud(
            self,
            download_object(
                object_name=object_name,
                deadline=deadline,
                download_timeout=download_timeout,
                max_bytes=max_bytes,
                observation=observation,
            ),
            setup=self._sync_get_cloud_protocol,
            cloud=cloud,
        )

    def _sync_call_point_cloud_action(
        self,
        payload: Mapping[str, Any],
        *,
        operation: str,
        deadline: float,
        require_data: bool,
        on_dispatch: Callable[[], None] | None = None,
    ) -> Any:
        from .client_point_cloud_transport import run_sync_point_cloud
        from .point_cloud_object_plan import mower_action

        return run_sync_point_cloud(
            self,
            mower_action(
                payload=payload,
                operation=operation,
                deadline=deadline,
                require_data=require_data,
                on_dispatch=on_dispatch,
            ),
            setup=self._sync_get_cloud_protocol,
        )

    def _sync_get_point_cloud_download_url(
        self,
        cloud: Any,
        object_name: str,
        *,
        deadline: float,
        require_response: bool = False,
    ) -> Any:
        from .client_point_cloud_transport import run_sync_point_cloud
        from .point_cloud_object_plan import resolve_object_url

        return run_sync_point_cloud(
            self,
            resolve_object_url(
                object_name=object_name,
                deadline=deadline,
                require_response=require_response,
            ),
            setup=self._sync_get_cloud_protocol,
            cloud=cloud,
        )

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
        on_dispatch: Callable[[], None] | None = None,
        raise_on_api_error: bool = False,
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
            if on_dispatch is not None:
                request_options["on_dispatch"] = on_dispatch
            if raise_on_api_error:
                request_options["raise_on_api_error"] = True
            if hasattr(cloud, "call_app_action"):
                response = cloud.call_app_action(
                    payload,
                    siid=siid,
                    aiid=aiid,
                    **request_options,
                )
            else:
                request_options.setdefault(
                    "retry_count",
                    2 if payload.get("m") == "g" else 0,
                )
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
        except DreameLawnMowerCloudAPIError:
            raise
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

        return cloud_property_scan_result(
            self, len(normalized_keys), all_entries, language=language,
            only_values=only_values, key_definition=cloud_key_definition,
        )

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
            state_key = mower_state_key(value, model=model)
            if state_key:
                rendered["state_key"] = state_key
            if not rendered.get("decoded_label"):
                label = mower_state_label(value, language=language, model=model)
                if label:
                    rendered["decoded_label"] = label
                    rendered["decoded_label_source"] = "bundled_mower_protocol"
        elif key == MOWER_ERROR_PROPERTY_KEY and not rendered.get("decoded_label"):
            label = mower_error_label(value, model=model)
            if label:
                rendered["decoded_label"] = label
                rendered["decoded_label_source"] = "bundled_mower_errors"
        elif key in {MOWER_RAW_STATUS_PROPERTY_KEY, MOWER_RUNTIME_STATUS_PROPERTY_KEY}:
            status_blob = decode_mower_status_blob(value, property_key=key)
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
