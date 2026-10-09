"""Shared point-cloud object validation, signing, and action error policy."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Generator, Mapping
from typing import Any

from requests.exceptions import Timeout as RequestsTimeout

from .client_map_helpers import (
    _app_object_extension,
    _point_cloud_action_data,
    _point_cloud_download_url,
    _PointCloudObjectIdentity,
)
from .exceptions import (
    DeviceException,
    DreameLawnMowerCloudAPIError,
    DreameLawnMowerConnectionError,
)
from .point_cloud import (
    DreameLawnMowerPointCloudDownload,
    DreameLawnMowerPointCloudError,
)
from .point_cloud_diagnostics import action_reply_observation, value_shape
from .point_cloud_policy import (
    _POINT_CLOUD_OBJECT_EXTENSIONS,
    _POINT_CLOUD_STORED_DOWNLOAD_TIMEOUT_SECONDS,
)
from .point_cloud_trace import record_point_cloud_stage
from .point_cloud_transport import (
    DownloadFile,
    DownloadObject,
    ParseMetadata,
    PointCloudRequest,
    RawAppAction,
    ResolveObjectURL,
    SignObject,
)


def stored_object(
    object_name: str,
    *,
    map_index: int,
    deadline: float,
    download_timeout: float,
    max_bytes: int,
    observation: dict[str, Any] | None = None,
) -> Generator[PointCloudRequest, Any, DreameLawnMowerPointCloudDownload | None]:
    """Return one valid stored PCD from either object-discovery route."""
    observation = {} if observation is None else observation
    extension = _app_object_extension(object_name)
    if extension is None or extension.casefold() not in _POINT_CLOUD_OBJECT_EXTENSIONS:
        return None
    stored_deadline = min(
        deadline,
        time.monotonic() + _POINT_CLOUD_STORED_DOWNLOAD_TIMEOUT_SECONDS,
    )
    observation["download_attempts"] = observation.get("download_attempts", 0) + 1
    record_point_cloud_stage("stored_download", observation)
    try:
        content, content_type, _ = yield DownloadObject(
            object_name=object_name,
            deadline=stored_deadline,
            download_timeout=min(
                download_timeout,
                _POINT_CLOUD_STORED_DOWNLOAD_TIMEOUT_SECONDS,
            ),
            max_bytes=max_bytes,
            observation=observation,
        )
        observation["last_download_step"] = "validation"
        metadata = yield ParseMetadata(
            content=content, max_bytes=max_bytes, deadline=stored_deadline
        )
    except (DeviceException, DreameLawnMowerPointCloudError) as err:
        if isinstance(err, DreameLawnMowerPointCloudError):
            observation.update(err.safe_diagnostics()["attempt"])
        observation["last_download_result"] = (
            f"error:{err.code}"
            if isinstance(err, DreameLawnMowerPointCloudError)
            else "error:device"
        )
        return None
    observation["last_download_result"] = "validated"
    return DreameLawnMowerPointCloudDownload(
        map_index=map_index,
        content=content,
        metadata=metadata,
        content_type=content_type,
        source="stored",
    )


def object_identity(
    object_name: str,
    *,
    deadline: float,
    download_timeout: float,
    max_bytes: int,
) -> Generator[PointCloudRequest, Any, tuple[bool, _PointCloudObjectIdentity | None]]:
    """Return whether a pre-generation object baseline is conclusive."""
    baseline_deadline = min(
        deadline,
        time.monotonic() + _POINT_CLOUD_STORED_DOWNLOAD_TIMEOUT_SECONDS,
    )
    record_point_cloud_stage("baseline_signer")
    try:
        raw_url = yield ResolveObjectURL(
            object_name=object_name, deadline=baseline_deadline, require_response=True
        )
        record_point_cloud_stage(
            "signer_reply",
            {"signer_shape": value_shape(raw_url)},
        )
    except (
        DeviceException,
        DreameLawnMowerPointCloudError,
        json.JSONDecodeError,
    ) as err:
        record_point_cloud_stage(
            "signer_reply",
            {
                "last_download_step": "signer",
                "download_reason": (
                    "signer_invalid_response"
                    if isinstance(err, json.JSONDecodeError)
                    else "transport_error"
                ),
                **(
                    err.safe_diagnostics()["attempt"]
                    if isinstance(err, DreameLawnMowerPointCloudError)
                    else {}
                ),
            },
        )
        return False, None

    try:
        url = _point_cloud_download_url(raw_url)
    except DreameLawnMowerPointCloudError as err:
        # Only the signer's explicit empty result proves the object was
        # unavailable before o:10. Malformed responses are inconclusive.
        record_point_cloud_stage(
            "signer_reply",
            {
                "last_download_step": "signer",
                **err.safe_diagnostics()["attempt"],
            },
        )
        return raw_url is None, None

    remaining = baseline_deadline - time.monotonic()
    if remaining <= 0:
        return False, None
    try:
        record_point_cloud_stage("baseline_download")
        _, _, identity = yield DownloadFile(
            url=url,
            timeout=min(
                download_timeout,
                _POINT_CLOUD_STORED_DOWNLOAD_TIMEOUT_SECONDS,
                remaining,
            ),
            max_bytes=max_bytes,
        )
    except DreameLawnMowerPointCloudError as err:
        # Transport, size, and content failures do not prove that a
        # signable baseline object was absent.
        record_point_cloud_stage(
            "download_result",
            {
                "last_download_step": "download",
                **err.safe_diagnostics()["attempt"],
            },
        )
        return False, None
    return True, identity


def download_object(
    object_name: str,
    *,
    deadline: float,
    download_timeout: float,
    max_bytes: int,
    observation: dict[str, Any] | None = None,
) -> Generator[PointCloudRequest, Any, tuple[bytes, str, _PointCloudObjectIdentity]]:
    observation = {} if observation is None else observation
    observation["last_download_step"] = "signer"
    observation.pop("download_http_status", None)
    observation.pop("download_bytes", None)
    observation.pop("signer_shape", None)
    observation.pop("validation_reason", None)
    observation.pop("download_reason", None)
    record_point_cloud_stage("signer", observation)
    try:
        try:
            raw_url = yield ResolveObjectURL(object_name=object_name, deadline=deadline)
        except json.JSONDecodeError as err:
            raise DreameLawnMowerPointCloudError(
                "Point-cloud signer returned an invalid response.",
                code="point_cloud_download_invalid",
                stage="download",
                public_message=(
                    "The mower's generated 3D map is not ready to download."
                ),
                retry_after_seconds=2,
                diagnostic_context={"download_reason": "signer_invalid_response"},
            ) from err
        observation["signer_shape"] = value_shape(raw_url)
        record_point_cloud_stage("signer_reply", observation)
        url = _point_cloud_download_url(raw_url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DreameLawnMowerPointCloudError(
                "Point-cloud generation timed out.",
                code="point_cloud_timeout",
                stage="download",
                public_message=("The mower did not finish the 3D map request in time."),
                timeout_seconds=download_timeout,
                retry_after_seconds=10,
            )
        observation["last_download_step"] = "download"
        record_point_cloud_stage("download", observation)
        result: tuple[bytes, str, _PointCloudObjectIdentity] = yield DownloadFile(
            url=url, timeout=min(download_timeout, remaining), max_bytes=max_bytes
        )
        observation["download_bytes"] = len(result[0])
        record_point_cloud_stage("download_result", observation)
        return result
    except (DeviceException, DreameLawnMowerPointCloudError) as err:
        if isinstance(err, DreameLawnMowerPointCloudError):
            observation.update(err.safe_diagnostics()["attempt"])
        observation["last_download_result"] = (
            f"error:{err.code}"
            if isinstance(err, DreameLawnMowerPointCloudError)
            else "error:device"
        )
        record_point_cloud_stage("download_result", observation)
        raise


def mower_action(
    payload: Mapping[str, Any],
    *,
    operation: str,
    deadline: float,
    require_data: bool,
    on_dispatch: Callable[[], None] | None = None,
) -> Generator[PointCloudRequest, Any, Any]:
    """Call one point-cloud action within the shared generation deadline."""
    record_point_cloud_stage(
        "indexed_read" if require_data else "generation_request",
    )
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
        action_options: dict[str, Any] = {
            "retry_count": 0,
            "timeout": remaining,
            "deadline": deadline,
            "redact_response": True,
            "raise_on_api_error": True,
        }
        if on_dispatch is not None:
            action_options["on_dispatch"] = on_dispatch
        response = yield RawAppAction(payload=payload, **action_options)
        record_point_cloud_stage(
            "indexed_reply" if require_data else "generation_reply",
            {"action_reply": action_reply_observation(response)},
        )
    except DreameLawnMowerCloudAPIError as err:
        raise DreameLawnMowerPointCloudError(
            f"The Dreame cloud rejected the {operation} request.",
            code="point_cloud_mower_request_rejected",
            stage="mower_request",
            public_message="The Dreame cloud rejected the mower 3D map request.",
            retry_after_seconds=10,
            vendor_error_code=err.code,
        ) from err
    except RequestsTimeout as err:
        raise DreameLawnMowerPointCloudError(
            f"The mower timed out while trying to {operation}.",
            code="point_cloud_timeout",
            stage="mower_request",
            public_message="The mower did not finish the 3D map request in time.",
            retry_after_seconds=10,
        ) from err
    except DreameLawnMowerConnectionError as err:
        if time.monotonic() >= deadline:
            raise DreameLawnMowerPointCloudError(
                "Point-cloud generation timed out.",
                code="point_cloud_timeout",
                stage="mower_request",
                public_message=("The mower did not finish the 3D map request in time."),
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
            diagnostic_context={"action_reply": action_reply_observation(response)},
        ) from err


def resolve_object_url(
    object_name: str,
    *,
    deadline: float,
    require_response: bool = False,
) -> Generator[PointCloudRequest, Any, Any]:
    """Resolve a signed point-cloud URL within the generation deadline."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DreameLawnMowerPointCloudError("Point-cloud generation timed out.")
    try:
        signer_options: dict[str, Any] = {
            "retry_count": 0,
            "timeout": remaining,
            "deadline": deadline,
        }
        if require_response:
            signer_options["require_response"] = True
        raw_url = yield SignObject(object_name=object_name, **signer_options)
    except RequestsTimeout as err:
        raise DreameLawnMowerPointCloudError(
            "Point-cloud download URL request timed out.",
            code="point_cloud_timeout",
            stage="download",
            public_message=(
                "The mower cloud timed out while preparing the generated "
                "3D map download."
            ),
            retry_after_seconds=10,
        ) from err
    if time.monotonic() >= deadline:
        raise DreameLawnMowerPointCloudError("Point-cloud generation timed out.")
    return raw_url
