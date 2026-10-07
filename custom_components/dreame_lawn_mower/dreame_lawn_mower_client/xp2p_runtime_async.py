"""Native HTTP with owned worker lifetime for verified runtime installation."""

from __future__ import annotations

import asyncio
import concurrent.futures
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from aiohttp import ClientSession

from .client_refresh import _run_state_worker
from .public_download import async_download_public_response
from .video_runtime import DreameLawnMowerVideoRuntimeError
from .xp2p_host_runtime import DreameLawnMowerXp2pHostAssets
from .xp2p_runtime_bootstrap import (
    LARGE_PAGE_ANDROID_BUILD_ARTIFACT_SIZE,
    ensure_xp2p_host_runtime,
)

# Individual SDK/runtime assets and selected ZIP members, never the whole build ZIP.
_MAX_ASSET_BYTES = 256 * 1024 * 1024


@dataclass
class _Response:
    status_code: int
    content: bytes
    headers: Mapping[str, str]
    url: str


class _RuntimeHttp:
    def __init__(self, session: ClientSession) -> None:
        self.session = session
        self.loop = asyncio.get_running_loop()
        self.cancelled = Event()
        self.tasks: set[asyncio.Task[_Response]] = set()

    def stop(self) -> None:
        """Called on the event loop; no worker may begin another request."""
        self.cancelled.set()
        for task in tuple(self.tasks):
            task.cancel()

    def get(
        self,
        url: str,
        *,
        timeout: float,
        headers: Mapping[str, str] | None = None,
    ) -> _Response:
        """Bridge the synchronous ZIP/file installer to native event-loop HTTP."""
        if self.cancelled.is_set():
            raise DreameLawnMowerVideoRuntimeError(
                "Runtime installation was cancelled."
            )
        future = asyncio.run_coroutine_threadsafe(
            self.download(url, timeout=timeout, headers=headers),
            self.loop,
        )
        try:
            return future.result()
        except concurrent.futures.CancelledError:
            raise DreameLawnMowerVideoRuntimeError(
                "Runtime installation was cancelled.",
            ) from None

    async def download(
        self,
        url: str,
        *,
        timeout: float,
        headers: Mapping[str, str] | None,
    ) -> _Response:
        if self.cancelled.is_set():
            raise DreameLawnMowerVideoRuntimeError(
                "Runtime installation was cancelled."
            )
        task = asyncio.current_task()
        assert task is not None
        self.tasks.add(task)
        try:
            byte_range = None
            response_headers: dict[str, str] = {}
            if headers is not None:
                value = headers["Range"]
                if not value.startswith("bytes="):
                    raise ValueError("Unsupported runtime byte range")
                start, end = (int(part) for part in value[6:].split("-"))
                byte_range = (start, end, LARGE_PAGE_ANDROID_BUILD_ARTIFACT_SIZE)
                response_headers["Content-Range"] = (
                    f"bytes {start}-{end}/{LARGE_PAGE_ANDROID_BUILD_ARTIFACT_SIZE}"
                )
            result = await async_download_public_response(
                self.session,
                url,
                deadline=time.monotonic() + timeout,
                timeout=timeout,
                max_bytes=_MAX_ASSET_BYTES,
                byte_range=byte_range,
            )
            return _Response(
                206 if byte_range else 200,
                result.content,
                response_headers,
                result.url,
            )
        finally:
            self.tasks.discard(task)


async def async_ensure_xp2p_host_runtime(
    root: str | Path,
    session: ClientSession,
    *,
    machine: str | None = None,
    page_size: int | None = None,
    timeout: float = 60.0,
) -> DreameLawnMowerXp2pHostAssets:
    """Borrow the session and own installer/HTTP work through cancellation."""
    http = _RuntimeHttp(session)
    worker = asyncio.create_task(
        _run_state_worker(
            lambda: ensure_xp2p_host_runtime(
                root,
                machine=machine,
                page_size=page_size,
                timeout=timeout,
                http_client=http,
                _cancelled=http.cancelled,
            ),
            http.cancelled,
        )
    )
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        http.stop()
        worker.cancel()
        while not worker.done():
            try:
                await asyncio.wait({worker})
            except asyncio.CancelledError:
                http.stop()
        if not worker.cancelled():
            worker.exception()
        # The worker can no longer schedule HTTP. Drain cancellation cleanup too.
        while http.tasks:
            try:
                await asyncio.wait(tuple(http.tasks))
            except asyncio.CancelledError:
                http.stop()
        raise
