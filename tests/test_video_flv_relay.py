"""Contract tests for the single-consumer FLV fan-out parser."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import pytest_socket
from aiohttp import ClientSession, TCPConnector, web
from aiohttp.resolver import ThreadedResolver

from custom_components.dreame_lawn_mower.video_flv_relay import (
    DreameLawnMowerFlvRelay,
    _FlvBootstrap,
    _safe_relay_failure,
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


@pytest.mark.asyncio
async def test_relay_fans_out_one_upstream_and_retires_after_last_viewer() -> None:
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
            relay_url = await relay.async_start()
            first = await client.get(relay_url)
            assert await first.content.readexactly(len(initial_media)) == initial_media
            await asyncio.wait_for(media_ready.wait(), timeout=1)

            second = await client.get(relay_url)
            second_bootstrap = await second.content.readexactly(len(initial_media))
            assert second_bootstrap == initial_media
            assert upstream_connections == 1

            first.close()
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
