"""Map identity and download-integrity regressions for paired saved lawns."""

from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    client_map_helpers,
)
from dreame_lawn_mower_client import DreameLawnMowerConnectionError
from tests.test_app_maps import _client, _FakeAppMapCloud


def test_unavailable_current_map_does_not_select_another_lawn() -> None:
    other = {"idx": 1, "available": True, "payload": {"map": []}}
    assert (
        client_map_helpers._select_app_map_payload(
            {
                "current_map_index": 0,
                "maps": [{"idx": 0, "available": False}, other],
            }
        )
        is None
    )
    assert client_map_helpers._select_app_map_payload({"maps": [other]}) is other


def test_incomplete_map_read_restarts_from_mapi_once() -> None:
    client = _client()
    cloud = _FakeAppMapCloud({"map": [{"data": [[0, 0], [20, 0], [20, 20]]}]})
    original = cloud.call_app_action
    failed = False

    def transient_chunk(payload, **kwargs):
        nonlocal failed
        if payload.get("t") == "MAPD" and not failed:
            failed = True
            cloud.calls.append(payload)
            return {"out": [{"d": {"data": "{", "size": 40}}]}
        return original(payload, **kwargs)

    cloud.call_app_action = transient_chunk
    client._sync_get_cloud_protocol = lambda: cloud
    result = client._sync_get_app_maps(include_payload=True, include_objects=False)
    entry = result["maps"][0]
    assert entry["available"] is True
    assert entry["download_attempts"] == 2
    assert entry["hash_match"] is True
    assert [c["t"] for c in cloud.calls].count("MAPI") == 2
    assert entry["payload"] == json.loads(cloud.payload_text)


@pytest.mark.parametrize("reported_size", [0, -1, True, 20, "3"])
def test_invalid_chunk_sizes_are_not_used_as_offsets(reported_size) -> None:
    client = _client()
    client._sync_call_app_action = lambda payload: {
        "d": {"data": "abc", "size": reported_size}
    }
    with pytest.raises(DreameLawnMowerConnectionError, match="chunk size mismatch"):
        client._sync_get_app_map_text(size=3, chunk_size=3)


def test_escaped_chunk_overhead_uses_transport_offsets() -> None:
    client = _client()
    calls = []

    def chunk(payload):
        calls.append(payload["d"]["start"])
        if payload["d"]["start"] == 0:
            return {"d": {"data": '{"a":', "size": 7}}
        return {"d": {"data": "12}", "size": 3}}

    client._sync_call_app_action = chunk
    assert client._sync_get_app_map_text(size=10, chunk_size=7) == ('{"a":12}', 2, 10)
    assert calls == [0, 7]


def test_map_integrity_hash_covers_decoded_text_not_transport_escape_overhead() -> None:
    client = _client()
    text = '{"map":[]}'
    wire_size = len(text) + 2
    client._sync_call_app_action = lambda payload: {
        "d": {"size": wire_size, "hash": hashlib.md5(text.encode()).hexdigest()}
    }
    client._sync_get_app_map_text = lambda **kwargs: (text, 1, wire_size)
    entry = {"idx": 0}
    client._sync_download_app_map(entry, chunk_size=400, include_payload=True)
    assert entry["available"] is True
    assert entry["hash_match"] is True
    assert entry["decoded_size"] == len(text)
    assert entry["received_size"] == wire_size
    assert entry["payload"] == {"map": []}


def test_concurrent_readers_do_not_interleave_selected_map_downloads() -> None:
    client = _client()
    cloud = _FakeAppMapCloud({"map": [{"data": [[0, 0], [20, 0], [20, 20]]}]})
    original = cloud.call_app_action
    first_read = threading.Event()
    second_read_started = threading.Event()
    first_thread = None
    interleaved = []

    def observe(payload, **kwargs):
        nonlocal first_thread
        if payload.get("t") == "MAPI":
            if first_thread is None:
                first_thread = threading.get_ident()
                first_read.set()
                assert second_read_started.wait(timeout=5)
            elif threading.get_ident() != first_thread and not first_finished.is_set():
                interleaved.append(payload["t"])
        result = original(payload, **kwargs)
        if (
            payload.get("t") == "MAPD"
            and threading.get_ident() == first_thread
            and payload["d"]["start"] + result["out"][0]["d"]["size"]
            >= len(cloud.payload_text.encode("utf-8"))
        ):
            first_finished.set()
        return result

    cloud.call_app_action = observe
    client._sync_get_cloud_protocol = lambda: cloud
    first_finished = threading.Event()

    def read_first():
        result = client._sync_get_app_maps(include_objects=False)
        first_finished.set()
        return result

    def read_second():
        assert first_read.wait(timeout=5)
        second_read_started.set()
        return client._sync_get_app_maps(include_objects=False)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(read_first)
        second = pool.submit(read_second)
        assert first.result(timeout=10)["available"] is True
        assert second.result(timeout=10)["available"] is True
    assert interleaved == []
