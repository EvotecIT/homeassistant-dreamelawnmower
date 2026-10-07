"""Native frame application preserves response results and captured ownership."""

import asyncio
import base64
import hashlib
import json
import time
import zlib
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp import ClientSession, web
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    client_map_application,
    cloud_session,
    map_manager,
    public_download,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_types import (
    MapFrameType,
)

from .test_async_app_commands import client_for
from .test_async_cloud_session import cloud_strings, login_response, server


def _encoded_map_frame(timestamp: int | None, frame_id: int = 10) -> str:
    header = bytearray(map_manager.DreameMowerMapDecoder.HEADER_SIZE)
    header[0:2] = (7).to_bytes(2, "little", signed=True)
    header[2:4] = frame_id.to_bytes(2, "little", signed=True)
    header[4] = MapFrameType.I.value
    metadata = {} if timestamp is None else {"timestamp_ms": timestamp}
    return base64.b64encode(
        zlib.compress(bytes(header) + json.dumps(metadata).encode())
    ).decode()


def _encoded_p_frame(timestamp, frame_id):
    data = bytearray(
        zlib.decompress(base64.b64decode(_encoded_map_frame(timestamp, frame_id)))
    )
    data[4] = MapFrameType.P.value
    return base64.b64encode(zlib.compress(data)).decode()


def test_received_map_properties_apply_inline_frame_through_native_owner(monkeypatch):
    async def scenario():
        async with ClientSession() as session:
            client = client_for(session)
            device = client._ensure_device()
            manager = device._map_manager
            manager._ready = True
            observed = []
            monkeypatch.setattr(
                device,
                "_property_changed",
                lambda: observed.append(manager._current_frame_id),
            )
            manager.listen(device._map_changed, device._property_changed)
            application = client_map_application.NativeMapApplication(
                client,
                device,
                manager,
                deadline=time.monotonic() + 5,
            )
            try:
                await application.receive_properties(
                    [{"piid": 1, "value": _encoded_map_frame(None, 2)}]
                )
                assert observed == [2]
                assert manager._current_frame_id == 2
                assert manager._map_data.timestamp_ms is not None
                assert not client._cloud_read_tasks
                await application.receive_properties([{"piid": 1, "value": "x"}])
                assert observed == [2]
            finally:
                await client.async_close()

    asyncio.run(scenario())


@pytest.mark.parametrize("predecessor_queued", [False, True])
def test_cloud_object_waits_for_missing_predecessor(monkeypatch, predecessor_queued):
    sign = AsyncMock(return_value="https://example.invalid/frame")
    download = AsyncMock(return_value=_encoded_p_frame(1700000000003, 3).encode())
    monkeypatch.setattr(
        cloud_session.DreameCloudSession, "async_get_interim_file_url", sign
    )
    monkeypatch.setattr(
        cloud_session.DreameCloudSession, "async_get_public_file", download
    )

    async def scenario():
        async with ClientSession() as session:
            client = client_for(session)
            device = client._ensure_device()
            manager = device._map_manager
            manager._ready = True
            observed = []
            monkeypatch.setattr(
                device,
                "_property_changed",
                lambda: observed.append(manager._current_frame_id),
            )
            manager.listen(device._map_changed, device._property_changed)
            application = client_map_application.NativeMapApplication(
                client,
                device,
                manager,
                deadline=time.monotonic() + 5,
            )
            try:
                assert await application.raw_frame(
                    _encoded_map_frame(1700000000001, 1), None
                )
                observed.clear()
                if predecessor_queued:
                    manager._queue_partial_map(
                        manager._decode_map_partial(_encoded_p_frame(1700000000002, 2))
                    )
                assert await application.cloud_object("future-frame", None) is None
                if not predecessor_queued:
                    assert manager._current_frame_id == 1
                    assert observed == []
                    assert await application.raw_frame(
                        _encoded_p_frame(1700000000002, 2), None
                    )
                assert manager._current_frame_id == 3
                assert observed == [2, 3]
            finally:
                await client.async_close()

    asyncio.run(scenario())


