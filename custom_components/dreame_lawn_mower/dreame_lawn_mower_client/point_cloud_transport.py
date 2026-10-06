"""Typed I/O requests emitted by point-cloud generation policy."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, kw_only=True)
class CloudSetup:
    deadline: float


@dataclass(frozen=True)
class CachedObjectNames:
    pass


@dataclass(frozen=True, kw_only=True)
class ObjectIdentity:
    object_name: str
    deadline: float
    download_timeout: float
    max_bytes: int


@dataclass(frozen=True, kw_only=True)
class DownloadObject(ObjectIdentity):
    observation: dict[str, Any] | None = None


@dataclass(frozen=True, kw_only=True)
class StoredObject(DownloadObject):
    map_index: int


@dataclass(frozen=True, kw_only=True)
class ReadAnnouncement:
    deadline: float
    requested_after_ms: int
    baseline: tuple[str, int] | None = None
    require_post_request: bool = False
    fallback_reserve_seconds: float = 0
    observation: dict[str, Any] | None = None


@dataclass(frozen=True, kw_only=True)
class MowerAction:
    payload: Mapping[str, Any]
    operation: str
    deadline: float
    require_data: bool
    on_dispatch: Callable[[], None] | None = None


@dataclass(frozen=True, kw_only=True)
class ParseMetadata:
    content: bytes
    max_bytes: int
    deadline: float


@dataclass(frozen=True, kw_only=True)
class WaitForObject:
    seconds: float


@dataclass(frozen=True, kw_only=True)
class DownloadFile:
    url: str
    timeout: float
    max_bytes: int


@dataclass(frozen=True, kw_only=True)
class ResolveObjectURL:
    object_name: str
    deadline: float
    require_response: bool = False


@dataclass(frozen=True, kw_only=True)
class SignObject:
    object_name: str
    retry_count: int
    timeout: float
    deadline: float
    require_response: bool = False


@dataclass(frozen=True, kw_only=True)
class RawAppAction:
    payload: Mapping[str, Any]
    retry_count: int
    timeout: float
    deadline: float
    redact_response: bool
    raise_on_api_error: bool
    on_dispatch: Callable[[], None] | None = None


type PointCloudRequest = (
    CloudSetup
    | CachedObjectNames
    | StoredObject
    | ReadAnnouncement
    | MowerAction
    | ObjectIdentity
    | DownloadObject
    | ParseMetadata
    | WaitForObject
    | DownloadFile
    | SignObject
    | RawAppAction
    | ResolveObjectURL
)
