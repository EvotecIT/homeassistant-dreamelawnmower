"""Cold app-map downloads have one bounded resource and time budget."""

import hashlib
import time

import pytest

from dreame_lawn_mower_client import DreameLawnMowerConnectionError
from dreame_lawn_mower_client._loader import load_internal_module

owner = load_internal_module("map_read_plan")


def chunk(text="x"):
    return {"d": {"data": text, "size": len(text)}}


def test_oversized_announced_map_is_rejected_before_requesting_chunks():
    plan = owner.download_map({}, {"idx": 0}, chunk_size=400, include_payload=True)
    assert next(plan).action["t"] == "MAPI"
    with pytest.raises(DreameLawnMowerConnectionError, match="budget"):
        plan.send({"d": {"size": 4 * 1024 * 1024 + 1, "hash": "synthetic"}})


def test_tiny_chunks_cannot_exceed_the_shared_chunk_budget(monkeypatch):
    monkeypatch.setattr(owner, "MAX_APP_MAP_CHUNKS", 2, raising=False)
    plan = owner.read_map_text(size=4, chunk_size=400)
    next(plan)
    plan.send(chunk())
    with pytest.raises(DreameLawnMowerConnectionError, match="budget"):
        plan.send(chunk())


def test_chunk_requests_share_one_deadline_instead_of_renewing_it(monkeypatch):
    now = 10.0
    monkeypatch.setattr(time, "monotonic", lambda: now)
    plan = owner.read_map_text(size=3, chunk_size=1)
    first = next(plan)
    assert first.deadline == 70.0
    now = 20.0
    second = plan.send(chunk())
    assert second.deadline == first.deadline
    now = 71.0
    with pytest.raises(DreameLawnMowerConnectionError, match="deadline"):
        plan.send(chunk())


def test_inventory_cannot_multiply_the_total_payload_budget(monkeypatch):
    monkeypatch.setattr(owner, "MAX_APP_MAP_TOTAL_BYTES", 3, raising=False)
    calls = []

    def dispatch(action, **kwargs):
        calls.append(action["t"])
        if action["t"] == "MAPL":
            return {"d": [[0, 1, 1, 1], [1, 0, 1, 1]]}
        if action["t"] == "MAPI":
            return {"d": {"size": 2, "hash": hashlib.md5(b"{}").hexdigest()}}
        return chunk("{}")

    driver = load_internal_module("app_read_transport")
    result = driver.run_app_read(owner.read_maps({}, 400, True), dispatch)
    assert result["maps"][0]["available"] is True
    assert result["maps"][1]["available"] is False
    assert "byte budget" in result["maps"][1]["error"]
    assert calls.count("MAPD") == 1


def test_sync_map_driver_propagates_one_deadline_to_the_transport():
    from .test_app_maps import _client, _FakeAppMapCloud

    client = _client()
    cloud = _FakeAppMapCloud({"map": []})
    original = cloud.call_app_action
    options = []

    def dispatch(action, **kwargs):
        options.append(kwargs)
        return original(action, **kwargs)

    cloud.call_app_action = dispatch
    client._sync_get_cloud_protocol = lambda **kwargs: cloud
    assert client._sync_get_app_maps(include_objects=False)["available"] is True
    assert len(options) >= 3
    deadlines = {item["deadline"] for item in options}
    assert len(deadlines) == 1
    assert next(iter(deadlines)) > time.monotonic()
    assert all(0 < item["timeout"] <= 20 for item in options)
