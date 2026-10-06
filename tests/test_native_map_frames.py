"""Native map-frame RPC preserves the synchronous wire envelope."""

import asyncio
import time
from unittest.mock import Mock

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    client_map_frames,
    map_manager,
)

from .test_async_app_commands import client_for
from .test_async_cloud_session import cloud_strings, login_response, server


@pytest.mark.parametrize("account", ["dreame", "mova"])
@pytest.mark.parametrize(
    "parameters", [None, {"frame_type": "P", "map_id": 8, "frame_id": 3}]
)
def test_native_map_request_preserves_sync_payload(monkeypatch, account, parameters):
    strings = cloud_strings(account)
    captured = []
    result = {"code": 0, "out": []}

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        body = await request.json()
        captured.append(body["data"])
        return web.json_response({"code": 0, "data": {"result": result}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session, account)
            device = client._ensure_device()
            device._protocol.cloud._host = "mqtt.example.invalid:8883"
            sync_protocol = Mock()
            sync_protocol.action.return_value = result
            manager = map_manager.DreameMapMowerMapManager(sync_protocol)
            assert manager._request_map(parameters) == result
            siid, aiid, payload, retry = sync_protocol.action.call_args.args
            assert (siid, aiid, retry) == (6, 1, 0)
            assert payload == [
                {
                    "piid": 2,
                    "value": (
                        '{"frame_type":"I"}'
                        if parameters is None
                        else '{"frame_type":"P","map_id":8,"frame_id":3}'
                    ),
                }
            ]
            try:
                assert (
                    await client_map_frames.async_request_map_frame(
                        client,
                        parameters,
                        deadline=time.monotonic() + 3,
                    )
                    == result
                )
                assert len(captured) == 1
                assert captured[0]["method"] == "action"
                assert captured[0]["params"] == {
                    "did": client._descriptor.did,
                    "siid": siid,
                    "aiid": aiid,
                    "in": payload,
                }
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("properties", "expected", "latest_time"),
    [
        ([{"piid": 3, "value": "object,key"}], ("object,key", None), 13),
        ([{"piid": 1, "value": "frame"}], (None, "frame"), 13),
        ([{"piid": 13, "value": "1,old-object,key"}], ("old-object,key", None), None),
        ([{"piid": 13, "value": "0,old-frame"}], (None, "old-frame"), None),
        ([{"piid": 1}, {"piid": 3, "value": ""}], (None, None), None),
    ],
)
@pytest.mark.parametrize("start_time", [None, 1000])
def test_complete_frame_response_preserves_metadata_without_io(
    properties,
    expected,
    latest_time,
    start_time,
):
    protocol = Mock()
    manager = map_manager.DreameMapMowerMapManager(protocol)
    manager._add_map_data_file = Mock(side_effect=AssertionError("unexpected download"))
    manager._add_raw_map_data = Mock(
        side_effect=AssertionError("unexpected application")
    )
    manager._latest_object_name_time = None
    manager._map_request_time = 1000
    manager._map_request_count = 7

    assert (
        manager._read_i_map_response(
            {"code": 0, "out": [{"piid": 5, "value": "12000"}, *properties]},
            start_time,
        )
        == expected
    )
    assert manager._last_robot_time == 12000
    assert manager._latest_object_name_time == latest_time
    assert manager._map_request_time == (
        None if latest_time is not None else (12000 if start_time is None else 1000)
    )
    assert manager._map_request_count == (1 if start_time is None else 7)
    protocol.action.assert_not_called()
    manager._add_map_data_file.assert_not_called()
    manager._add_raw_map_data.assert_not_called()


