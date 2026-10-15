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

from .public_download import MAX_PUBLIC_JSON_BYTES

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession

SESSION_EVENT_KEY = "4.1"
SESSION_TASK_PIID = 1
SESSION_SUMMARY_PIID = 9
MAX_SESSION_PHOTO_BYTES = 8 * 1024 * 1024
MAX_SESSION_PHOTOS = 100
MAX_SESSION_HISTORY = 100
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
            url = await cloud.async_get_interim_file_url(
                descriptor.did,
                descriptor.model,
                session.object_name,
                deadline=deadline,
            )
            if not url:
                continue
            content = await cloud.async_get_public_file(
                url,
                deadline=deadline,
                max_bytes=MAX_PUBLIC_JSON_BYTES,
            )
            try:
                summary = json.loads(content)
            except ValueError:
                continue
            if not isinstance(summary, Mapping):
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
        )
        if not url:
            return None
        return await cloud.async_get_public_file(
            url,
            deadline=deadline,
            max_bytes=min(max_bytes, MAX_SESSION_PHOTO_BYTES),
        )

    return await client._async_cloud_read(read)
