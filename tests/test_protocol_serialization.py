"""Shared Dreame cloud transport serialization contracts."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from threading import Event, RLock, Thread
from unittest.mock import Mock

import pytest
import requests

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    protocol,
    protocol_cloud,
)


@pytest.mark.parametrize("host,path", [
    (None, "rpc/iot/command"), ("hub.example.invalid", "rpc-hub/iot/command"),
])
@pytest.mark.parametrize("callback_mode", [False, True])
def test_rpc_envelope_preserves_routing_and_request_identity(host, path, callback_mode):
    cloud = object.__new__(protocol_cloud.DreameMowerDreameHomeCloudProtocol)
    cloud._host = host
    cloud._did = "42"
    cloud._id = 10
    cloud._strings = [""] * 53
    cloud._strings[37] = "rpc"
    cloud._strings[27] = "iot"
    cloud._strings[38] = "command"
    parameters = [{"did": "1", "siid": 2, "piid": 1}]
    result = [{"did": "1", "code": 0, "value": 3}]
    response = {"code": 0, "data": {"result": result}}
    cloud._api_call = Mock(return_value=response)
    cloud._api_call_async = Mock()
    callback = Mock()
    if callback_mode:
        cloud.send_async(callback, "get_properties", parameters, retry_count=1)
        args = cloud._api_call_async.call_args.args
        args[0](response)
        callback.assert_called_once_with(result)
        route, payload, retries = args[1:]
        expected_id = 11
    else:
        assert cloud.send("get_properties", parameters, retry_count=1) == result
        route, payload, retries = cloud._api_call.call_args.args[:3]
        expected_id = 10
    assert route == path
    assert retries == 1
    assert payload == {
        "did": "42", "id": expected_id,
        "data": {
            "did": "42", "id": expected_id,
            "method": "get_properties", "params": parameters,
        },
    }
    assert cloud._id == 11


def test_native_rpc_serializes_tasks_and_legacy_threads_and_releases_on_cancel():
    cloud = object.__new__(protocol_cloud.DreameMowerDreameHomeCloudProtocol)
    cloud._request_lock = RLock()
    cloud._id = 10
    legacy_entered = Event()
    ids = []

    def legacy():
        with cloud._operation_lock():
            ids.append(cloud._id)
            cloud._id += 1
            legacy_entered.set()

    async def scenario():
        entered = asyncio.Event()
        second_entered = asyncio.Event()

        async def first():
            async with cloud.async_rpc_operation(deadline=time.monotonic() + 2) as rid:
                ids.append(rid)
                entered.set()
                await asyncio.Future()

        async def second():
            async with cloud.async_rpc_operation(deadline=time.monotonic() + 2) as rid:
                ids.append(rid)
                second_entered.set()

        task = asyncio.create_task(first())
        await entered.wait()
        thread = Thread(target=legacy)
        thread.start()
        next_task = asyncio.create_task(second())
        try:
            await asyncio.sleep(0.03)
            assert not legacy_entered.is_set()
            assert not second_entered.is_set()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await asyncio.wait_for(next_task, 2)
        await asyncio.to_thread(thread.join, 2)
        assert not thread.is_alive()
        assert task.cancelled()

    asyncio.run(scenario())
    assert legacy_entered.is_set()
    assert sorted(ids) == [10, 11, 12]


def test_native_rpc_lock_wait_has_deadline_without_consuming_id():
    cloud = object.__new__(protocol_cloud.DreameMowerDreameHomeCloudProtocol)
    cloud._request_lock = RLock()
    cloud._id = 10
    entered = Event()
    release = Event()

    def legacy():
        with cloud._operation_lock():
            entered.set()
            release.wait(2)

    async def scenario():
        with pytest.raises(
            protocol_cloud.DreameLawnMowerConnectionError, match="timed out",
        ):
            async with cloud.async_rpc_operation(deadline=time.monotonic() + 0.03):
                pytest.fail("Legacy operation still owns the request lock")
        assert cloud._id == 10

    thread = Thread(target=legacy)
    thread.start()
    assert entered.wait(1)
    try:
        asyncio.run(scenario())
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive()


def test_cloud_request_lock_serializes_app_and_device_operations() -> None:
    """Different cloud entry points must not race one session or request id."""
    cloud = object.__new__(protocol_cloud.DreameMowerDreameHomeCloudProtocol)
    cloud._request_lock = RLock()
    request_started = Event()
    release_request = Event()
    send_started = Event()

    def request_unlocked(*_args, **_kwargs) -> str:
        request_started.set()
        assert release_request.wait(timeout=1)
        return "request"

    def send_unlocked(*_args, **_kwargs) -> str:
        send_started.set()
        return "send"

    cloud._request_unlocked = request_unlocked
    cloud._send_unlocked = send_unlocked
    results: list[str] = []
    request_thread = Thread(
        target=lambda: results.append(cloud.request("https://example.invalid", None))
    )
    send_thread = Thread(
        target=lambda: results.append(cloud.send("get_properties", []))
    )

    request_thread.start()
    assert request_started.wait(timeout=1)
    send_thread.start()
    assert not send_started.wait(timeout=0.05)

    release_request.set()
    request_thread.join(timeout=1)
    send_thread.join(timeout=1)

    assert not request_thread.is_alive()
    assert not send_thread.is_alive()
    assert send_started.is_set()
    assert sorted(results) == ["request", "send"]


def test_cloud_request_deadline_includes_waiting_for_shared_lock() -> None:
    cloud = object.__new__(protocol_cloud.DreameMowerDreameHomeCloudProtocol)
    cloud._request_lock = RLock()
    cloud._request_unlocked = lambda *_args, **_kwargs: "unexpected"
    errors: list[Exception] = []

    cloud._request_lock.acquire()
    try:
        request_thread = Thread(
            target=lambda: _capture_request_error(
                cloud,
                deadline=time.monotonic() + 0.05,
                errors=errors,
            )
        )
        request_thread.start()
        request_thread.join(timeout=0.5)
    finally:
        cloud._request_lock.release()

    assert not request_thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], requests.exceptions.Timeout)


def test_deadline_worker_keeps_serialization_until_transport_exits() -> None:
    cloud = object.__new__(protocol_cloud.DreameMowerDreameHomeCloudProtocol)
    cloud._request_lock = RLock()
    transport_started = Event()
    release_transport = Event()
    follower_started = Event()
    errors: list[Exception] = []

    def slow_transport() -> str:
        transport_started.set()
        assert release_transport.wait(timeout=1)
        return "late"

    caller = Thread(
        target=lambda: _capture_serialized_operation_error(
            cloud,
            slow_transport,
            deadline=time.monotonic() + 0.05,
            errors=errors,
        )
    )
    caller.start()
    assert transport_started.wait(timeout=1)
    caller.join(timeout=0.5)

    assert not caller.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], requests.exceptions.Timeout)

    follower = Thread(
        target=lambda: cloud._run_serialized_operation(
            lambda: follower_started.set(),
            deadline=None,
        )
    )
    follower.start()
    assert not follower_started.wait(timeout=0.05)

    release_transport.set()
    follower.join(timeout=1)

    assert not follower.is_alive()
    assert follower_started.is_set()


def test_cloud_disconnect_does_not_wait_forever_for_deadline_worker() -> None:
    cloud = object.__new__(protocol_cloud.DreameMowerDreameHomeCloudProtocol)
    cloud._request_lock = RLock()
    cloud._connected = True
    cloud._logged_in = True
    cloud._message_callback = object()
    cloud._connected_callback = object()
    cloud._session = Mock()
    cloud._thread = None
    mqtt_client = Mock()
    cloud._client = mqtt_client
    cloud._client_connected = True
    cloud._client_connecting = True
    reconnect_timer = Mock()
    cloud._reconnect_timer = reconnect_timer
    lock_held = Event()
    release_lock = Event()

    def hold_lock() -> None:
        with cloud._request_lock:
            lock_held.set()
            assert release_lock.wait(timeout=1)

    worker = Thread(target=hold_lock)
    worker.start()
    assert lock_held.wait(timeout=1)

    started = time.monotonic()
    disconnected = cloud.disconnect(timeout=0.05)
    elapsed = time.monotonic() - started
    release_lock.set()
    worker.join(timeout=1)
    cloud._disconnect_cleanup_thread.join(timeout=1)

    assert disconnected is False
    assert elapsed < 0.5
    assert not cloud._disconnect_cleanup_thread.is_alive()
    cloud._session.close.assert_called_once_with()
    assert cloud._connected is False
    assert cloud._logged_in is False
    assert cloud._message_callback is None
    assert cloud._connected_callback is None
    assert cloud._client is None
    assert cloud._client_connected is False
    assert cloud._client_connecting is False
    reconnect_timer.cancel.assert_called_once_with()
    assert cloud._reconnect_timer is None
    mqtt_client.loop_stop.assert_called_once_with()
    mqtt_client.disconnect.assert_called_once_with()


def test_cloud_disconnect_callback_does_not_rearm_during_shutdown() -> None:
    cloud = object.__new__(protocol_cloud.DreameMowerDreameHomeCloudProtocol)
    cloud._shutdown_requested = True
    cloud._reconnect_timer = None
    cloud._client_connected = True
    cloud._client_connecting = True

    protocol_cloud.DreameMowerDreameHomeCloudProtocol._on_client_disconnect(
        Mock(),
        cloud,
        1,
    )

    assert cloud._reconnect_timer is None
    assert cloud._client_connected is False
    assert cloud._client_connecting is False


def test_late_deadline_worker_cannot_restore_state_after_disconnect() -> None:
    cloud = object.__new__(protocol_cloud.DreameMowerDreameHomeCloudProtocol)
    cloud._request_lock = RLock()
    cloud._session = Mock()
    cloud._connected = True
    cloud._logged_in = True
    cloud._message_callback = object()
    cloud._connected_callback = object()
    cloud._client = None
    cloud._client_connected = False
    cloud._client_connecting = False
    cloud._thread = None
    operation_started = Event()
    release_operation = Event()
    errors: list[Exception] = []

    def late_operation() -> None:
        operation_started.set()
        assert release_operation.wait(timeout=1)
        if not cloud._disconnect_is_pending():
            cloud._connected = True
            cloud._logged_in = True

    caller = Thread(
        target=lambda: _capture_serialized_operation_error(
            cloud,
            late_operation,
            deadline=time.monotonic() + 0.05,
            errors=errors,
        )
    )
    caller.start()
    assert operation_started.wait(timeout=1)
    caller.join(timeout=0.5)

    disconnected = cloud.disconnect(timeout=0.05)
    release_operation.set()
    cloud._disconnect_cleanup_thread.join(timeout=1)

    assert disconnected is False
    assert len(errors) == 1
    assert isinstance(errors[0], requests.exceptions.Timeout)
    assert cloud._connected is False
    assert cloud._logged_in is False
    assert cloud._session.close.call_count == 1
    assert cloud._disconnect_pending is False


def test_queued_operation_cannot_restart_cloud_after_disconnect() -> None:
    cloud = object.__new__(protocol_cloud.DreameMowerDreameHomeCloudProtocol)
    cloud._request_lock = RLock()
    cloud._session = Mock()
    cloud._connected = True
    cloud._logged_in = True
    cloud._message_callback = object()
    cloud._connected_callback = object()
    cloud._client = None
    cloud._client_connected = False
    cloud._client_connecting = False
    cloud._thread = None
    operation_started = Event()

    cloud._request_lock.acquire()
    queued = Thread(
        target=lambda: cloud._run_serialized_operation(
            lambda: operation_started.set(),
            deadline=None,
        )
    )
    queued.start()
    try:
        assert cloud.disconnect(timeout=0.05) is True
    finally:
        cloud._request_lock.release()
    queued.join(timeout=1)

    assert not queued.is_alive()
    assert not operation_started.is_set()
    assert cloud._shutdown_requested is True
    assert cloud._disconnect_pending is False
    assert cloud._connected is False
    assert cloud._logged_in is False


def test_protocol_disconnects_aliased_cloud_only_once() -> None:
    mower_protocol = object.__new__(protocol.DreameMowerProtocol)
    cloud = Mock()
    mower_protocol.cloud = cloud
    mower_protocol.device_cloud = cloud
    mower_protocol._connected = True

    mower_protocol.disconnect()

    cloud.disconnect.assert_called_once_with()
    assert mower_protocol._connected is False


def test_optional_cloud_timeout_details_are_debug_only(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cloud = object.__new__(protocol_cloud.DreameMowerDreameHomeCloudProtocol)
    cloud._key_expire = 0
    cloud._country = "eu"
    cloud._strings = [f"header-{index}" for index in range(53)]
    cloud._ti = ""
    cloud._key = "key"
    cloud._session = Mock()
    cloud._connected = True
    cloud._fail_count = 0
    cloud._deadline_operation_runs_in_worker = lambda: False
    monkeypatch.setattr(
        protocol_cloud,
        "_post_cloud_response",
        Mock(side_effect=requests.exceptions.Timeout),
    )

    with caplog.at_level(logging.DEBUG, logger=protocol_cloud.__name__):
        result = cloud._request_unlocked(
            "https://example.invalid",
            {"data": "optional"},
            retry_count=0,
            timeout=0.01,
        )

    assert result is None
    warning_records = [
        record for record in caplog.records if record.levelno >= logging.WARNING
    ]
    assert not warning_records
    assert "Read timed out" in caplog.text


def test_missing_optional_cloud_response_is_debug_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cloud = object.__new__(protocol_cloud.DreameMowerDreameHomeCloudProtocol)
    cloud._host = ""
    cloud._did = "device-1"
    cloud._id = 1
    cloud._strings = [f"value-{index}" for index in range(53)]
    cloud._api_call = Mock(return_value=None)

    with caplog.at_level(logging.DEBUG, logger=protocol_cloud.__name__):
        result = cloud._send_unlocked("action", [])

    assert result is None
    warning_records = [
        record for record in caplog.records if record.levelno >= logging.WARNING
    ]
    assert not warning_records
    assert "send failed" in caplog.text


def _capture_request_error(
    cloud: protocol_cloud.DreameMowerDreameHomeCloudProtocol,
    *,
    deadline: float,
    errors: list[Exception],
) -> None:
    try:
        cloud.request("https://example.invalid", None, deadline=deadline)
    except Exception as err:  # noqa: BLE001 - thread forwards the observed failure
        errors.append(err)


def _capture_serialized_operation_error(
    cloud: protocol_cloud.DreameMowerDreameHomeCloudProtocol,
    operation: Callable[[], object],
    *,
    deadline: float,
    errors: list[Exception],
) -> None:
    try:
        cloud._run_serialized_operation(operation, deadline=deadline)
    except Exception as err:  # noqa: BLE001 - thread forwards the observed failure
        errors.append(err)


def test_app_action_retries_reads_but_dispatches_mutations_once() -> None:
    cloud = object.__new__(protocol_cloud.DreameMowerDreameHomeCloudProtocol)
    cloud._did = "device-1"
    calls: list[tuple[str, int]] = []

    def send(
        method: str,
        parameters: object,
        retry_count: int,
        **_kwargs: object,
    ) -> object:
        calls.append((method, retry_count))
        return {"r": 0}

    cloud.send = send

    cloud.call_app_action({"m": "g", "t": "MAPL"})
    cloud.call_app_action({"m": "s", "t": "CFG", "d": {"value": 1}})
    cloud.call_app_action({"m": "a", "o": 10, "d": {"idx": 0}})

    assert calls == [
        ("action", 2),
        ("action", 0),
        ("action", 0),
    ]