@pytest.mark.parametrize(
    "outcome", ["success", "changed", "disconnected", "close", "cancel", "deadline"]
)
def test_frame_download_retains_key_and_rejects_stale_owner(monkeypatch, outcome):
    from unittest.mock import AsyncMock

    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
        cloud_session,
        exceptions,
    )

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        sign = AsyncMock(return_value="https://example.invalid/private-frame")

        async def download(self, url, *, deadline):
            assert url == "https://example.invalid/private-frame"
            entered.set()
            await release.wait()
            return b"encoded-frame"

        monkeypatch.setattr(
            cloud_session.DreameCloudSession, "async_get_interim_file_url", sign
        )
        monkeypatch.setattr(
            cloud_session.DreameCloudSession, "async_get_public_file", download
        )
        async with ClientSession() as session:
            client = client_for(session)
            device = client._ensure_device()
            manager = device._map_manager
            deadline = time.monotonic() + (0.2 if outcome == "deadline" else 3)
            task = asyncio.create_task(
                client_map_frames.async_download_frame_object(
                    client,
                    device,
                    manager,
                    "frame-object,frame-key",
                    deadline=deadline,
                )
            )
            try:
                await asyncio.wait_for(entered.wait(), 2)
                sign.assert_awaited_once_with(
                    client._descriptor.did,
                    client._descriptor.model,
                    "frame-object",
                    deadline=deadline,
                )
                if outcome == "changed":
                    device._map_manager = map_manager.DreameMapMowerMapManager(
                        device._protocol
                    )
                elif outcome == "disconnected":
                    manager._disconnected = True
                elif outcome == "close":
                    await asyncio.wait_for(client.async_close(), 2)
                if outcome == "cancel":
                    task.cancel()
                if outcome != "deadline":
                    release.set()
                if outcome == "success":
                    assert await task == (b"encoded-frame", "frame-key")
                else:
                    with pytest.raises(
                        asyncio.CancelledError
                        if outcome in {"close", "cancel"}
                        else exceptions.DreameLawnMowerConnectionError
                    ):
                        await task
                assert not client._cloud_read_tasks
                assert not session.closed
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                device._map_manager = manager
                await client.async_close()

    asyncio.run(scenario())

@pytest.mark.parametrize("result_code", [0, 1])
def test_missing_frame_uses_native_transport_and_existing_retry_policy(
    monkeypatch, result_code
):
    strings = cloud_strings("dreame")
    captured = []

    async def scenario():
        received = asyncio.Event()

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            captured.append((await request.json())["data"]["params"]["in"])
            received.set()
            return web.json_response(
                {"code": 0, "data": {"result": {"code": result_code}}}
            )

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            device = client._ensure_device()
            manager = device._map_manager
            device._protocol.cloud._host = "mqtt.example.invalid:8883"
            manager._map_data = Mock()
            manager._current_map_id = 8
            manager._current_frame_id = 2
            monkeypatch.setattr(manager, "_partial_map_queue_size", lambda: 1)
            monkeypatch.setattr(
                manager, "_request_map", Mock(side_effect=AssertionError("sync HTTP"))
            )
            owner = client_map_frames.NativeMissingMapFrames(client, device)
            manager._native_missing_frame_request = owner.request
            try:
                await asyncio.to_thread(manager._request_missing_p_map)
                await asyncio.wait_for(received.wait(), 2)
                await owner._task
                assert captured == [[{
                    "piid": 2,
                    "value": '{"map_id":8,"frame_id":3,"frame_type":"P"}',
                }]]
                assert manager._last_p_request_map_id == 8
                assert manager._last_p_request_frame_id == 3
                # Re-enter the real policy immediately: success and rejection
                # both keep the existing three-second request throttle.
                await owner._request()
                assert len(captured) == 1
                assert not client._cloud_read_tasks
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())

