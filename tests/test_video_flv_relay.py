"""Contract tests for the single-consumer FLV fan-out parser."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import pytest_socket
from aiohttp import ClientSession, TCPConnector, web
from aiohttp.resolver import ThreadedResolver

from custom_components.dreame_lawn_mower.video_flv_relay import (
    _MAX_SPS_BYTES,
    DreameLawnMowerFlvRelay,
    _BitReader,
    _FlvBootstrap,
    _h264_sps_dimensions,
    _safe_relay_failure,
    _Subscriber,
)

FLV_HEADER = b"FLV\x01\x01\x00\x00\x00\x09\x00\x00\x00\x00"


def _tag(tag_type: int, timestamp: int, payload: bytes) -> bytes:
    header = (
        bytes([tag_type])
        + len(payload).to_bytes(3, "big")
        + (timestamp & 0xFFFFFF).to_bytes(3, "big")
        + bytes([(timestamp >> 24) & 0xFF])
        + b"\x00\x00\x00"
    )
    return header + payload + (len(payload) + 11).to_bytes(4, "big")


def test_flv_parser_retains_decoder_bootstrap_and_current_keyframe_group() -> None:
    parser = _FlvBootstrap()
    sequence = _tag(9, 0, b"\x17\x00\x00\x00\x00\x01\x64\x00\x1f")
    keyframe = _tag(9, 40, b"\x17\x01\x00\x00\x00\x00\x00\x00\x01")
    interframe = _tag(9, 80, b"\x27\x01\x00\x00\x00\x00\x00\x00\x02")
    content = FLV_HEADER + sequence + keyframe + interframe

    records = []
    for offset in range(0, len(content), 7):
        records.extend(parser.feed(content[offset : offset + 7]))

    assert b"".join(records) == content
    assert parser.media_ready is True
    assert parser.bootstrap() == FLV_HEADER + sequence + keyframe + interframe
    assert parser.diagnostics()["video_codec_id"] == 7
    assert parser.diagnostics()["video_observed_frames"] == 2


def test_flv_parser_bounds_late_subscriber_bootstrap_by_bytes() -> None:
    parser = _FlvBootstrap()
    parser.header = b"header"
    parser.metadata = b"metadata"
    parser.audio_sequence = b"audio"
    parser.video_sequence = b"sequence"
    parser.gop = [b"keyframe"]
    parser.gop_bytes = len(parser.gop[0])

    with patch(
        "custom_components.dreame_lawn_mower.video_flv_relay."
        "_MAX_SUBSCRIBER_QUEUE_BYTES",
        len(parser.header) + len(parser.video_sequence) + parser.gop_bytes,
    ):
        bootstrap = parser.bootstrap()

    assert bootstrap == parser.header + parser.video_sequence + parser.gop[0]


def test_h264_sps_parser_bounds_payload_and_bit_operations() -> None:
    assert _h264_sps_dimensions(b"\x67" + bytes(_MAX_SPS_BYTES)) is None

    reader = _BitReader(bytes(_MAX_SPS_BYTES))
    with pytest.raises(ValueError, match="parsing budget"):
        reader.ue()


def test_flv_parser_rejects_non_flv_upstream_before_fanout() -> None:
    parser = _FlvBootstrap()

    with pytest.raises(ValueError, match="FLV header"):
        parser.feed(b"not-a-video-response")


def test_relay_failure_does_not_expose_private_source_url() -> None:
    failure = _safe_relay_failure(
        RuntimeError("failed to read http://127.0.0.1:1234/private-token.flv")
    )

    assert failure == "The mower video source failed (RuntimeError)."
    assert "private-token" not in failure


def test_relay_failure_sanitizes_value_error_with_private_source_url() -> None:
    failure = _safe_relay_failure(
        ValueError("Invalid URL http://vendor.invalid/private-token.flv")
    )

    assert failure == "The mower video source failed (ValueError)."
    assert "private-token" not in failure


def test_failed_pump_cleanup_preserves_a_replacement_pump() -> None:
    async def _run() -> tuple[bool, bool]:
        relay = object.__new__(DreameLawnMowerFlvRelay)
        relay._lock = asyncio.Lock()
        replacement = asyncio.create_task(asyncio.Event().wait())
        relay._pump_task = replacement
        failed_pump = asyncio.current_task()
        try:
            await relay.async_stop_upstream(expected_task=failed_pump)
            return relay._pump_task is replacement, replacement.cancelled()
        finally:
            replacement.cancel()
            with suppress(asyncio.CancelledError):
                await replacement

    assert asyncio.run(_run()) == (True, False)


def test_flv_parser_waits_for_sequence_header_and_keyframe_before_ready() -> None:
    parser = _FlvBootstrap()
    interframe = _tag(9, 0, b"\x27\x01\x00\x00\x00\x00\x00\x00\x02")
    keyframe = _tag(9, 40, b"\x17\x01\x00\x00\x00\x00\x00\x00\x01")
    sequence = _tag(9, 80, b"\x17\x00\x00\x00\x00\x01\x64\x00\x1f")

    parser.feed(FLV_HEADER + interframe + keyframe)
    assert parser.media_ready is False

    parser.feed(sequence)
    assert parser.media_ready is False

    parser.feed(keyframe)
    assert parser.media_ready is True


async def _relay_fans_out_one_upstream_and_retires_after_last_viewer() -> None:
    pytest_socket.enable_socket()
    upstream_connections = 0
    release_upstream = asyncio.Event()
    media_ready = asyncio.Event()
    relay_idle = asyncio.Event()
    failures: list[str] = []
    sequence = _tag(9, 0, b"\x17\x00\x00\x00\x00\x01\x64\x00\x1f")
    keyframe = _tag(9, 0, b"\x17\x01\x00\x00\x00\x00\x00\x00\x01")
    interframe = _tag(9, 40, b"\x27\x01\x00\x00\x00\x00\x00\x00\x02")
    initial_media = FLV_HEADER + sequence + keyframe

    async def _source_handler(request: web.Request) -> web.StreamResponse:
        nonlocal upstream_connections
        upstream_connections += 1
        response = web.StreamResponse(headers={"Content-Type": "video/x-flv"})
        await response.prepare(request)
        try:
            await response.write(initial_media)
            while not release_upstream.is_set():
                await asyncio.sleep(0.01)
                await response.write(interframe)
        except ConnectionError:
            pass
        return response

    source_application = web.Application()
    source_application.router.add_get("/source.flv", _source_handler)
    source_runner = web.AppRunner(source_application)
    await source_runner.setup()
    source_site = web.TCPSite(source_runner, "127.0.0.1", 0)
    await source_site.start()
    source_server = source_site._server  # noqa: SLF001
    assert source_server is not None
    source_port = int(source_server.sockets[0].getsockname()[1])
    source_url = f"http://127.0.0.1:{source_port}/source.flv"

    client = ClientSession(
        connector=TCPConnector(resolver=ThreadedResolver()),
    )
    hass = SimpleNamespace(async_create_task=asyncio.create_task)

    async def _media_ready(_diagnostics: dict[str, object]) -> None:
        media_ready.set()

    async def _failed(error: str) -> None:
        failures.append(error)

    async def _idle() -> None:
        relay_idle.set()

    relay = DreameLawnMowerFlvRelay(
        hass,
        source_factory=lambda: asyncio.sleep(0, result=source_url),
        media_ready=_media_ready,
        failed=_failed,
        idle=_idle,
        idle_grace=0.02,
    )
    try:
        with patch(
            "custom_components.dreame_lawn_mower.video_flv_relay."
            "async_get_clientsession",
            return_value=client,
        ):
            relay_urls = await asyncio.gather(
                *(relay.async_start() for _index in range(5))
            )
            assert len(set(relay_urls)) == 1
            relay_url = relay_urls[0]
            ha_stream_url = await relay.async_start_ha_stream()
            assert ha_stream_url != relay_url

            head = await client.head(relay_url)
            assert head.status == 405
            assert upstream_connections == 0

            first = await client.get(relay_url)
            assert await first.content.readexactly(len(initial_media)) == initial_media
            await asyncio.wait_for(media_ready.wait(), timeout=1)

            second = await client.get(ha_stream_url)
            second_bootstrap = await second.content.readexactly(len(initial_media))
            assert second_bootstrap == initial_media
            assert upstream_connections == 1
            assert relay.subscriber_count == 2
            assert relay.direct_subscriber_count == 1

            first.close()
            for _attempt in range(100):
                if relay.direct_subscriber_count == 0:
                    break
                await asyncio.sleep(0.01)
            assert relay.subscriber_count == 1
            assert relay.direct_subscriber_count == 0
            second.close()
            await asyncio.wait_for(relay_idle.wait(), timeout=1)

        assert failures == []
        assert relay.subscriber_count == 0
        assert relay.diagnostics["relay_upstream_active"] is False
    finally:
        release_upstream.set()
        await relay.async_close()
        await client.close()
        await source_runner.cleanup()


def test_relay_fans_out_one_upstream_and_retires_after_last_viewer() -> None:
    asyncio.run(_relay_fans_out_one_upstream_and_retires_after_last_viewer())


def test_relay_reconnect_uses_fresh_bootstrap_on_the_stable_url() -> None:
    async def _run() -> tuple[int, list[str], int, bool]:
        pytest_socket.enable_socket()
        upstream_connections = 0
        release_first = asyncio.Event()
        release_second = asyncio.Event()
        first_sequence = _tag(9, 0, b"\x17\x00\x00\x00\x00\x01\x64\x00\x1f")
        first_keyframe = _tag(9, 0, b"\x17\x01\x00\x00\x00\x00\x00\x00\x01")
        first_media = FLV_HEADER + first_sequence + first_keyframe
        second_sequence = _tag(9, 0, b"\x17\x00\x00\x00\x00\x01\x42\x00\x1f")
        second_keyframe = _tag(9, 0, b"\x17\x01\x00\x00\x00\x00\x00\x00\x09")
        second_media = FLV_HEADER + second_sequence + second_keyframe

        async def _source(request: web.Request) -> web.StreamResponse:
            nonlocal upstream_connections
            upstream_connections += 1
            response = web.StreamResponse(headers={"Content-Type": "video/x-flv"})
            await response.prepare(request)
            if upstream_connections == 1:
                await response.write(first_media)
                await release_first.wait()
                return response
            await response.write(second_media)
            await release_second.wait()
            return response

        application = web.Application()
        application.router.add_get("/source.flv", _source)
        runner = web.AppRunner(application)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        server = site._server  # noqa: SLF001
        assert server is not None
        port = int(server.sockets[0].getsockname()[1])
        source_url = f"http://127.0.0.1:{port}/source.flv"
        client = ClientSession(
            connector=TCPConnector(resolver=ThreadedResolver()),
        )
        failures: list[str] = []
        media_ready_calls = 0

        async def _media_ready(_diagnostics: dict[str, object]) -> None:
            nonlocal media_ready_calls
            media_ready_calls += 1

        relay = DreameLawnMowerFlvRelay(
            SimpleNamespace(async_create_task=asyncio.create_task),
            source_factory=lambda: asyncio.sleep(0, result=source_url),
            media_ready=_media_ready,
            failed=lambda error: asyncio.sleep(0, result=failures.append(error)),
            idle=lambda: asyncio.sleep(0),
        )
        first_response = second_response = None
        try:
            with patch(
                "custom_components.dreame_lawn_mower.video_flv_relay."
                "async_get_clientsession",
                return_value=client,
            ):
                relay_url = await relay.async_start()
                first_response = await client.get(relay_url)
                assert (
                    await first_response.content.readexactly(len(first_media))
                    == first_media
                )
                release_first.set()
                assert await first_response.content.read() == b""
                for _attempt in range(100):
                    if failures:
                        break
                    await asyncio.sleep(0.01)

                second_response = await client.get(relay_url)
                fresh_media = await second_response.content.readexactly(
                    len(second_media)
                )
                for _attempt in range(100):
                    if media_ready_calls == 2:
                        break
                    await asyncio.sleep(0.01)
                return (
                    upstream_connections,
                    failures,
                    media_ready_calls,
                    fresh_media == second_media,
                )
        finally:
            release_first.set()
            release_second.set()
            if first_response is not None:
                first_response.close()
            if second_response is not None:
                second_response.close()
            await relay.async_close()
            await client.close()
            await runner.cleanup()

    assert asyncio.run(_run()) == (
        2,
        ["The mower video source ended."],
        2,
        True,
    )


def test_relay_late_subscriber_does_not_duplicate_future_chunk_records() -> None:
    async def _run() -> bytes:
        pytest_socket.enable_socket()
        pause_after_sequence = asyncio.Event()
        resume_broadcast = asyncio.Event()
        close_source = asyncio.Event()
        sequence = _tag(9, 0, b"\x17\x00\x00\x00\x00\x01\x64\x00\x1f")
        keyframe = _tag(9, 0, b"\x17\x01\x00\x00\x00\x00\x00\x00\x01")
        interframe = _tag(9, 40, b"\x27\x01\x00\x00\x00\x00\x00\x00\x02")
        content = FLV_HEADER + sequence + keyframe + interframe

        async def _source(request: web.Request) -> web.StreamResponse:
            response = web.StreamResponse(headers={"Content-Type": "video/x-flv"})
            await response.prepare(request)
            await response.write(content)
            await close_source.wait()
            return response

        application = web.Application()
        application.router.add_get("/source.flv", _source)
        runner = web.AppRunner(application)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        server = site._server  # noqa: SLF001
        assert server is not None
        port = int(server.sockets[0].getsockname()[1])
        client = ClientSession(
            connector=TCPConnector(resolver=ThreadedResolver()),
        )
        relay = DreameLawnMowerFlvRelay(
            SimpleNamespace(async_create_task=asyncio.create_task),
            source_factory=lambda: asyncio.sleep(
                0,
                result=f"http://127.0.0.1:{port}/source.flv",
            ),
            media_ready=lambda _diagnostics: asyncio.sleep(0),
            failed=lambda _error: asyncio.sleep(0),
            idle=lambda: asyncio.sleep(0),
        )
        original_broadcast = relay._async_broadcast  # noqa: SLF001
        broadcast_count = 0

        async def _broadcast_with_pause(record: bytes) -> None:
            nonlocal broadcast_count
            broadcast_count += 1
            await original_broadcast(record)
            if broadcast_count == 2:
                pause_after_sequence.set()
                await resume_broadcast.wait()

        relay._async_broadcast = _broadcast_with_pause  # type: ignore[method-assign]  # noqa: SLF001
        first_response = second_response = None
        try:
            with patch(
                "custom_components.dreame_lawn_mower.video_flv_relay."
                "async_get_clientsession",
                return_value=client,
            ):
                relay_url = await relay.async_start()
                first_response = await client.get(relay_url)
                await asyncio.wait_for(pause_after_sequence.wait(), timeout=1)
                assert (
                    await first_response.content.readexactly(
                        len(FLV_HEADER + sequence)
                    )
                    == FLV_HEADER + sequence
                )

                second_response = await client.get(relay_url)
                late_bootstrap = await second_response.content.readexactly(
                    len(FLV_HEADER + sequence)
                )
                resume_broadcast.set()
                late_tail = await second_response.content.readexactly(
                    len(keyframe + interframe)
                )
                return late_bootstrap + late_tail
        finally:
            resume_broadcast.set()
            close_source.set()
            if first_response is not None:
                first_response.close()
            if second_response is not None:
                second_response.close()
            await relay.async_close()
            await client.close()
            await runner.cleanup()

    assert asyncio.run(_run()) == (
        FLV_HEADER
        + _tag(9, 0, b"\x17\x00\x00\x00\x00\x01\x64\x00\x1f")
        + _tag(9, 0, b"\x17\x01\x00\x00\x00\x00\x00\x00\x01")
        + _tag(9, 40, b"\x27\x01\x00\x00\x00\x00\x00\x00\x02")
    )


def test_relay_keeps_one_upstream_through_a_brief_wifi_stall() -> None:
    async def _run() -> tuple[int, list[str], bytes]:
        pytest_socket.enable_socket()
        upstream_connections = 0
        resume_source = asyncio.Event()
        close_source = asyncio.Event()
        sequence = _tag(9, 0, b"\x17\x00\x00\x00\x00\x01\x64\x00\x1f")
        keyframe = _tag(9, 0, b"\x17\x01\x00\x00\x00\x00\x00\x00\x01")
        interframe = _tag(9, 40, b"\x27\x01\x00\x00\x00\x00\x00\x00\x02")
        initial_media = FLV_HEADER + sequence + keyframe

        async def _source(request: web.Request) -> web.StreamResponse:
            nonlocal upstream_connections
            upstream_connections += 1
            response = web.StreamResponse(headers={"Content-Type": "video/x-flv"})
            await response.prepare(request)
            await response.write(initial_media)
            await resume_source.wait()
            await response.write(interframe)
            await close_source.wait()
            return response

        application = web.Application()
        application.router.add_get("/source.flv", _source)
        runner = web.AppRunner(application)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        server = site._server  # noqa: SLF001
        assert server is not None
        port = int(server.sockets[0].getsockname()[1])
        client = ClientSession(
            connector=TCPConnector(resolver=ThreadedResolver()),
        )
        failures: list[str] = []
        relay = DreameLawnMowerFlvRelay(
            SimpleNamespace(async_create_task=asyncio.create_task),
            source_factory=lambda: asyncio.sleep(
                0,
                result=f"http://127.0.0.1:{port}/source.flv",
            ),
            media_ready=lambda _diagnostics: asyncio.sleep(0),
            failed=lambda error: asyncio.sleep(0, result=failures.append(error)),
            idle=lambda: asyncio.sleep(0),
        )
        response = None
        try:
            with (
                patch(
                    "custom_components.dreame_lawn_mower.video_flv_relay."
                    "async_get_clientsession",
                    return_value=client,
                ),
                patch(
                    "custom_components.dreame_lawn_mower.video_flv_relay."
                    "_UPSTREAM_READ_TIMEOUT",
                    0.5,
                ),
            ):
                response = await client.get(await relay.async_start())
                assert (
                    await response.content.readexactly(len(initial_media))
                    == initial_media
                )
                await asyncio.sleep(0.1)
                resume_source.set()
                resumed_media = await response.content.readexactly(len(interframe))
                return upstream_connections, failures, resumed_media
        finally:
            resume_source.set()
            close_source.set()
            if response is not None:
                response.close()
            await relay.async_close()
            await client.close()
            await runner.cleanup()

    assert asyncio.run(_run()) == (1, [], _tag(
        9,
        40,
        b"\x27\x01\x00\x00\x00\x00\x00\x00\x02",
    ))


def test_relay_retires_cold_start_when_waiting_viewer_disconnects() -> None:
    async def _run() -> tuple[int, bool]:
        pytest_socket.enable_socket()
        source_started = asyncio.Event()
        idle = asyncio.Event()

        async def _source_factory() -> str:
            source_started.set()
            await asyncio.Future()
            raise AssertionError("The cancelled source factory must not return.")

        relay = DreameLawnMowerFlvRelay(
            SimpleNamespace(async_create_task=asyncio.create_task),
            source_factory=_source_factory,
            media_ready=lambda _diagnostics: asyncio.sleep(0),
            failed=lambda _error: asyncio.sleep(0),
            idle=lambda: asyncio.sleep(0, result=idle.set()),
            idle_grace=0.01,
        )
        client = ClientSession(
            connector=TCPConnector(resolver=ThreadedResolver()),
        )
        try:
            with patch(
                "custom_components.dreame_lawn_mower.video_flv_relay."
                "async_get_clientsession",
                return_value=client,
            ):
                relay_url = await relay.async_start()
                response = await client.get(relay_url)
                await asyncio.wait_for(source_started.wait(), timeout=1)
                assert relay.subscriber_count == 1

                response.close()
                await asyncio.wait_for(idle.wait(), timeout=1)

            return relay.subscriber_count, relay.diagnostics[
                "relay_upstream_active"
            ]
        finally:
            await relay.async_close()
            await client.close()

    assert asyncio.run(_run()) == (0, False)


def test_relay_fails_stream_that_never_reaches_decoder_ready_media() -> None:
    async def _run() -> tuple[list[str], bool]:
        pytest_socket.enable_socket()
        release = asyncio.Event()

        async def _source(request: web.Request) -> web.StreamResponse:
            response = web.StreamResponse(headers={"Content-Type": "video/x-flv"})
            await response.prepare(request)
            await response.write(FLV_HEADER)
            await release.wait()
            return response

        application = web.Application()
        application.router.add_get("/source.flv", _source)
        runner = web.AppRunner(application)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        server = site._server  # noqa: SLF001
        assert server is not None
        port = int(server.sockets[0].getsockname()[1])
        client = ClientSession(
            connector=TCPConnector(resolver=ThreadedResolver()),
        )
        failures: list[str] = []
        relay = DreameLawnMowerFlvRelay(
            SimpleNamespace(async_create_task=asyncio.create_task),
            source_factory=lambda: asyncio.sleep(
                0,
                result=f"http://127.0.0.1:{port}/source.flv",
            ),
            media_ready=lambda _diagnostics: asyncio.sleep(0),
            failed=lambda error: asyncio.sleep(0, result=failures.append(error)),
            idle=lambda: asyncio.sleep(0),
        )
        response: object | None = None
        try:
            with (
                patch(
                    "custom_components.dreame_lawn_mower.video_flv_relay."
                    "async_get_clientsession",
                    return_value=client,
                ),
                patch(
                    "custom_components.dreame_lawn_mower.video_flv_relay."
                    "_MEDIA_READY_TIMEOUT",
                    0.02,
                ),
            ):
                response = await client.get(await relay.async_start())
                assert await response.content.readexactly(len(FLV_HEADER)) == FLV_HEADER
                for _attempt in range(100):
                    if failures:
                        break
                    await asyncio.sleep(0.01)
            return failures, relay.diagnostics["relay_first_media_ready"]
        finally:
            release.set()
            if response is not None:
                response.close()
            await relay.async_close()
            await client.close()
            await runner.cleanup()

    failures, media_ready = asyncio.run(_run())

    assert failures == ["The mower video source timed out."]
    assert media_ready is False


def test_relay_disconnects_subscriber_before_byte_queue_grows_unbounded() -> None:
    async def _run() -> tuple[bool, int, int]:
        hass = SimpleNamespace(async_create_task=asyncio.create_task)
        relay = DreameLawnMowerFlvRelay(
            hass,
            source_factory=lambda: asyncio.sleep(0, result=None),
            media_ready=lambda _diagnostics: asyncio.sleep(0),
            failed=lambda _error: asyncio.sleep(0),
            idle=lambda: asyncio.sleep(0),
        )
        subscriber = _Subscriber(asyncio.Queue(maxsize=96))
        relay._subscribers.add(subscriber)
        with patch(
            "custom_components.dreame_lawn_mower.video_flv_relay."
            "_MAX_SUBSCRIBER_QUEUE_BYTES",
            10,
        ):
            await relay._async_broadcast(b"123456")
            assert subscriber.queued_bytes == 6
            await relay._async_broadcast(b"abcdef")
        terminal = await subscriber.queue.get()
        return subscriber.closed, subscriber.queued_bytes, terminal is None

    assert asyncio.run(_run()) == (True, 0, True)


def test_relay_retires_upstream_when_backpressure_evicts_last_subscriber() -> None:
    async def _run() -> tuple[bool, bool]:
        idle = asyncio.Event()
        release_pump = asyncio.Event()
        relay = DreameLawnMowerFlvRelay(
            SimpleNamespace(async_create_task=asyncio.create_task),
            source_factory=lambda: asyncio.sleep(0, result=None),
            media_ready=lambda _diagnostics: asyncio.sleep(0),
            failed=lambda _error: asyncio.sleep(0),
            idle=lambda: asyncio.sleep(0, result=idle.set()),
            idle_grace=0,
        )
        subscriber = _Subscriber(asyncio.Queue(maxsize=1))

        async def _pump() -> None:
            await release_pump.wait()

        relay._subscribers.add(subscriber)
        relay._pump_task = asyncio.create_task(_pump())
        try:
            with patch(
                "custom_components.dreame_lawn_mower.video_flv_relay."
                "_MAX_SUBSCRIBER_QUEUE_BYTES",
                1,
            ):
                await relay._async_broadcast(b"too-large")
            await asyncio.wait_for(idle.wait(), timeout=1)
            return subscriber.closed, relay._pump_task is None
        finally:
            release_pump.set()
            await relay.async_close()

    assert asyncio.run(_run()) == (True, True)


def test_old_pump_teardown_preserves_replacement_pump_and_subscribers() -> None:
    async def _run() -> tuple[bool, bool, bool]:
        cleanup_started = asyncio.Event()
        release_cleanup = asyncio.Event()
        release_replacement = asyncio.Event()
        relay: DreameLawnMowerFlvRelay

        async def _failed(_error: str) -> None:
            await relay.async_stop_upstream()
            cleanup_started.set()
            await release_cleanup.wait()

        relay = DreameLawnMowerFlvRelay(
            SimpleNamespace(async_create_task=asyncio.create_task),
            source_factory=lambda: asyncio.sleep(0, result=None),
            media_ready=lambda _diagnostics: asyncio.sleep(0),
            failed=_failed,
            idle=lambda: asyncio.sleep(0),
        )

        async def _replacement_pump() -> None:
            await release_replacement.wait()

        with patch(
            "custom_components.dreame_lawn_mower.video_flv_relay."
            "async_get_clientsession",
            return_value=object(),
        ):
            old_pump = asyncio.create_task(relay._async_pump())
            relay._pump_task = old_pump
            try:
                await asyncio.wait_for(cleanup_started.wait(), timeout=1)
                replacement = asyncio.create_task(_replacement_pump())
                subscriber = _Subscriber(asyncio.Queue(maxsize=1))
                relay._pump_task = replacement
                relay._subscribers.add(subscriber)

                release_cleanup.set()
                await old_pump
                return (
                    relay._pump_task is replacement,
                    subscriber in relay._subscribers,
                    not subscriber.closed,
                )
            finally:
                release_cleanup.set()
                release_replacement.set()
                await relay.async_stop_upstream()

    assert asyncio.run(_run()) == (True, True, True)


def test_relay_close_rejects_waiting_subscriber_without_restarting_pump() -> None:
    async def _run() -> tuple[int, int, bool]:
        source_starts = 0

        async def _source_factory() -> str:
            nonlocal source_starts
            source_starts += 1
            return "http://127.0.0.1/source.flv"

        async def _callback(*_args: object) -> None:
            return None

        relay = DreameLawnMowerFlvRelay(
            SimpleNamespace(async_create_task=asyncio.create_task),
            source_factory=_source_factory,
            media_ready=_callback,
            failed=_callback,
            idle=_callback,
        )
        await relay._lock.acquire()  # noqa: SLF001 - force the close/GET race.
        handler = asyncio.create_task(
            relay._async_handle_request(  # noqa: SLF001 - lifecycle contract.
                SimpleNamespace(),
                ha_stream_owned=False,
            )
        )
        try:
            await asyncio.sleep(0)
            closing = asyncio.create_task(relay.async_close())
            await asyncio.sleep(0)
            assert relay._closed is True  # noqa: SLF001 - terminal fence.
        finally:
            relay._lock.release()  # noqa: SLF001

        with pytest.raises(web.HTTPServiceUnavailable) as closed:
            await asyncio.wait_for(handler, timeout=1)
        assert closed.value.text == "The local mower video relay is closed."
        await asyncio.wait_for(closing, timeout=1)
        with pytest.raises(RuntimeError, match="relay is closed"):
            await relay.async_start()
        return source_starts, relay.subscriber_count, relay._pump_task is None  # noqa: SLF001

    assert asyncio.run(_run()) == (0, 0, True)


def test_relay_removes_subscriber_when_response_preparation_fails() -> None:
    async def _run() -> tuple[int, bool]:
        release_source = asyncio.Event()
        idle = asyncio.Event()

        async def _source() -> None:
            await release_source.wait()
            return None

        relay = DreameLawnMowerFlvRelay(
            SimpleNamespace(async_create_task=asyncio.create_task),
            source_factory=_source,
            media_ready=lambda _diagnostics: asyncio.sleep(0),
            failed=lambda _error: asyncio.sleep(0),
            idle=lambda: asyncio.sleep(0, result=idle.set()),
            idle_grace=0.01,
        )
        try:
            with patch.object(
                web.StreamResponse,
                "prepare",
                side_effect=ConnectionResetError("client disconnected"),
            ):
                await relay._async_handle(SimpleNamespace())
            await asyncio.wait_for(idle.wait(), timeout=1)
            return relay.subscriber_count, relay.diagnostics[
                "relay_upstream_active"
            ]
        finally:
            release_source.set()
            await relay.async_close()

    assert asyncio.run(_run()) == (0, False)


def test_relay_cancels_media_deadline_before_ready_callback() -> None:
    async def _run() -> tuple[list[str], bool]:
        pytest_socket.enable_socket()
        release = asyncio.Event()
        callback_finished = asyncio.Event()
        sequence = _tag(9, 0, b"\x17\x00\x00\x00\x00\x01\x64\x00\x1f")
        keyframe = _tag(9, 0, b"\x17\x01\x00\x00\x00\x00\x00\x00\x01")

        async def _source(request: web.Request) -> web.StreamResponse:
            response = web.StreamResponse(headers={"Content-Type": "video/x-flv"})
            await response.prepare(request)
            await response.write(FLV_HEADER + sequence + keyframe)
            await release.wait()
            return response

        application = web.Application()
        application.router.add_get("/source.flv", _source)
        runner = web.AppRunner(application)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        server = site._server  # noqa: SLF001
        assert server is not None
        port = int(server.sockets[0].getsockname()[1])
        client = ClientSession(
            connector=TCPConnector(resolver=ThreadedResolver()),
        )
        failures: list[str] = []

        async def _ready(_diagnostics: dict[str, object]) -> None:
            await asyncio.sleep(0.04)
            callback_finished.set()

        relay = DreameLawnMowerFlvRelay(
            SimpleNamespace(async_create_task=asyncio.create_task),
            source_factory=lambda: asyncio.sleep(
                0,
                result=f"http://127.0.0.1:{port}/source.flv",
            ),
            media_ready=_ready,
            failed=lambda error: asyncio.sleep(0, result=failures.append(error)),
            idle=lambda: asyncio.sleep(0),
        )
        response: object | None = None
        try:
            with (
                patch(
                    "custom_components.dreame_lawn_mower.video_flv_relay."
                    "async_get_clientsession",
                    return_value=client,
                ),
                patch(
                    "custom_components.dreame_lawn_mower.video_flv_relay."
                    "_MEDIA_READY_TIMEOUT",
                    0.01,
                ),
            ):
                response = await client.get(await relay.async_start())
                await response.content.readexactly(
                    len(FLV_HEADER + sequence + keyframe)
                )
                await asyncio.wait_for(callback_finished.wait(), timeout=1)
                return failures, relay.diagnostics["relay_first_media_ready"]
        finally:
            release.set()
            if response is not None:
                response.close()
            await relay.async_close()
            await client.close()
            await runner.cleanup()

    assert asyncio.run(_run()) == ([], True)
