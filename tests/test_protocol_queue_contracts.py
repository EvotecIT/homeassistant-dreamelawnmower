"""Legacy protocol failure delivery, worker lifetime, and private transport logs."""

from __future__ import annotations

import json
import logging
from queue import Queue
from threading import Event, Thread
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import paho.mqtt.client as mqtt
import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    device as device_module,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    protocol,
    protocol_cloud,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    DeviceException,
)


@pytest.mark.parametrize("transport", ["cloud", "lan"])
@pytest.mark.parametrize("failure", ["operation", "callback"])
def test_request_failure_delivers_once_and_does_not_strand_queue(
    transport: str,
    failure: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Real queues finish failed items, preserve order and consume the stop marker."""
    cls = (
        protocol_cloud.DreameMowerDreameHomeCloudProtocol
        if transport == "cloud"
        else protocol.DreameMowerDeviceProtocol
    )
    owner = object.__new__(cls)
    owner._queue = Queue()
    marker = "private-device-value"
    delivered: list[tuple[str, Any]] = []
    invoked: list[tuple[str, Any, int]] = []
    escaped: list[Exception] = []

    def dispatch(method: str, params: Any, retries: int) -> Any:
        invoked.append((method, params, retries))
        if method == "first" and failure == "operation":
            raise DeviceException(marker)
        return method + "-result"

    def first_callback(value: Any) -> None:
        delivered.append(("first", value))
        if failure == "callback":
            raise DeviceException(marker)

    if transport == "cloud":
        owner._api_call = dispatch
    else:
        owner.send = dispatch
    owner._queue.put((first_callback, "first", {"siid": 1}, 0))
    owner._queue.put(
        (lambda value: delivered.append(("second", value)), "second", [], 2)
    )
    owner._queue.put(())

    def run() -> None:
        try:
            owner._api_task()
        except Exception as err:
            escaped.append(err)

    with caplog.at_level(logging.DEBUG):
        worker = Thread(target=run, daemon=True)
        worker.start()
        worker.join(2)
    assert not worker.is_alive()
    assert escaped == []
    assert invoked == [("first", {"siid": 1}, 0), ("second", [], 2)]
    assert delivered == [
        ("first", None if failure == "operation" else "first-result"),
        ("second", "second-result"),
    ]
    assert owner._queue.unfinished_tasks == 0
    assert "DeviceException" in caplog.text
    assert marker not in caplog.text


def test_cloud_facade_delivers_absent_response_and_recovers_next_request() -> None:
    """The public callback observes a failed reply and the next request can succeed."""
    facade = protocol.DreameMowerProtocol(
        username="test", password="test", country="cn"
    )
    cloud = facade.cloud
    cloud._strings = protocol_cloud.cloud_strings("dreame")
    cloud._logged_in = True
    cloud._connected = True
    cloud._client_connected = True
    cloud._api_call = Mock(side_effect=[None, {"data": {"result": ["available"]}}])
    observed: list[tuple[Any, bool]] = []
    completed = Event()
    facade._connected = True

    def second_callback(value: Any) -> None:
        observed.append((value, facade.connected))
        completed.set()

    try:
        facade.send_async(
            lambda value: observed.append((value, facade.connected)),
            "get_properties",
            [],
        )
        facade.send_async(second_callback, "get_properties", [])
        assert completed.wait(2)
        facade.disconnect()
        cloud._thread.join(2)
        assert not cloud._thread.is_alive()
        assert observed == [(None, False), (["available"], True)]
        assert cloud._queue.unfinished_tasks == 0
    finally:
        facade.disconnect()


@pytest.mark.parametrize("reply", [{"data": None}, {"data": "not-an-object"}, ["data"]])
def test_callback_rpc_delivers_missing_result_for_malformed_cloud_reply(
    reply: Any,
) -> None:
    """A malformed vendor envelope follows the same absent-result callback contract."""
    cloud = protocol_cloud.DreameMowerDreameHomeCloudProtocol("test", "test")
    cloud._strings = protocol_cloud.cloud_strings("dreame")
    callback = Mock()
    cloud._api_call_async = Mock()
    try:
        cloud.send_async(callback, "get_properties", [])
        cloud._api_call_async.call_args.args[0](reply)
        callback.assert_called_once_with(None)
    finally:
        cloud.disconnect()


def test_reapplying_lan_credentials_preserves_discovery_and_changes_reset_it() -> None:
    """An unchanged token does not invalidate a valid MiIO discovery."""
    token = "01" * 16
    owner = protocol.DreameMowerDeviceProtocol("192.0.2.1", token)
    owner._discovered = True
    owner.set_credentials("192.0.2.1", token)
    assert owner.connected
    owner.set_credentials("192.0.2.2", "02" * 16)
    assert not owner.connected
    assert owner.ip == "192.0.2.2"
    assert owner.token == bytes.fromhex("02" * 16)


def test_invalid_lan_credentials_do_not_partially_replace_current_identity() -> None:
    """Reject malformed hex before changing the active endpoint or discovery."""
    token = "01" * 16
    owner = protocol.DreameMowerDeviceProtocol("192.0.2.1", token)
    owner._discovered = True
    with pytest.raises(ValueError):
        owner.set_credentials("192.0.2.2", "not-hex")
    assert owner.ip == "192.0.2.1"
    assert owner.token == bytes.fromhex(token)
    assert owner.connected


@pytest.mark.parametrize("operation", ["request", "get", "get_file"])
def test_transport_error_logs_do_not_include_private_exception_text(
    operation: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Requests exceptions can carry signed URLs or account values in their text."""
    cloud = protocol_cloud.DreameMowerDreameHomeCloudProtocol("test", "test")
    cloud._strings = protocol_cloud.cloud_strings("dreame")
    marker = "private-account-value"
    cloud._connected = True
    cloud._session.close()
    cloud._session = Mock()
    cloud._session.post.side_effect = RuntimeError(marker)
    cloud._session.get.side_effect = RuntimeError(marker)
    try:
        with caplog.at_level(logging.DEBUG):
            if operation == "request":
                result = cloud.request(
                    "https://test.example.invalid", "{}", retry_count=0
                )
            elif operation == "get":
                result = cloud.get("https://test.example.invalid", retry_count=0)
            else:
                result = cloud.get_file("https://test.example.invalid", retry_count=0)
        assert result is None
        assert "RuntimeError" in caplog.text
        assert marker not in caplog.text
    finally:
        cloud.disconnect()


def test_mqtt_message_delivery_does_not_log_private_payload(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The actual Paho handler delivers state while keeping it out of debug logs."""
    cloud = protocol_cloud.DreameMowerDreameHomeCloudProtocol("test", "test")
    cloud._message_callback = Mock()
    message = mqtt.MQTTMessage()
    message.payload = b'{"data":{"stream_key":"private-stream-key"}}'
    try:
        with caplog.at_level(logging.DEBUG):
            cloud._on_client_message(Mock(), cloud, message)
        cloud._message_callback.assert_called_once_with(
            {"stream_key": "private-stream-key"}
        )
        assert "private-stream-key" not in caplog.text
    finally:
        cloud.disconnect()


def test_refresh_login_preserves_authentication_without_original_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refresh grant does not require retaining the original password."""
    cloud = protocol_cloud.DreameMowerDreameHomeCloudProtocol(None, None)
    strings = protocol_cloud.cloud_strings("dreame")
    cloud._strings = strings
    cloud._secondary_key = "synthetic-refresh-token"
    response = SimpleNamespace(
        status_code=200,
        text=json.dumps(
            {
                strings[18]: "synthetic-token",
                strings[19]: "synthetic-refresh-token",
                strings[20]: 300,
            }
        ),
    )
    post = Mock(return_value=response)
    monkeypatch.setattr(protocol_cloud, "_post_cloud_response", post)
    try:
        assert cloud.login() is True
        assert cloud.logged_in
        assert post.call_count == 1
        assert "synthetic-refresh-token" in post.call_args.args[2]["data"]
    finally:
        cloud.disconnect()


def test_pending_map_failure_after_disconnect_cannot_rearm_device_timer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Actual legacy map callbacks cannot create polling work after device close."""
    mower = device_module.DreameMowerDevice("Protocol shutdown", None, None)
    mower._map_manager = None
    cloud = mower._protocol.cloud
    cloud._logged_in = True
    cloud._strings = protocol_cloud.cloud_strings("dreame")
    entered, release = Event(), Event()
    timer = Mock()
    monkeypatch.setattr(device_module, "Timer", timer)

    def dispatch(*args: Any, **kwargs: Any) -> None:
        entered.set()
        assert release.wait(2)

    cloud._api_call = dispatch
    try:
        mower.update_map_data_async({"test": 1})
        assert entered.wait(2)
        mower.disconnect()
        release.set()
        cloud._thread.join(2)
        assert not cloud._thread.is_alive()
        assert mower.disconnected
        timer.assert_not_called()
        assert mower._update_timer is None
    finally:
        release.set()
        if cloud._thread is not None:
            cloud._thread.join(2)
        mower.disconnect()


@pytest.mark.parametrize("deferred", [False, True])
def test_repeated_and_deferred_disconnect_drain_one_stop_marker(deferred: bool) -> None:
    """Both teardown routes close one real worker and leave completed accounting."""
    cloud = protocol_cloud.DreameMowerDreameHomeCloudProtocol("test", "test")
    entered, release, delivered = Event(), Event(), Event()

    def dispatch(*args: Any, **kwargs: Any) -> None:
        with cloud._operation_lock():
            entered.set()
            assert release.wait(2)

    cloud._api_call = dispatch
    try:
        cloud._api_call_async(lambda value: delivered.set(), "read")
        assert entered.wait(2)
        if deferred:
            assert cloud.disconnect(timeout=0.01) is False
            release.set()
            cloud._disconnect_cleanup_thread.join(2)
            assert not cloud._disconnect_cleanup_thread.is_alive()
        else:
            release.set()
            assert delivered.wait(2)
            assert cloud.disconnect() is True
        cloud._thread.join(2)
        assert not cloud._thread.is_alive()
        assert cloud.disconnect() is True
        assert cloud._queue.unfinished_tasks == 0
    finally:
        release.set()
        if cloud._thread is not None:
            cloud._thread.join(2)
        cloud.disconnect()


def test_timer_creation_and_disconnect_share_one_cancellation_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Close waits for an admitted timer, then cancels it before returning."""
    mower = device_module.DreameMowerDevice("Timer race", None, None)
    mower._map_manager = None
    constructing, release, cancel_entered, closed = Event(), Event(), Event(), Event()
    timer = Mock()
    original_schedule = mower.schedule_update

    def create_timer(*args: Any, **kwargs: Any) -> Any:
        constructing.set()
        assert release.wait(2)
        return timer

    def schedule(wait: float, *args: Any) -> None:
        if wait < 0:
            cancel_entered.set()
        original_schedule(wait, *args)

    def close() -> None:
        mower.disconnect()
        closed.set()

    monkeypatch.setattr(device_module, "Timer", create_timer)
    mower.schedule_update = schedule
    producer = Thread(target=lambda: mower.schedule_update(5), daemon=True)
    closer = Thread(target=close, daemon=True)
    try:
        producer.start()
        assert constructing.wait(2)
        closer.start()
        assert cancel_entered.wait(2)
        closed.wait(0.1)
        release.set()
        producer.join(2)
        closer.join(2)
        assert not producer.is_alive() and not closer.is_alive()
        assert closed.is_set()
        timer.start.assert_called_once()
        timer.cancel.assert_called_once()
        assert mower._update_timer is None
    finally:
        release.set()
        if producer.ident is not None:
            producer.join(2)
        if closer.ident is not None:
            closer.join(2)
        mower.disconnect()


def test_superseded_timer_callback_cannot_clear_new_timer_or_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An old timer that already woke up cannot take ownership from its replacement."""
    mower = device_module.DreameMowerDevice("Timer replacement", None, None)
    mower._map_manager = None
    timers: list[tuple[Any, Any]] = []

    def create_timer(wait: float, callback: Any) -> Any:
        timer = Mock()
        timers.append((timer, callback))
        return timer

    monkeypatch.setattr(device_module, "Timer", create_timer)
    mower.update = Mock()
    try:
        mower.schedule_update(5)
        mower.schedule_update(10)
        first, replacement = timers[:2]
        first[0].cancel.assert_called_once()
        first[1]()
        mower.update.assert_not_called()
        assert mower._update_timer is replacement[0]
    finally:
        mower.disconnect()
