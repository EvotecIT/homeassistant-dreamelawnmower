"""Bounded private-media archive for photos of completed mower tasks."""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .dreame_lawn_mower_client.exceptions import DeviceException, DreameLawnMowerError
from .dreame_lawn_mower_client.http_response import HttpResponseError
from .dreame_lawn_mower_client.session_photos import (
    MAX_SESSION_PHOTO_BYTES,
    MAX_SESSION_PHOTOS,
    SessionPhoto,
    async_download_session_photo,
    async_get_session_photos,
    is_session_photo_name,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .dreame_lawn_mower_client.client import DreameLawnMowerClient

MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_BATCH_BYTES = 64 * 1024 * 1024
BATCH_TIMEOUT = 120
_ARCHIVE_WRITE_LOCK = threading.Lock()


def photo_archive_target(folder: Path, photo: SessionPhoto) -> Path:
    """Keep distinct cloud objects separate even when filenames are reused."""
    if not is_session_photo_name(photo.file_name):
        raise ValueError("Invalid session photo filename")
    album = hashlib.sha256(photo.object_name.encode()).hexdigest()[:24]
    return folder / album / photo.file_name


def _archive_size(folder: Path) -> int:
    """Measure retained files without following directory or file symlinks."""
    size = 0
    for item in folder.rglob("*"):
        try:
            if not item.is_symlink() and item.is_file():
                size += item.stat().st_size
        except FileNotFoundError:
            # A cancelled writer may finish removing its sibling staging file
            # while a replacement service takes its initial usage snapshot.
            continue
    return size


def _write_photo(target: Path, content: bytes) -> bool:
    """Publish complete bytes without replacing an existing photo, then clean up."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent,
            prefix=".photo-",
            suffix=".part",
            delete=False,
        ) as stream:
            partial = Path(stream.name)
            stream.write(content)
        # A hard link publishes a complete sibling file atomically and fails
        # if another writer already saved this object. Never replace their file.
        try:
            os.link(partial, target)
        except FileExistsError:
            return False
        return True
    finally:
        if partial is not None:
            partial.unlink(missing_ok=True)


async def async_archive_session_photos(
    hass: HomeAssistant,
    client: DreameLawnMowerClient,
    entity_id: str,
    *,
    session_count: int = 1,
) -> dict[str, Any]:
    """Save a bounded batch to a registered media source; never prune user media."""
    media_dirs = hass.config.media_dirs
    root = media_dirs.get("local") or next(iter(media_dirs.values()), None)
    if not root:
        raise HomeAssistantError("Configure a Home Assistant media directory first")
    folder = Path(root, DOMAIN, entity_id.split(".", 1)[1])
    deadline = time.monotonic() + BATCH_TIMEOUT
    try:
        async with asyncio.timeout(BATCH_TIMEOUT):
            used = await hass.async_add_executor_job(_archive_size, folder)
            photos = await async_get_session_photos(
                client,
                session_count=session_count,
                deadline=deadline,
            )
            saved: list[dict[str, Any]] = []
            downloaded = 0
            limited = len(photos) == MAX_SESSION_PHOTOS
            for photo in photos:
                target = photo_archive_target(folder, photo)
                exists = await hass.async_add_executor_job(target.exists)
                new = False
                if not exists:
                    remaining = min(
                        MAX_BATCH_BYTES - downloaded, MAX_ARCHIVE_BYTES - used
                    )
                    if remaining <= 0:
                        limited = True
                        break
                    try:
                        content = await async_download_session_photo(
                            client,
                            photo,
                            deadline=deadline,
                            max_bytes=min(MAX_SESSION_PHOTO_BYTES, remaining),
                        )
                    except HttpResponseError as err:
                        if err.reason != "byte_limit":
                            raise
                        limited = True
                        # An oversized object consumes its attempted allowance;
                        # repeated rejects must not bypass the batch budget.
                        downloaded += min(MAX_SESSION_PHOTO_BYTES, remaining)
                        if remaining >= MAX_SESSION_PHOTO_BYTES:
                            continue
                        break
                    if content is None:
                        continue
                    downloaded += len(content)
                    written = await hass.async_add_executor_job(
                        _write_photo_with_quota,
                        folder,
                        target,
                        content,
                    )
                    if written is None:
                        limited = True
                        break
                    new = written
                    if new:
                        used += len(content)
                saved.append(
                    {
                        "file": str(target),
                        "new": new,
                        "task": photo.session.task,
                        "session": photo.session.object_name,
                    }
                )
            return {"photos": saved, "limited": limited}
    except (DreameLawnMowerError, DeviceException) as err:
        raise HomeAssistantError("Session photos unavailable") from err
    except TimeoutError as err:
        raise HomeAssistantError(
            "Session photo download timed out; saved photos are kept"
        ) from err
    except (OSError, ValueError) as err:
        raise HomeAssistantError(
            "Unable to save session photos to the media directory"
        ) from err


def _write_photo_with_quota(folder: Path, target: Path, content: bytes) -> bool | None:
    """Recheck quota and publish under ownership that outlives service cancellation."""
    # Entry reloads and cancelled executor futures can leave an older writer
    # running. Serialize the real filesystem operation rather than trusting an
    # entity-instance lock or a usage snapshot taken before the download.
    with _ARCHIVE_WRITE_LOCK:
        if target.exists():
            return False
        if _archive_size(folder) + len(content) > MAX_ARCHIVE_BYTES:
            return None
        return _write_photo(target, content)
