"""Read-only discovery and download of photos attached to finished task sessions.

Each finished task publishes an event (siid 4 / eiid 1) whose piid 9 names a
stored session summary such as ``ali_dreame/2026/10/15/IQ000001/-1234_0103.json``.
The summary's ``photo_list`` holds bare file names (``1792026162.jpg``); the
vendor app signs them as ``IQ000001/-1234/ali_dreame/<file>``. Observed on a
MOVA LiDAX Ultra 1000 (mova.mower.g2529c) for point patrol photos.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .http_response import HttpResponseError
from .public_download import MAX_PUBLIC_JSON_BYTES, PublicDownloadError

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession

SESSION_EVENT_KEY = "4.1"
SESSION_TASK_PIID = 1
SESSION_SUMMARY_PIID = 9
MAX_SESSION_PHOTO_BYTES = 8 * 1024 * 1024
MAX_SESSION_PHOTOS = 100
MAX_SESSION_HISTORY = 100
# How many recent sessions feed the per-zone mowing history.
ZONE_HISTORY_SESSIONS = 30
ZONE_STATUS_COMPLETED = 2
# Person-detection photos are stored next to the session photos as
# ``<unix seconds>_person.jpg`` and are listed by no session summary. A LiDAX
# Ultra 1000 named the photo one second before Home Assistant observed the
# code 27 notice, so search a small window nearest that second first.
PERSON_PHOTO_SUFFIX = "_person.jpg"
PERSON_PHOTO_SEARCH_OFFSETS = tuple(
    sorted(range(-20, 4), key=lambda offset: (abs(offset + 1), offset > -1))
)
_PHOTO_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}\.(?:jpe?g|png)", re.I)


def is_session_photo_name(name: str) -> bool:
    """Accept a portable image basename, never a path or alternate data stream."""
    return _PHOTO_NAME.fullmatch(name) is not None


def _summary_parts(object_name: str) -> tuple[str, str, str] | None:
    parts = object_name.split("/")
    if (
        len(parts) < 3
        or parts[0] != "ali_dreame"
        or any(not part or part in (".", "..") or "\\" in part for part in parts)
        or not parts[-1].endswith(".json")
    ):
        return None
    prefix, separator, _ = parts[-1].partition("_")
    if not separator or not prefix:
        return None
    return parts[-2], prefix, parts[0]


@dataclass(frozen=True)
class TaskSession:
    """One finished task as announced by the session event."""

    object_name: str
    task: int | None
    created_at_ms: int | None


@dataclass(frozen=True)
class SessionPhoto:
    """One photo of a finished task session."""

    file_name: str
    object_name: str
    session: TaskSession


def task_sessions_from_history(records: Any) -> list[TaskSession]:
    """Decode session events, newest first, ignoring malformed records."""
    sessions: list[TaskSession] = []
    for record in records if isinstance(records, list) else []:
        if not isinstance(record, Mapping):
            continue
        raw = record.get("history")
        try:
            values = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            continue
        props = {
            item.get("piid"): item.get("value")
            for item in (values if isinstance(values, list) else [])
            if isinstance(item, Mapping) and type(item.get("piid")) is int
        }
        object_name = props.get(SESSION_SUMMARY_PIID)
        if not isinstance(object_name, str) or _summary_parts(object_name) is None:
            continue
        task = props.get(SESSION_TASK_PIID)
        created = record.get("createTime")
        sessions.append(
            TaskSession(
                object_name=object_name,
                task=task if isinstance(task, int) else None,
                created_at_ms=created if isinstance(created, int) else None,
            )
        )
    sessions.sort(key=lambda item: item.created_at_ms or 0, reverse=True)
    return sessions


def session_photo_object_name(session_object_name: str, file_name: str) -> str | None:
    """Map a summary path and a photo file name to the signed object name."""
    parts = _summary_parts(session_object_name)
    if parts is None or not is_session_photo_name(file_name):
        return None
    folder, prefix, namespace = parts
    return f"{folder}/{prefix}/{namespace}/{file_name}"


def session_photo_names(summary: Any) -> list[str]:
    """Return the summary's photo file names once each, in vendor order."""
    names = summary.get("photo_list") if isinstance(summary, Mapping) else None
    seen: list[str] = []
    for name in names if isinstance(names, list) else []:
        if isinstance(name, str) and is_session_photo_name(name) and name not in seen:
            seen.append(name)
            if len(seen) == MAX_SESSION_PHOTOS:
                break
    return seen


