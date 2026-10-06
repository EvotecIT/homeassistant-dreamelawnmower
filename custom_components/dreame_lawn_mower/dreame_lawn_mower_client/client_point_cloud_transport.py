"""Synchronous transport for the shared point-cloud generation plan."""

from __future__ import annotations

import time
from collections.abc import Generator
from typing import TYPE_CHECKING, Any, Protocol, assert_never

from .client_map_helpers import _download_point_cloud_content_with_identity
from .point_cloud import (
    DreameLawnMowerPointCloudError,
    parse_pcd_metadata,
)
from .point_cloud_transport import (
    CachedObjectNames,
    CloudSetup,
    DownloadFile,
    DownloadObject,
    MowerAction,
    ObjectIdentity,
    ParseMetadata,
    PointCloudRequest,
    RawAppAction,
    ReadAnnouncement,
    ResolveObjectURL,
    SignObject,
    StoredObject,
    WaitForObject,
)

if TYPE_CHECKING:
    from .client_maps import _DreameLawnMowerClientMapsMixin


class CloudSetupDispatch(Protocol):
    def __call__(self, *, deadline: float) -> Any: ...


def run_sync_point_cloud[T](
    client: _DreameLawnMowerClientMapsMixin,
    plan: Generator[PointCloudRequest, Any, T],
    *,
    setup: CloudSetupDispatch,
    cloud: Any = None,
) -> T:
    """Retain existing synchronous hooks while executing transport-free policy."""

    def dispatch(request: PointCloudRequest) -> Any:
        nonlocal cloud
        match request:
            case CachedObjectNames():
                with client._app_map_object_cache_lock:
                    return (
                        client._latest_app_map_object_names
                        if client._latest_app_map_object_inventory_identity is not None
                        and client._latest_app_map_object_inventory_identity
                        == client._latest_app_map_inventory_identity
                        else ()
                    )
            case CloudSetup():
                cloud = setup(deadline=request.deadline)
                if not hasattr(cloud, "get_interim_file_url"):
                    raise DreameLawnMowerPointCloudError(
                        "The configured cloud protocol cannot download interim files.",
                        code="point_cloud_download_unsupported",
                        stage="download",
                        retryable=False,
                        public_message=(
                            "This Home Assistant connection cannot download "
                            "the mower's "
                            "generated 3D map."
                        ),
                    )
                return None
            case StoredObject():
                return client._sync_try_download_stored_point_cloud(
                    cloud,
                    request.object_name,
                    map_index=request.map_index,
                    deadline=request.deadline,
                    download_timeout=request.download_timeout,
                    max_bytes=request.max_bytes,
                    observation=request.observation,
                )
            case DownloadObject():
                return client._sync_download_point_cloud_object(
                    cloud,
                    request.object_name,
                    deadline=request.deadline,
                    download_timeout=request.download_timeout,
                    max_bytes=request.max_bytes,
                    observation=request.observation,
                )
            case ObjectIdentity():
                return client._sync_probe_point_cloud_object_identity(
                    cloud,
                    request.object_name,
                    deadline=request.deadline,
                    download_timeout=request.download_timeout,
                    max_bytes=request.max_bytes,
                )
            case ReadAnnouncement():
                return client._sync_get_announced_point_cloud_object(
                    cloud,
                    requested_after_ms=request.requested_after_ms,
                    baseline=request.baseline,
                    require_post_request=request.require_post_request,
                    fallback_reserve_seconds=request.fallback_reserve_seconds,
                    deadline=request.deadline,
                    observation=request.observation,
                )
            case MowerAction():
                options: dict[str, Any] = {}
                if request.on_dispatch is not None:
                    options["on_dispatch"] = request.on_dispatch
                return client._sync_call_point_cloud_action(
                    request.payload,
                    operation=request.operation,
                    deadline=request.deadline,
                    require_data=request.require_data,
                    **options,
                )
            case ParseMetadata():
                return parse_pcd_metadata(
                    request.content,
                    max_bytes=request.max_bytes,
                    deadline=request.deadline,
                )
            case WaitForObject():
                time.sleep(request.seconds)
                return None
            case DownloadFile():
                return _download_point_cloud_content_with_identity(
                    request.url,
                    timeout=request.timeout,
                    max_bytes=request.max_bytes,
                )
            case ResolveObjectURL():
                options = {}
                if request.require_response:
                    options["require_response"] = True
                return client._sync_get_point_cloud_download_url(
                    cloud,
                    request.object_name,
                    deadline=request.deadline,
                    **options,
                )
            case SignObject():
                options = {}
                if request.require_response:
                    options["require_response"] = True
                return cloud.get_interim_file_url(
                    request.object_name,
                    retry_count=request.retry_count,
                    timeout=request.timeout,
                    deadline=request.deadline,
                    **options,
                )
            case RawAppAction():
                options = {}
                if request.on_dispatch is not None:
                    options["on_dispatch"] = request.on_dispatch
                return client._sync_call_app_action(
                    request.payload,
                    retry_count=request.retry_count,
                    timeout=request.timeout,
                    deadline=request.deadline,
                    redact_response=request.redact_response,
                    raise_on_api_error=request.raise_on_api_error,
                    **options,
                )
            case _:
                assert_never(request)

    result: list[T] = []

    def capture() -> Generator[PointCloudRequest, Any]:
        result.append((yield from plan))

    execution = capture()
    try:
        request = next(execution)
        while True:
            try:
                response = dispatch(request)
            except Exception as error:
                request = execution.throw(error)
            else:
                request = execution.send(response)
    except StopIteration:
        return result[0]
    finally:
        execution.close()
