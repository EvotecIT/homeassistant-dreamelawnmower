"""Shared Dreame cloud transport serialization contracts."""

from __future__ import annotations

import time
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