@pytest.mark.parametrize("start_time", [None, 0, 1000])
def test_current_map_preserves_start_time_and_empty_result(monkeypatch, start_time):
    strings = cloud_strings("dreame")
    captured = []

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        captured.append(
            json.loads((await request.json())["data"]["params"]["in"][0]["value"])
        )
        return web.json_response(
            {
                "code": 0,
                "data": {
                    "result": {
                        "code": 0,
                        "out": [{"piid": 5, "value": 12000}],
                    }
                },
            }
        )

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            device = client._ensure_device()
            manager = device._map_manager
            manager._map_request_count = 7
            application = client_map_application.NativeMapApplication(
                client,
                device,
                manager,
                deadline=time.monotonic() + 5,
            )
            try:
                assert await application.request_current(start_time) is False
                expected = {"req_type": 1, "frame_type": "I", "force_type": 1}
                if start_time:
                    expected["time"] = start_time
                assert captured == [expected]
                assert manager._last_robot_time == 12000
                assert manager._map_request_count == (1 if start_time is None else 7)
                assert manager._map_data is None
            finally:
                await client.async_close()

    asyncio.run(scenario())


def test_application_deadline_drains_reader_waiting_for_state_lock():
    async def scenario():
        async with ClientSession() as session:
            client = client_for(session)
            device = client._ensure_device()
            manager = device._map_manager
            application = client_map_application.NativeMapApplication(
                client,
                device,
                manager,
                deadline=time.monotonic() + 0.1,
            )
            loop = asyncio.get_running_loop()
            previous_handler = loop.get_exception_handler()
            errors = []
            loop.set_exception_handler(lambda _loop, context: errors.append(context))
            device._state_lock.acquire()
            try:
                with pytest.raises(
                    client_map_application.DreameLawnMowerConnectionError
                ):
                    await asyncio.wait_for(application.request_next(7, 2), 2)
                assert not client._cloud_read_tasks
                assert manager._request_queue == {}
                assert manager._current_frame_id is None
                assert errors == []
            finally:
                device._state_lock.release()
                await client.async_close()
                loop.set_exception_handler(previous_handler)

    asyncio.run(scenario())