@pytest.mark.parametrize("finish", ["complete", "cancel", "close"])
def test_missing_frame_coalesces_and_drains_inflight_request(monkeypatch, finish):
    strings = cloud_strings("dreame")

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        requests = []

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            requests.append(await request.json())
            entered.set()
            await release.wait()
            return web.json_response({"code": 0, "data": {"result": {"code": 0}}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            device = client._ensure_device()
            manager = device._map_manager
            device._protocol.cloud._host = "mqtt.example.invalid:8883"
            manager._map_data = Mock()
            manager._current_map_id = 8
            manager._current_frame_id = 2
            monkeypatch.setattr(manager, "_partial_map_queue_size", lambda: 1)
            owner = client_map_frames.NativeMissingMapFrames(client, device)
            try:
                owner._start()
                await asyncio.wait_for(entered.wait(), 2)
                task = owner._task
                for _ in range(5):
                    owner._start()
                assert owner._task is task
                assert len(requests) == 1
                if finish == "close":
                    await asyncio.wait_for(client.async_close(), 2)
                elif finish == "cancel":
                    task.cancel()
                else:
                    release.set()
                if finish == "complete":
                    await asyncio.wait_for(task, 2)
                    await asyncio.wait_for(owner._task, 2)
                    assert len(requests) == 1
                else:
                    with pytest.raises(asyncio.CancelledError):
                        await task
                assert not client._cloud_read_tasks
                assert not session.closed
            finally:
                release.set()
                await client.async_close()

    asyncio.run(scenario())


def test_missing_frame_rejects_manager_replaced_after_preparation(monkeypatch):
    from unittest.mock import AsyncMock

    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
        cloud_session,
        exceptions,
    )

    async def scenario():
        async with ClientSession() as session:
            client = client_for(session)
            device = client._ensure_device()
            manager = device._map_manager
            device._protocol.cloud._host = "mqtt.example.invalid:8883"
            manager._map_data = Mock()
            manager._current_map_id = 8
            manager._current_frame_id = 2
            monkeypatch.setattr(manager, "_partial_map_queue_size", lambda: 1)
            original = client_map_frames.async_read_device_state

            async def replace_after_read(*args, **kwargs):
                result = await original(*args, **kwargs)
                device._map_manager = map_manager.DreameMapMowerMapManager(
                    device._protocol
                )
                return result

            monkeypatch.setattr(
                client_map_frames, "async_read_device_state", replace_after_read
            )
            send = AsyncMock(side_effect=AssertionError("stale owner sent RPC"))
            monkeypatch.setattr(
                cloud_session.DreameCloudSession, "async_command_device_action", send
            )
            owner = client_map_frames.NativeMissingMapFrames(client, device)
            try:
                with pytest.raises(exceptions.DreameLawnMowerConnectionError):
                    await owner._request()
                send.assert_not_awaited()
            finally:
                device._map_manager = manager
                await client.async_close()

    asyncio.run(scenario())


def test_missing_frame_rechecks_new_gap_after_inflight_rpc(monkeypatch):
    strings = cloud_strings("dreame")

    async def scenario():
        first, second, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        payloads = []

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            payloads.append((await request.json())["data"]["params"]["in"][0]["value"])
            if len(payloads) == 1:
                first.set()
                await release.wait()
            else:
                second.set()
            return web.json_response({"code": 0, "data": {"result": {"code": 0}}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            device = client._ensure_device()
            manager = device._map_manager
            device._protocol.cloud._host = "mqtt.example.invalid:8883"
            manager._map_data = Mock()
            manager._current_map_id = 8
            manager._current_frame_id = 2
            monkeypatch.setattr(manager, "_partial_map_queue_size", lambda: 1)
            owner = client_map_frames.NativeMissingMapFrames(client, device)
            try:
                owner._start()
                await asyncio.wait_for(first.wait(), 2)
                manager._current_frame_id = 3
                owner._start()
                release.set()
                await asyncio.wait_for(second.wait(), 2)
                await owner._task
                assert payloads == [
                    '{"map_id":8,"frame_id":3,"frame_type":"P"}',
                    '{"map_id":8,"frame_id":4,"frame_type":"P"}',
                ]
            finally:
                release.set()
                await client.async_close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "properties, expected",
    [
        ([{"piid": 3, "value": "object,key"}], ("object,key", None, None)),
        ([{"piid": 1, "value": "inline"}], (None, "inline", None)),
        (
            [{"piid": 3, "value": "object"}, {"piid": 1, "value": "inline"},
             {"piid": 5, "value": "12000"}],
            ("object", "inline", 12000),
        ),
        ([{"piid": 1}, {"piid": 3, "value": ""}], (None, None, None)),
        ([{"piid": 5, "value": 0}], (None, None, 0)),
    ],
)
def test_next_frame_metadata_preserves_inline_file_and_timestamp(properties, expected):
    assert map_manager.DreameMapMowerMapManager._read_p_map_response(
        {"code": 0, "out": properties}
    ) == expected