async def async_get_session_photos(
    client: DreameLawnMowerClient,
    *,
    session_count: int = 1,
    deadline: float | None = None,
) -> list[SessionPhoto]:
    """List the photos of the latest finished task sessions."""
    if not 1 <= session_count <= 20:
        raise ValueError("session_count must be between 1 and 20")
    deadline = min(deadline or float("inf"), time.monotonic() + 60)
    descriptor = client._descriptor

    async def read(cloud: DreameCloudSession) -> list[SessionPhoto]:
        records = await cloud.async_get_property_history(
            descriptor.did,
            SESSION_EVENT_KEY,
            kind="event",
            require_response=True,
            limit=min(MAX_SESSION_HISTORY, max(20, session_count * 5)),
            deadline=deadline,
        )
        photos: list[SessionPhoto] = []
        usable_sessions = 0
        seen_sessions: set[str] = set()
        seen_photos: set[str] = set()
        for session in task_sessions_from_history(records)[:MAX_SESSION_HISTORY]:
            if session.object_name in seen_sessions:
                continue
            seen_sessions.add(session.object_name)
            summary = await _async_read_session_summary(
                cloud, client, session.object_name, deadline=deadline
            )
            if summary is None:
                continue
            usable_sessions += 1
            for name in session_photo_names(summary):
                object_name = session_photo_object_name(session.object_name, name)
                if object_name and object_name not in seen_photos:
                    seen_photos.add(object_name)
                    photos.append(SessionPhoto(name, object_name, session))
                    if len(photos) == MAX_SESSION_PHOTOS:
                        return photos
            if usable_sessions == session_count:
                break
        return photos

    return await client._async_cloud_read(read)


async def async_download_session_photo(
    client: DreameLawnMowerClient,
    photo: SessionPhoto,
    *,
    deadline: float | None = None,
    max_bytes: int = MAX_SESSION_PHOTO_BYTES,
) -> bytes | None:
    """Download one session photo; ``None`` when the cloud has no signed URL."""
    deadline = min(deadline or float("inf"), time.monotonic() + 30)
    descriptor = client._descriptor

    async def read(cloud: DreameCloudSession) -> bytes | None:
        url = await cloud.async_get_interim_file_url(
            descriptor.did,
            descriptor.model,
            photo.object_name,
            deadline=deadline,
            require_response=True,
        )
        if not url:
            return None
        return await _read_optional_object(
            cloud,
            url,
            deadline=deadline,
            max_bytes=min(max_bytes, MAX_SESSION_PHOTO_BYTES),
        )

    return await client._async_cloud_read(read)


async def _async_read_session_summary(
    cloud: DreameCloudSession,
    client: DreameLawnMowerClient,
    object_name: str,
    *,
    deadline: float,
) -> Mapping[str, Any] | None:
    """Download and decode one session summary; ``None`` when unusable."""
    descriptor = client._descriptor
    url = await cloud.async_get_interim_file_url(
        descriptor.did,
        descriptor.model,
        object_name,
        deadline=deadline,
        require_response=True,
    )
    if not url:
        return None
    try:
        content = await _read_optional_object(
            cloud,
            url,
            deadline=deadline,
            max_bytes=MAX_PUBLIC_JSON_BYTES,
        )
    except HttpResponseError as err:
        if err.reason == "byte_limit":
            return None
        raise
    if content is None:
        return None
    try:
        summary = json.loads(content)
    except ValueError:
        return None
    return summary if isinstance(summary, Mapping) else None


@dataclass(frozen=True)
class ZoneMowingRecord:
    """How one finished task session left one zone.

    ``region_status`` pairs a zone id with a status; status ``2`` marked every
    zone a LiDAX Ultra 1000 mowed to the end. Summaries carry no map index, so
    zone ids are those of the map that was active for the task.
    """

    zone_id: int
    status: int
    started_at: int | None
    ended_at: int | None
    mode: int | None
    session: str

    @property
    def completed(self) -> bool:
        return self.status == ZONE_STATUS_COMPLETED


def zone_mowing_records(
    summary: Any, session_object_name: str
) -> tuple[ZoneMowingRecord, ...]:
    """Return the per-zone outcome of one session summary."""
    if not isinstance(summary, Mapping):
        return ()
    statuses = summary.get("region_status")
    if not isinstance(statuses, list):
        return ()

    def _int(value: Any) -> int | None:
        return value if type(value) is int else None

    records: list[ZoneMowingRecord] = []
    seen: set[int] = set()
    for item in statuses:
        if not isinstance(item, list | tuple) or len(item) < 2:
            continue
        zone_id, status = _int(item[0]), _int(item[1])
        if zone_id is None or status is None or zone_id <= 0 or zone_id in seen:
            continue
        seen.add(zone_id)
        records.append(
            ZoneMowingRecord(
                zone_id=zone_id,
                status=status,
                started_at=_int(summary.get("start")),
                ended_at=_int(summary.get("end")),
                mode=_int(summary.get("mode")),
                session=session_object_name,
            )
        )
    return tuple(records)


