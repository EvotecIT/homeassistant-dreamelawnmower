"""Shared Dreame cloud transport serialization contracts."""

from __future__ import annotations

import time
from collections.abc import Callable
from threading import Event, RLock, Thread

import requests

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    protocol_cloud,
)


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

    assert disconnected is False
    assert elapsed < 0.5
    assert cloud._connected is False
    assert cloud._logged_in is False
    assert cloud._message_callback is None
    assert cloud._connected_callback is None


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
