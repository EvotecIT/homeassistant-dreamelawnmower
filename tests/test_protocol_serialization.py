"""Shared Dreame cloud transport serialization contracts."""

from __future__ import annotations

from threading import Event, RLock, Thread

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