async def async_get_zone_mowing_records(
    client: DreameLawnMowerClient,
    *,
    known: Mapping[str, tuple[ZoneMowingRecord, ...]] | None = None,
    deadline: float | None = None,
) -> dict[str, tuple[ZoneMowingRecord, ...]]:
    """Return per-zone outcomes of the recent sessions, keyed by summary.

    Summaries never change once published, so those in ``known`` are reused
    and only new sessions are downloaded.
    """
    known = known or {}
    deadline = min(deadline or float("inf"), time.monotonic() + 60)
    descriptor = client._descriptor

    async def read(
        cloud: DreameCloudSession,
    ) -> dict[str, tuple[ZoneMowingRecord, ...]]:
        records = await cloud.async_get_property_history(
            descriptor.did,
            SESSION_EVENT_KEY,
            kind="event",
            require_response=True,
            limit=ZONE_HISTORY_SESSIONS,
            deadline=deadline,
        )
        result: dict[str, tuple[ZoneMowingRecord, ...]] = {}
        for session in task_sessions_from_history(records)[:ZONE_HISTORY_SESSIONS]:
            name = session.object_name
            if name in result:
                continue
            if name in known:
                result[name] = known[name]
                continue
            summary = await _async_read_session_summary(
                cloud, client, name, deadline=deadline
            )
            if summary is not None:
                result[name] = zone_mowing_records(summary, name)
        return result

    return await client._async_cloud_read(read)


def latest_zone_mowing(
    sessions: Mapping[str, tuple[ZoneMowingRecord, ...]],
) -> dict[int, dict[str, ZoneMowingRecord | None]]:
    """Return, per zone, its latest record and its latest completed record."""
    result: dict[int, dict[str, ZoneMowingRecord | None]] = {}
    ordered = sorted(
        (record for records in sessions.values() for record in records),
        key=lambda record: record.ended_at or record.started_at or 0,
        reverse=True,
    )
    for record in ordered:
        entry = result.setdefault(record.zone_id, {"latest": record, "completed": None})
        if entry["completed"] is None and record.completed:
            entry["completed"] = record
    return result


async def _read_optional_object(
    cloud: DreameCloudSession,
    url: str,
    *,
    deadline: float,
    max_bytes: int,
) -> bytes | None:
    """Skip vendor-deleted objects while preserving other download failures."""
    try:
        return await cloud.async_get_public_file(
            url,
            deadline=deadline,
            max_bytes=max_bytes,
        )
    except PublicDownloadError as err:
        if err.status in (404, 410):
            return None
        raise


def person_photo_object_names(
    session_object_name: str, detected_at: float
) -> list[str]:
    """Return candidate object names for a detection, most likely first."""
    parts = _summary_parts(session_object_name)
    if parts is None:
        return []
    folder, prefix, namespace = parts
    second = int(detected_at)
    return [
        f"{folder}/{prefix}/{namespace}/{second + offset}{PERSON_PHOTO_SUFFIX}"
        for offset in PERSON_PHOTO_SEARCH_OFFSETS
    ]


async def async_find_person_photo(
    client: DreameLawnMowerClient,
    detected_at: float,
    *,
    deadline: float | None = None,
    max_bytes: int = MAX_SESSION_PHOTO_BYTES,
) -> tuple[SessionPhoto, bytes] | None:
    """Download the photo of one person detection; ``None`` when none exists."""
    deadline = min(deadline or float("inf"), time.monotonic() + 60)
    descriptor = client._descriptor

    async def read(cloud: DreameCloudSession) -> tuple[SessionPhoto, bytes] | None:
        records = await cloud.async_get_property_history(
            descriptor.did,
            SESSION_EVENT_KEY,
            kind="event",
            require_response=True,
            limit=5,
            deadline=deadline,
        )
        sessions = task_sessions_from_history(records)
        if not sessions:
            return None
        # Only the storage folder comes from the session; the detection may
        # belong to a task whose summary is not published yet.
        storage = TaskSession(sessions[0].object_name, None, None)
        for object_name in person_photo_object_names(storage.object_name, detected_at):
            url = await cloud.async_get_interim_file_url(
                descriptor.did,
                descriptor.model,
                object_name,
                deadline=deadline,
                require_response=True,
            )
            if not url:
                continue
            content = await _read_optional_object(
                cloud,
                url,
                deadline=deadline,
                max_bytes=min(max_bytes, MAX_SESSION_PHOTO_BYTES),
            )
            if content is not None:
                name = object_name.rsplit("/", 1)[1]
                return SessionPhoto(name, object_name, storage), content
        return None

    return await client._async_cloud_read(read)
