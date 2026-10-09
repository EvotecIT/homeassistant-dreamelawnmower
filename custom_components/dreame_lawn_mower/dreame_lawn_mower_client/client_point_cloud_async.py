"""Native I/O driver for the shared point-cloud generation and object plans."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Generator
from threading import Event
from typing import TYPE_CHECKING, Any, assert_never

from .client_app_reads import async_command_app_action, async_read_app_action
from .client_map_helpers import _PointCloudObjectIdentity
from .client_refresh import _run_state_worker
from .exceptions import DreameLawnMowerConnectionError
from .http_response import HttpResponseError
from .point_cloud import (
    DreameLawnMowerPointCloudDownload,
    DreameLawnMowerPointCloudError,
    parse_pcd_metadata,
)
from .point_cloud_announcement import announcement_result
from .point_cloud_diagnostics import value_shape
from .point_cloud_generation_plan import point_cloud_generation
from .point_cloud_object_plan import (
    download_object,
    mower_action,
    object_identity,
    resolve_object_url,
    stored_object,
)
from .point_cloud_policy import (
    _POINT_CLOUD_ANNOUNCEMENT_PROBE_TIMEOUT_SECONDS,
    _POINT_CLOUD_ANNOUNCEMENT_PROPERTY_KEY,
)
from .point_cloud_trace import record_point_cloud_stage
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
from .public_download import PublicDownloadError, async_download_public_response

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession


async def async_generate_point_cloud(
    client: DreameLawnMowerClient,
    *,
    map_index: int,
    timeout: float,
    poll_interval: float,
    download_timeout: float,
    max_bytes: int,
    deadline: float,
    allow_stored: bool,
    allow_unscoped_stored: bool,
) -> DreameLawnMowerPointCloudDownload:
    """Own native requests and CPU workers while retaining the generation lock."""
    if not client._point_cloud_generation_lock.acquire(blocking=False):
        raise DreameLawnMowerPointCloudError(
            "Point-cloud generation is already in progress.",
            code="point_cloud_generation_in_progress",
            stage="queue",
            retry_after_seconds=5,
        )
    cancelled = Event()

    async def read(cloud: DreameCloudSession) -> DreameLawnMowerPointCloudDownload:
        async def execute[T](plan: Generator[PointCloudRequest, Any, T]) -> T:
            result: list[T] = []

            def capture() -> Generator[PointCloudRequest, Any]:
                result.append((yield from plan))

            execution = capture()
            try:
                request = next(execution)
                while True:
                    try:
                        if client._closing or cancelled.is_set():
                            raise asyncio.CancelledError
                        response = await dispatch(request)
                    except Exception as error:
                        request = execution.throw(error)
                    else:
                        request = execution.send(response)
            except StopIteration:
                return result[0]
            finally:
                execution.close()

        async def dispatch(request: PointCloudRequest) -> Any:
            match request:
                case CloudSetup():
                    try:
                        await cloud.async_ensure_authenticated(
                            timeout=max(0.001, request.deadline - time.monotonic()),
                            deadline=request.deadline,
                        )
                    except DreameLawnMowerConnectionError as err:
                        if isinstance(err.__cause__, TimeoutError):
                            raise DreameLawnMowerPointCloudError(
                                "Point-cloud cloud setup timed out.",
                                code="point_cloud_timeout",
                                stage="cloud",
                                timeout_seconds=timeout,
                                retry_after_seconds=10,
                            ) from err
                        raise
                    return None
                case CachedObjectNames():
                    async with asyncio.timeout(max(0, deadline - time.monotonic())):
                        while not client._app_map_object_cache_lock.acquire(
                            blocking=False
                        ):
                            await asyncio.sleep(0.01)
                        try:
                            return (
                                client._latest_app_map_object_names
                                if client._latest_app_map_object_inventory_identity
                                is not None
                                and client._latest_app_map_object_inventory_identity
                                == client._latest_app_map_inventory_identity
                                else ()
                            )
                        finally:
                            client._app_map_object_cache_lock.release()
                case StoredObject():
                    return await execute(
                        stored_object(
                            request.object_name,
                            map_index=request.map_index,
                            deadline=request.deadline,
                            download_timeout=request.download_timeout,
                            max_bytes=request.max_bytes,
                            observation=request.observation,
                        )
                    )
                case DownloadObject():
                    return await execute(
                        download_object(
                            request.object_name,
                            deadline=request.deadline,
                            download_timeout=request.download_timeout,
                            max_bytes=request.max_bytes,
                            observation=request.observation,
                        )
                    )
                case ObjectIdentity():
                    return await execute(
                        object_identity(
                            request.object_name,
                            deadline=request.deadline,
                            download_timeout=request.download_timeout,
                            max_bytes=request.max_bytes,
                        )
                    )
                case ReadAnnouncement():
                    observation = (
                        request.observation if request.observation is not None else {}
                    )
                    observation["status"] = "budget_exhausted"
                    record_point_cloud_stage("announcement_read")
                    remaining = request.deadline - time.monotonic()
                    budget = remaining - max(0.0, request.fallback_reserve_seconds)
                    if budget <= 0:
                        return None, None, None
                    probe_timeout = min(
                        budget, _POINT_CLOUD_ANNOUNCEMENT_PROBE_TIMEOUT_SECONDS
                    )
                    probe_deadline = min(
                        request.deadline, time.monotonic() + probe_timeout
                    )
                    try:
                        payload = await cloud.async_get_properties(
                            client._descriptor.did,
                            _POINT_CLOUD_ANNOUNCEMENT_PROPERTY_KEY,
                            timeout=probe_timeout,
                            deadline=probe_deadline,
                        )
                    except DreameLawnMowerConnectionError:
                        observation["status"] = "transport_error"
                        return None, None, None
                    if payload is None:
                        observation["status"] = "no_response"
                        return None, None, None
                    return announcement_result(
                        client._normalize_cloud_property_entries(payload),
                        payload_shape=value_shape(payload),
                        requested_after_ms=request.requested_after_ms,
                        baseline=request.baseline,
                        require_post_request=request.require_post_request,
                        observation=observation,
                    )
                case MowerAction():
                    return await execute(
                        mower_action(
                            request.payload,
                            operation=request.operation,
                            deadline=request.deadline,
                            require_data=request.require_data,
                            on_dispatch=request.on_dispatch,
                        )
                    )
                case RawAppAction():
                    if request.payload.get("m") == "g":
                        return await async_read_app_action(
                            client,
                            request.payload,
                            deadline=request.deadline,
                            strict_response=request.raise_on_api_error,
                        )
                    return await async_command_app_action(
                        client,
                        request.payload,
                        deadline=request.deadline,
                        on_dispatch=request.on_dispatch,
                    )
                case ResolveObjectURL():
                    return await execute(
                        resolve_object_url(
                            request.object_name,
                            deadline=request.deadline,
                            require_response=request.require_response,
                        )
                    )
                case SignObject():
                    try:
                        return await cloud.async_get_interim_file_url(
                            client._descriptor.did,
                            client._descriptor.model,
                            request.object_name,
                            deadline=request.deadline,
                            require_response=request.require_response,
                            timeout=request.timeout,
                        )
                    except DreameLawnMowerConnectionError as err:
                        timed_out = (
                            isinstance(err.__cause__, TimeoutError)
                            or time.monotonic() >= request.deadline
                        )
                        raise DreameLawnMowerPointCloudError(
                            "Point-cloud download URL request failed.",
                            code="point_cloud_timeout"
                            if timed_out
                            else "point_cloud_download_invalid",
                            stage="download",
                            retry_after_seconds=10 if timed_out else 2,
                            diagnostic_context={
                                "download_reason": (
                                    "signer_invalid_response"
                                    if isinstance(err.__cause__, ValueError)
                                    else "transport_error"
                                )
                            },
                        ) from err
                case DownloadFile():
                    try:
                        response = await async_download_public_response(
                            cloud._session,
                            request.url,
                            deadline=time.monotonic() + request.timeout,
                            timeout=request.timeout,
                            max_bytes=request.max_bytes,
                            https_only=True,
                        )
                    except DreameLawnMowerConnectionError as err:
                        context: dict[str, Any] = {"download_reason": "transport_error"}
                        if isinstance(err, (PublicDownloadError, HttpResponseError)):
                            context["download_reason"] = err.reason
                        if (
                            isinstance(err, PublicDownloadError)
                            and err.status is not None
                        ):
                            context["download_http_status"] = err.status
                        raise DreameLawnMowerPointCloudError(
                            "The point-cloud download could not be completed.",
                            diagnostic_context=context,
                        ) from err
                    identity = await _run_state_worker(
                        lambda: _PointCloudObjectIdentity(
                            content_sha256=hashlib.sha256(response.content).hexdigest(),
                            etag=response.etag,
                            last_modified=response.last_modified,
                        ),
                        cancelled,
                    )
                    return response.content, response.content_type, identity
                case ParseMetadata():
                    return await _run_state_worker(
                        lambda: parse_pcd_metadata(
                            request.content,
                            max_bytes=request.max_bytes,
                            deadline=request.deadline,
                        ),
                        cancelled,
                    )
                case WaitForObject():
                    await asyncio.sleep(request.seconds)
                    return None
                case _:
                    assert_never(request)

        return await execute(
            point_cloud_generation(
                client._account_type,
                str(client._descriptor.model),
                map_index,
                timeout,
                poll_interval,
                download_timeout,
                max_bytes,
                deadline,
                allow_stored,
                allow_unscoped_stored,
            )
        )

    try:
        return await client._async_cloud_read(read)
    finally:
        cancelled.set()
        client._point_cloud_generation_lock.release()
