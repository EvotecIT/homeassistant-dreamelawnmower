"""Loopback FLV fan-out for the mower's single-consumer video source.

The mower runtime exposes one private FLV URL which must never be consumed by
multiple Home Assistant features directly.  This relay is the single owner of
that URL and gives local HLS/WebRTC consumers independent loopback responses.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Final

from aiohttp import ClientResponseError, ClientSession, ClientTimeout, web
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .ha_tasks import create_background_task

_LOGGER = logging.getLogger(__name__)

_QUEUE_DEPTH: Final = 96
_MAX_SUBSCRIBER_QUEUE_BYTES: Final = 12 * 1024 * 1024
_MAX_GOP_BYTES: Final = 8 * 1024 * 1024
_MAX_HEADER_BYTES: Final = 1024
_MAX_TAG_BYTES: Final = 4 * 1024 * 1024
_MAX_SPS_BYTES: Final = 4 * 1024
_MAX_SPS_BIT_OPERATIONS: Final = 32 * 1024
_UPSTREAM_READ_TIMEOUT: Final = 30.0
_MEDIA_READY_TIMEOUT: Final = 15.0
_IDLE_GRACE: Final = 15.0
_IDLE_POLL_INTERVAL: Final = 1.0

SourceFactory = Callable[[], Awaitable[str | None]]
MediaReadyCallback = Callable[[dict[str, object]], Awaitable[None]]
FailureCallback = Callable[[str], Awaitable[None]]
IdleCallback = Callable[[], Awaitable[None]]
KeepWarmCallback = Callable[[], bool]
SubscriberStartedCallback = Callable[[bool], None]


class _FlvFormatError(ValueError):
    """A bounded parser failure safe to expose in diagnostics."""


def _safe_relay_failure(error: Exception) -> str:
    """Return a bounded failure without leaking private source URLs."""
    if isinstance(error, _FlvFormatError):
        return str(error)
    if isinstance(error, ClientResponseError):
        return f"The mower video source returned HTTP {error.status}."
    if isinstance(error, TimeoutError):
        return "The mower video source timed out."
    message = str(error)
    if message in {
        "The mower video source did not start.",
        "The mower video source ended.",
    }:
        return message
    return f"The mower video source failed ({type(error).__name__})."


class _BitReader:
    """Minimal H.264 SPS bit reader."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._offset = 0

    def bits(self, count: int) -> int:
        if (
            count < 0
            or self._offset + count > len(self._payload) * 8
            or self._offset + count > _MAX_SPS_BIT_OPERATIONS
        ):
            raise ValueError("The mower video SPS exceeded its parsing budget.")
        value = 0
        for _ in range(count):
            byte = self._payload[self._offset // 8]
            bit = 7 - (self._offset % 8)
            self._offset += 1
            value = (value << 1) | ((byte >> bit) & 1)
        return value

    def ue(self) -> int:
        zeros = 0
        while self.bits(1) == 0:
            zeros += 1
        return (1 << zeros) - 1 + (self.bits(zeros) if zeros else 0)

    def se(self) -> int:
        value = self.ue()
        return (value + 1) // 2 if value % 2 else -(value // 2)


def _skip_scaling_list(reader: _BitReader, size: int) -> None:
    last_scale = 8
    next_scale = 8
    for _index in range(size):
        if next_scale:
            delta_scale = reader.se()
            next_scale = (last_scale + delta_scale + 256) % 256
        last_scale = next_scale or last_scale


def _h264_sps_dimensions(sps: bytes) -> tuple[int, int] | None:
    """Decode coded width/height from one SPS NAL without a video decoder."""
    try:
        if not sps or len(sps) > _MAX_SPS_BYTES:
            return None
        rbsp = bytearray()
        zero_count = 0
        for value in sps[1:]:
            if zero_count >= 2 and value == 3:
                zero_count = 0
                continue
            rbsp.append(value)
            zero_count = zero_count + 1 if value == 0 else 0
        reader = _BitReader(bytes(rbsp))
        profile_idc = reader.bits(8)
        reader.bits(8)
        reader.bits(8)
        reader.ue()
        chroma_format_idc = 1
        separate_colour_plane = 0
        if profile_idc in {
            44,
            83,
            86,
            100,
            110,
            118,
            122,
            128,
            134,
            135,
            138,
            139,
            244,
        }:
            chroma_format_idc = reader.ue()
            if chroma_format_idc == 3:
                separate_colour_plane = reader.bits(1)
            reader.ue()
            reader.ue()
            reader.bits(1)
            if reader.bits(1):
                count = 8 if chroma_format_idc != 3 else 12
                for index in range(count):
                    if reader.bits(1):
                        _skip_scaling_list(reader, 16 if index < 6 else 64)
        reader.ue()
        pic_order_cnt_type = reader.ue()
        if pic_order_cnt_type == 0:
            reader.ue()
        elif pic_order_cnt_type == 1:
            reader.bits(1)
            reader.se()
            reader.se()
            for _ in range(reader.ue()):
                reader.se()
        reader.ue()
        reader.bits(1)
        pic_width_in_mbs_minus1 = reader.ue()
        pic_height_in_map_units_minus1 = reader.ue()
        frame_mbs_only_flag = reader.bits(1)
        if not frame_mbs_only_flag:
            reader.bits(1)
        reader.bits(1)
        crop_left = crop_right = crop_top = crop_bottom = 0
        if reader.bits(1):
            crop_left = reader.ue()
            crop_right = reader.ue()
            crop_top = reader.ue()
            crop_bottom = reader.ue()
        chroma_array_type = 0 if separate_colour_plane else chroma_format_idc
        sub_width = 2 if chroma_array_type in {1, 2} else 1
        sub_height = 2 if chroma_array_type == 1 else 1
        crop_unit_x = sub_width if chroma_array_type else 1
        crop_unit_y = (
            sub_height * (2 - frame_mbs_only_flag)
            if chroma_array_type
            else 2 - frame_mbs_only_flag
        )
        width = (pic_width_in_mbs_minus1 + 1) * 16
        height = (pic_height_in_map_units_minus1 + 1) * 16 * (2 - frame_mbs_only_flag)
        width -= (crop_left + crop_right) * crop_unit_x
        height -= (crop_top + crop_bottom) * crop_unit_y
        return (width, height) if width > 0 and height > 0 else None
    except (IndexError, ValueError):
        return None


@dataclass(slots=True, eq=False)
class _Subscriber:
    """One local relay consumer."""

    queue: asyncio.Queue[bytes | None]
    ha_stream_owned: bool = False
    closed: bool = False
    queued_bytes: int = 0


class _FlvBootstrap:
    """Parse FLV tags and retain the minimum safe late-subscriber bootstrap."""

    def __init__(self, *, retain_bootstrap: bool = True) -> None:
        self._retain_bootstrap = retain_bootstrap
        self._buffer = bytearray()
        self.header: bytes | None = None
        self.metadata: bytes | None = None
        self.audio_sequence: bytes | None = None
        self.video_sequence: bytes | None = None
        self.gop: list[bytes] = []
        self.gop_bytes = 0
        self.media_ready = False
        self.video_codec: int | None = None
        self.first_timestamp_ms: int | None = None
        self.latest_timestamp_ms: int | None = None
        self.video_frames = 0
        self.stream_bytes = 0
        self.width: int | None = None
        self.height: int | None = None

    def feed(self, chunk: bytes) -> list[bytes]:
        """Return complete FLV records decoded from an arbitrary byte chunk."""
        self._buffer.extend(chunk)
        records: list[bytes] = []
        if self.header is None:
            if len(self._buffer) < 13:
                return records
            if self._buffer[:3] != b"FLV":
                raise _FlvFormatError(
                    "The mower video source did not return an FLV header."
                )
            header_length = int.from_bytes(self._buffer[5:9], "big")
            total = header_length + 4
            if (
                header_length < 9
                or header_length > _MAX_HEADER_BYTES
                or len(self._buffer) < total
            ):
                if header_length < 9 or header_length > _MAX_HEADER_BYTES:
                    raise _FlvFormatError(
                        "The mower video source returned an invalid FLV header."
                    )
                return records
            self.header = bytes(self._buffer[:total])
            del self._buffer[:total]
            records.append(self.header)

        while len(self._buffer) >= 15:
            data_size = int.from_bytes(self._buffer[1:4], "big")
            if data_size > _MAX_TAG_BYTES:
                raise _FlvFormatError(
                    "The mower video source returned an oversized FLV tag."
                )
            total = 11 + data_size + 4
            if len(self._buffer) < total:
                break
            tag = bytes(self._buffer[:total])
            del self._buffer[:total]
            if self._retain_bootstrap:
                self._observe_tag(tag, data_size)
                self.stream_bytes += len(tag)
            records.append(tag)
        return records

    def _observe_tag(self, tag: bytes, data_size: int) -> None:
        tag_type = tag[0]
        timestamp = int.from_bytes(tag[4:7], "big") | (tag[7] << 24)
        payload = tag[11 : 11 + data_size]
        if tag_type == 18:
            self.metadata = tag
            return
        if tag_type == 8 and payload:
            sound_format = payload[0] >> 4
            if sound_format == 10 and len(payload) > 1 and payload[1] == 0:
                self.audio_sequence = tag
            return
        if tag_type != 9 or not payload:
            if self.gop:
                self._append_gop(tag)
            return

        frame_type = payload[0] >> 4
        codec = payload[0] & 0x0F
        self.video_codec = codec
        packet_type = payload[1] if codec in {7, 12} and len(payload) > 1 else None
        if packet_type == 0:
            self.video_sequence = tag
            if codec == 7 and len(payload) > 11:
                configuration = payload[5:]
                sps_count = configuration[5] & 0x1F
                if sps_count:
                    sps_length = int.from_bytes(configuration[6:8], "big")
                    dimensions = _h264_sps_dimensions(
                        configuration[8 : 8 + sps_length]
                    )
                    if dimensions is not None:
                        self.width, self.height = dimensions
            return

        is_video_media = packet_type == 1 if packet_type is not None else True
        if is_video_media and frame_type == 1:
            self.gop = []
            self.gop_bytes = 0
            if self.first_timestamp_ms is None:
                self.first_timestamp_ms = timestamp
        if self.gop or (is_video_media and frame_type == 1):
            self._append_gop(tag)
        if is_video_media:
            if frame_type == 1 and self.video_sequence is not None:
                self.media_ready = True
            self.video_frames += 1
            self.latest_timestamp_ms = timestamp

    def _append_gop(self, tag: bytes) -> None:
        self.gop.append(tag)
        self.gop_bytes += len(tag)
        required_header_bytes = sum(
            len(part)
            for part in (self.header, self.video_sequence)
            if part is not None
        )
        retained_gop_limit = min(
            _MAX_GOP_BYTES,
            max(
                0,
                _MAX_SUBSCRIBER_QUEUE_BYTES - required_header_bytes,
            ),
        )
        if self.gop_bytes > retained_gop_limit:
            # A partial GOP cannot initialize a late decoder safely. Wait for
            # the next keyframe instead of retaining dependent frames.
            self.gop = []
            self.gop_bytes = 0

    def bootstrap(self) -> bytes:
        """Return decoder headers plus the current keyframe group."""
        required_parts = [
            part
            for part in (self.header, self.video_sequence, *self.gop)
            if part is not None
        ]
        required_bytes = sum(len(part) for part in required_parts)
        if required_bytes > _MAX_SUBSCRIBER_QUEUE_BYTES:
            return b""

        optional_parts: list[bytes] = []
        remaining = _MAX_SUBSCRIBER_QUEUE_BYTES - required_bytes
        for part in (self.metadata, self.audio_sequence):
            if part is not None and len(part) <= remaining:
                optional_parts.append(part)
                remaining -= len(part)
        return b"".join(
            (
                *((self.header,) if self.header is not None else ()),
                *optional_parts,
                *((self.video_sequence,) if self.video_sequence is not None else ()),
                *self.gop,
            )
        )

    def diagnostics(self) -> dict[str, object]:
        """Return non-sensitive stream metadata observed by the relay."""
        duration_ms = (
            self.latest_timestamp_ms - self.first_timestamp_ms
            if self.latest_timestamp_ms is not None
            and self.first_timestamp_ms is not None
            and self.latest_timestamp_ms >= self.first_timestamp_ms
            else 0
        )
        return {
            "flv_header_present": self.header is not None,
            "video_codec_id": self.video_codec,
            "video_width": self.width,
            "video_height": self.height,
            "video_observed_fps": (
                round(self.video_frames * 1000 / duration_ms, 2)
                if duration_ms > 0
                else None
            ),
            "video_observed_bitrate_kbps": (
                round(self.stream_bytes * 8 / duration_ms, 1)
                if duration_ms > 0
                else None
            ),
            "video_observed_frames": self.video_frames,
            "first_video_timestamp_ms": self.first_timestamp_ms,
            "relay_gop_bytes": self.gop_bytes,
        }


class DreameLawnMowerFlvRelay:
    """A lazy, loopback-only FLV fan-out with late-subscriber bootstrapping."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        source_factory: SourceFactory,
        media_ready: MediaReadyCallback,
        failed: FailureCallback,
        idle: IdleCallback,
        should_stay_warm: KeepWarmCallback | None = None,
        subscriber_started: SubscriberStartedCallback | None = None,
        idle_grace: float = _IDLE_GRACE,
        idle_poll_interval: float = _IDLE_POLL_INTERVAL,
    ) -> None:
        self._hass = hass
        self._source_factory = source_factory
        self._media_ready_callback = media_ready
        self._failure_callback = failed
        self._idle_callback = idle
        self._should_stay_warm = should_stay_warm or (lambda: False)
        self._subscriber_started = subscriber_started or (lambda _ha_owned: None)
        self._idle_grace = idle_grace
        self._idle_poll_interval = idle_poll_interval
        self._token = secrets.token_urlsafe(32)
        self._ha_stream_token = secrets.token_urlsafe(32)
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._url: str | None = None
        self._ha_stream_url: str | None = None
        self._subscribers: set[_Subscriber] = set()
        self._listener_lock = asyncio.Lock()
        self._lock = asyncio.Lock()
        self._pump_task: asyncio.Task[None] | None = None
        self._idle_task: asyncio.Task[None] | None = None
        self._idle_stopping = False
        self._idle_ready = asyncio.Event()
        self._idle_ready.set()
        self._closed = False
        self._record_parser = _FlvBootstrap(retain_bootstrap=False)
        self._parser = _FlvBootstrap()
        self._media_callback_sent = False
        self._started_at: float | None = None
        self._first_media_at: float | None = None
        self._last_failure: str | None = None

    @property
    def url(self) -> str | None:
        """Return the stable loopback source URL after setup."""
        return self._url

    @property
    def subscriber_count(self) -> int:
        """Return the number of active local media consumers."""
        return len(self._subscribers)

    @property
    def direct_subscriber_count(self) -> int:
        """Return consumers that are not HA Stream's owned source connection."""
        return sum(
            not subscriber.ha_stream_owned for subscriber in self._subscribers
        )

    @property
    def diagnostics(self) -> dict[str, object]:
        """Return startup and fan-out telemetry safe for entity attributes."""
        now = asyncio.get_running_loop().time()
        return {
            "relay_ready": self._url is not None,
            "relay_consumers": self.subscriber_count,
            "relay_direct_consumers": self.direct_subscriber_count,
            "relay_upstream_active": self._pump_task is not None
            and not self._pump_task.done(),
            "relay_upstream_held_warm": (
                not self._subscribers
                and self._pump_task is not None
                and not self._pump_task.done()
                and self._should_stay_warm()
            ),
            "relay_startup_elapsed_ms": (
                round(((self._first_media_at or now) - self._started_at) * 1000)
                if self._started_at is not None
                else None
            ),
            "relay_first_media_ready": self._first_media_at is not None,
            "relay_last_failure": self._last_failure,
            **self._parser.diagnostics(),
        }

    async def async_start(self) -> str:
        """Bind the dormant relay to an ephemeral loopback port."""
        async with self._listener_lock:
            if self._closed:
                raise RuntimeError("The local mower video relay is closed.")
            if self._url is not None:
                return self._url
            application = web.Application()
            application.router.add_get(
                f"/{self._token}.flv",
                self._async_handle,
                allow_head=False,
            )
            application.router.add_get(
                f"/{self._ha_stream_token}.flv",
                self._async_handle_ha_stream,
                allow_head=False,
            )
            runner = web.AppRunner(
                application,
                access_log=None,
                handler_cancellation=True,
            )
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            server = site._server  # noqa: SLF001 - no public bound-port API.
            if server is None or not server.sockets:
                await runner.cleanup()
                raise RuntimeError(
                    "The local mower video relay did not bind a socket."
                )
            port = int(server.sockets[0].getsockname()[1])
            self._runner = runner
            self._site = site
            self._url = f"http://127.0.0.1:{port}/{self._token}.flv"
            self._ha_stream_url = (
                f"http://127.0.0.1:{port}/{self._ha_stream_token}.flv"
            )
            return self._url

    async def async_start_ha_stream(self) -> str:
        """Return the dedicated source URL owned by Home Assistant Stream."""
        await self.async_start()
        if self._ha_stream_url is None:
            raise RuntimeError("The local mower HA stream relay is unavailable.")
        return self._ha_stream_url

    async def async_close(self) -> None:
        """Close subscribers, upstream playback, and the loopback listener."""
        self._closed = True
        # Wake handlers parked behind an in-progress idle shutdown so they can
        # observe the terminal close instead of reviving the upstream pump.
        self._idle_ready.set()
        await self.async_stop_upstream()
        async with self._listener_lock:
            runner = self._runner
            self._runner = None
            self._site = None
            self._url = None
            self._ha_stream_url = None
        if runner is not None:
            await runner.cleanup()

    async def async_stop_upstream(
        self,
        *,
        expected_task: asyncio.Task[None] | None = None,
    ) -> None:
        """Stop owned relay IO without destroying the stable loopback endpoint."""
        async with self._lock:
            task = self._pump_task
            if (
                expected_task is not None
                and task is not None
                and task is not expected_task
            ):
                return
            self._pump_task = None
            idle_task = self._idle_task
            self._idle_task = None
            subscribers = tuple(self._subscribers)
            self._subscribers.clear()
            for subscriber in subscribers:
                subscriber.closed = True
                self._finish_subscriber(subscriber)
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if (
            idle_task is not None
            and idle_task is not asyncio.current_task()
            and not idle_task.done()
        ):
            idle_task.cancel()
            with suppress(asyncio.CancelledError):
                await idle_task
        self._reset_observation()

    async def _async_handle(self, _request: web.Request) -> web.StreamResponse:
        return await self._async_handle_request(
            _request,
            ha_stream_owned=False,
        )

    async def _async_handle_ha_stream(
        self,
        _request: web.Request,
    ) -> web.StreamResponse:
        """Serve HA Stream's owned source connection on its dedicated URL."""
        return await self._async_handle_request(
            _request,
            ha_stream_owned=True,
        )

    async def _async_handle_request(
        self,
        _request: web.Request,
        *,
        ha_stream_owned: bool,
    ) -> web.StreamResponse:
        subscriber = _Subscriber(
            asyncio.Queue(maxsize=_QUEUE_DEPTH),
            ha_stream_owned=ha_stream_owned,
        )
        while True:
            await self._idle_ready.wait()
            async with self._lock:
                if self._closed:
                    raise web.HTTPServiceUnavailable(
                        text="The local mower video relay is closed."
                    )
                if self._idle_stopping:
                    continue
                idle_task = self._idle_task
                self._idle_task = None
                if idle_task is not None and not idle_task.done():
                    idle_task.cancel()
                bootstrap = self._parser.bootstrap()
                if (
                    bootstrap
                    and len(bootstrap) <= _MAX_SUBSCRIBER_QUEUE_BYTES
                ):
                    subscriber.queue.put_nowait(bootstrap)
                    subscriber.queued_bytes = len(bootstrap)
                self._subscribers.add(subscriber)
                self._subscriber_started(ha_stream_owned)
                if self._pump_task is None or self._pump_task.done():
                    self._last_failure = None
                    self._started_at = asyncio.get_running_loop().time()
                    self._pump_task = create_background_task(
                        self._hass,
                        self._async_pump(),
                        "dreame-lawn-mower-video-relay",
                    )
                break

        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "video/x-flv",
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
        try:
            await response.prepare(_request)
            while True:
                chunk = await subscriber.queue.get()
                if chunk is None:
                    break
                subscriber.queued_bytes = max(
                    0,
                    subscriber.queued_bytes - len(chunk),
                )
                await response.write(chunk)
        except asyncio.CancelledError:
            raise
        except (ConnectionError, RuntimeError):
            pass
        finally:
            async with self._lock:
                subscriber.closed = True
                self._subscribers.discard(subscriber)
                self._schedule_idle_if_empty_locked()
        if response.prepared:
            with suppress(ConnectionError, RuntimeError):
                await response.write_eof()
        return response

    async def _async_pump(self) -> None:
        session: ClientSession = async_get_clientsession(self._hass)
        try:
            source = await self._source_factory()
            if not source:
                raise RuntimeError("The mower video source did not start.")
            timeout = ClientTimeout(
                total=None,
                sock_connect=10,
                sock_read=_UPSTREAM_READ_TIMEOUT,
            )
            async with session.get(source, timeout=timeout) as response:
                response.raise_for_status()
                async with asyncio.timeout(_MEDIA_READY_TIMEOUT) as media_deadline:
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        for record in self._record_parser.feed(chunk):
                            await self._async_broadcast(record)
                        if (
                            self._parser.media_ready
                            and not self._media_callback_sent
                        ):
                            self._media_callback_sent = True
                            self._first_media_at = asyncio.get_running_loop().time()
                            media_deadline.reschedule(None)
                            await self._media_ready_callback(self.diagnostics)
                raise RuntimeError("The mower video source ended.")
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 - propagate a clean local stream end.
            safe_error = _safe_relay_failure(err)
            self._last_failure = safe_error
            _LOGGER.debug("Dreame mower FLV relay stopped: %s", safe_error)
            # Detach the failed pump before cleanup may await native runtime
            # teardown. A reconnecting HA Stream or WebRTC provider can then
            # subscribe to the same stable relay URL without receiving stale
            # decoder bootstrap data or being erased by the old pump's finally
            # block. Its replacement source factory remains serialized by the
            # camera's stream lock until the failed runtime is fully retired.
            async with self._lock:
                if self._pump_task is asyncio.current_task():
                    self._pump_task = None
                    subscribers = tuple(self._subscribers)
                    self._subscribers.clear()
                    for subscriber in subscribers:
                        subscriber.closed = True
                        self._finish_subscriber(subscriber)
                    self._reset_observation()
            await self._failure_callback(safe_error)
        finally:
            async with self._lock:
                if self._pump_task is asyncio.current_task():
                    self._pump_task = None
                    subscribers = tuple(self._subscribers)
                    self._subscribers.clear()
                    for subscriber in subscribers:
                        subscriber.closed = True
                        self._finish_subscriber(subscriber)
                    self._reset_observation()

    async def _async_stop_when_idle(self) -> None:
        """Retire WebRTC or HLS playback after the last local viewer leaves."""
        current_task = asyncio.current_task()
        pump_task: asyncio.Task[None] | None = None
        try:
            await asyncio.sleep(self._idle_grace)
            while True:
                async with self._lock:
                    if self._subscribers or self._pump_task is None:
                        return
                    if not self._should_stay_warm():
                        self._idle_stopping = True
                        self._idle_ready.clear()
                        pump_task = self._pump_task
                        self._pump_task = None
                        break
                await asyncio.sleep(self._idle_poll_interval)
            if pump_task is not None and not pump_task.done():
                pump_task.cancel()
                with suppress(asyncio.CancelledError):
                    await pump_task
            self._reset_observation()
            await self._idle_callback()
        except asyncio.CancelledError:
            raise
        finally:
            async with self._lock:
                self._idle_stopping = False
                if self._idle_task is current_task:
                    self._idle_task = None
                self._idle_ready.set()

    async def _async_broadcast(self, record: bytes) -> None:
        async with self._lock:
            for subscriber in tuple(self._subscribers):
                if subscriber.closed:
                    self._subscribers.discard(subscriber)
                    continue
                if (
                    len(record) > _MAX_SUBSCRIBER_QUEUE_BYTES
                    or subscriber.queued_bytes + len(record)
                    > _MAX_SUBSCRIBER_QUEUE_BYTES
                ):
                    subscriber.closed = True
                    self._subscribers.discard(subscriber)
                    self._finish_subscriber(subscriber)
                    continue
                try:
                    subscriber.queue.put_nowait(record)
                    subscriber.queued_bytes += len(record)
                except asyncio.QueueFull:
                    subscriber.closed = True
                    self._subscribers.discard(subscriber)
                    self._finish_subscriber(subscriber)
            # Commit decoder bootstrap state under the same relay lock as the
            # fan-out. A viewer that joins after this lock receives the record
            # in its bootstrap; existing viewers received it from their queue.
            # No viewer can observe a future record and then receive it again.
            self._parser.feed(record)
            self._schedule_idle_if_empty_locked()

    def _schedule_idle_if_empty_locked(self) -> None:
        """Start upstream retirement when no relay subscriber remains."""
        if (
            not self._subscribers
            and self._pump_task is not None
            and not self._pump_task.done()
            and self._idle_task is None
        ):
            self._idle_task = self._hass.async_create_task(
                self._async_stop_when_idle()
            )

    @staticmethod
    def _finish_subscriber(subscriber: _Subscriber) -> None:
        while True:
            try:
                subscriber.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        subscriber.queued_bytes = 0
        with suppress(asyncio.QueueFull):
            subscriber.queue.put_nowait(None)

    def _reset_observation(self) -> None:
        """Discard decoder bootstrap and per-session timing."""
        self._record_parser = _FlvBootstrap(retain_bootstrap=False)
        self._parser = _FlvBootstrap()
        self._media_callback_sent = False
        self._started_at = None
        self._first_media_at = None