@pytest.mark.parametrize("account", ["dreame", "mova"])
@pytest.mark.parametrize(
    "outcome", ["success", "empty", "rejected", "replace", "cancel", "vslam"]
)
def test_next_frame_response_and_owner_lifetime(monkeypatch, account, outcome):
    strings = cloud_strings(account)

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        requests = []

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            requests.append((await request.json())["data"])
            started.set()
            await release.wait()
            result = (
                None
                if outcome == "empty"
                else {
                    "code": 1 if outcome == "rejected" else 0,
                    "out": [],
                }
            )
            return web.json_response({"code": 0, "data": {"result": result}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session, account)
            device = client._ensure_device()
            protocol = Mock()
            manager = map_manager.DreameMapMowerMapManager(protocol)
            manager._vslam_map = outcome == "vslam"
            device._map_manager = manager
            application = client_map_application.NativeMapApplication(
                client,
                device,
                manager,
                deadline=time.monotonic() + 10,
            )
            task = asyncio.create_task(application.request_next(1, 2))
            try:
                await asyncio.wait_for(started.wait(), 3)
                assert await application.request_next(1, 2) is None
                if outcome == "replace":
                    device._map_manager = map_manager.DreameMapMowerMapManager(Mock())
                elif outcome == "cancel":
                    task.cancel()
                release.set()
                if outcome == "replace":
                    with pytest.raises(
                        client_map_application.DreameLawnMowerConnectionError
                    ):
                        await task
                elif outcome == "cancel":
                    with pytest.raises(asyncio.CancelledError):
                        await task
                else:
                    assert await task is (outcome == "success")
                assert len(requests) == (2 if outcome == "vslam" else 1)
                if outcome == "vslam":
                    assert json.loads(requests[1]["params"]["in"][0]["value"]) == {
                        "req_type": 1,
                        "frame_type": "I",
                        "force_type": 1,
                    }
                assert manager._request_queue == {}
                protocol.action.assert_not_called()
            finally:
                release.set()
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("account", ["dreame", "mova"])
@pytest.mark.parametrize("download_fails", [False, True])
def test_downloaded_encrypted_frame_precedes_inline_frame(
    monkeypatch, account, download_fails
):
    strings = cloud_strings(account)
    timestamp = 1700000000000
    key, iv = "fixture-map-key", "0123456789abcdef"
    padder = PKCS7(128).padder()
    compressed = base64.b64decode(_encoded_map_frame(None, 2))
    padded = padder.update(compressed) + padder.finalize()
    encryptor = Cipher(
        algorithms.AES(hashlib.sha256(key.encode()).hexdigest()[:32].encode()),
        modes.CBC(iv.encode()),
    ).encryptor()
    payload = base64.b64encode(encryptor.update(padded) + encryptor.finalize())
    sign = AsyncMock(return_value="https://example.invalid/map")
    download = AsyncMock(return_value=payload)
    if download_fails:
        download.side_effect = public_download.PublicDownloadError(
            "Object unavailable", reason="http", status=404
        )
    monkeypatch.setattr(
        cloud_session.DreameCloudSession, "async_get_interim_file_url", sign
    )
    monkeypatch.setattr(
        cloud_session.DreameCloudSession, "async_get_public_file", download
    )

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        return web.json_response(
            {
                "code": 0,
                "data": {
                    "result": {
                        "code": 0,
                        "out": [
                            {"piid": 3, "value": f"frame-object,{key}"},
                            {"piid": 1, "value": _encoded_p_frame(timestamp + 1, 3)},
                            {"piid": 5, "value": timestamp},
                        ],
                    }
                },
            }
        )

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session, account)
            device = client._ensure_device()
            manager = map_manager.DreameMapMowerMapManager(Mock())
            device._map_manager = manager
            manager._aes_iv, manager._ready = iv, True
            observed = []
            monkeypatch.setattr(
                device,
                "_property_changed",
                lambda: observed.append(
                    (manager._current_frame_id, manager._map_data.timestamp_ms)
                ),
            )
            manager.listen(device._map_changed, device._property_changed)
            deadline = time.monotonic() + 10
            application = client_map_application.NativeMapApplication(
                client,
                device,
                manager,
                deadline=deadline,
            )
            try:
                if download_fails:
                    assert await application.raw_frame(
                        _encoded_map_frame(timestamp, 2), None
                    )
                    observed.clear()
                assert await application.request_next(7, 2) is True
                assert observed == (
                    [(3, timestamp + 1)]
                    if download_fails
                    else [(2, timestamp), (3, timestamp + 1)]
                )
                assert manager._current_frame_id == 3
                sign.assert_awaited_once_with(
                    client._descriptor.did,
                    client._descriptor.model,
                    "frame-object",
                    deadline=deadline,
                )
                download.assert_awaited_once_with(
                    "https://example.invalid/map", deadline=deadline
                )
            finally:
                await client.async_close()

    asyncio.run(scenario())


@pytest.mark.parametrize("listening", [False, True])
@pytest.mark.parametrize("queued", [False, True])
def test_decoded_frame_respects_notification_registration(
    monkeypatch, listening, queued
):
    async def scenario():
        async with ClientSession() as session:
            client = client_for(session)
            device = client._ensure_device()
            manager = map_manager.DreameMapMowerMapManager(Mock())
            device._map_manager = manager
            manager._ready = True
            notified = []
            monkeypatch.setattr(
                device,
                "_property_changed",
                lambda: notified.append(manager._current_frame_id),
            )
            if listening:
                manager.listen(device._map_changed, device._property_changed)
            application = client_map_application.NativeMapApplication(
                client,
                device,
                manager,
                deadline=time.monotonic() + 5,
            )
            try:
                incoming = _encoded_map_frame(1700000000000, 2)
                if queued:
                    await application.raw_frame(
                        _encoded_map_frame(1699999999999, 1), None
                    )
                    notified.clear()
                    raw = _encoded_p_frame(1700000000001, 3)
                    manager._queue_partial_map(manager._decode_map_partial(raw))
                    incoming = _encoded_p_frame(1700000000000, 2)
                assert await application.raw_frame(incoming, None) is True
                assert manager._current_frame_id == (3 if queued else 2)
                assert notified == (([2, 3] if queued else [2]) if listening else [])
                # An undecodable frame must not be reported as applied or
                # replace the valid frame used for subsequent recovery decisions.
                assert await application.raw_frame("", None) is False
                assert manager._current_frame_id == (3 if queued else 2)
                assert notified == (([2, 3] if queued else [2]) if listening else [])
            finally:
                await client.async_close()

    asyncio.run(scenario())
