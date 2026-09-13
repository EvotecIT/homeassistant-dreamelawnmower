"""Discovery must distinguish an empty export from an unreadable result."""

from types import SimpleNamespace

import pytest

import dreame_lawn_mower_client.client as client_module
from dreame_lawn_mower_client import DreameLawnMowerPointCloudError
from tests.test_point_cloud import (
    _binary_pcd,
    _client,
    _FakeResponse,
    _internal_client_module,
    _mova_client,
)


@pytest.mark.parametrize(
    "route", ["legacy", "announcement", "viax_indexed", "stable_indexed"]
)
@pytest.mark.parametrize(
    ("pattern", "retryable"),
    [
        ("unreadable", True),
        ("empty", False),
        ("recovered", False),
        ("failed_late", True),
        ("malformed", True),
        ("no_poll_budget", True),
    ],
)
def test_discovery_classifies_post_dispatch_evidence(
    monkeypatch, route, pattern, retryable
):
    """Use the real discovery loop and action envelope, faking only cloud and time."""
    client = (
        _mova_client(model="mova.mower.g2583", display_model="VIAX 500")
        if route == "viax_indexed"
        else _client()
    )
    clock = [100.0]
    dispatched = False
    generation_calls = 0
    reads = 0

    def unreadable():
        nonlocal reads
        reads += 1
        return (
            pattern == "unreadable"
            or pattern == "recovered"
            and reads == 1
            or pattern == "failed_late"
            and clock[0] >= 102.0
        )

    def properties(key, **kwargs):
        if route == "legacy":
            return []
        if dispatched and route == "announcement":
            if unreadable():
                return None
            if pattern == "malformed":
                return [{"key": key, "value": {"name": "private"}}]
        return [
            {
                "key": key,
                "value": ("private/stable.bin" if route == "stable_indexed" else ""),
                "updateDate": 1,
            }
        ]

    def action(payload, **kwargs):
        nonlocal dispatched, generation_calls
        if payload["m"] == "a":
            generation_calls += 1
            kwargs["on_dispatch"]()
            dispatched = True
            if pattern == "no_poll_budget":
                clock[0] += 5
            return {"r": 0}
        if dispatched:
            if unreadable():
                raise DreameLawnMowerPointCloudError(
                    "private transport detail",
                    code="point_cloud_timeout",
                )
            if pattern == "malformed":
                return {"r": 0, "d": {"name": [{"name": "private/unfamiliar.bin"}]}}
        return {"r": 0, "d": {"name": ["", ""]}}

    client._sync_get_cloud_protocol = lambda **kwargs: SimpleNamespace(
        get_properties=properties,
        get_interim_file_url=lambda *args, **kwargs: None,
    )
    client._sync_call_app_action = action
    monkeypatch.setattr(client_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(client_module.time, "time", lambda: 100.0)
    monkeypatch.setattr(
        client_module.time,
        "sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )

    with pytest.raises(DreameLawnMowerPointCloudError) as failure:
        client._sync_download_app_map_point_cloud(0, 5, 0.25, 10, 1024)

    error = failure.value
    assert error.retryable is retryable
    assert error.code == (
        "point_cloud_timeout" if retryable else "point_cloud_not_published"
    )
    safe = error.safe_diagnostics()
    assert safe["reason"] == (
        "polling_inconclusive" if retryable else "object_not_observed"
    )
    assert safe["attempt"]["download_attempts"] == 0
    assert safe["attempt"][
        "announcement_poll_result" if route == "announcement" else "indexed_poll_result"
    ] == (
        "not_attempted"
        if pattern == "no_poll_budget"
        else "inconclusive"
        if retryable
        else "observed"
    )
    assert "private" not in str(safe)
    assert generation_calls == 1


@pytest.mark.parametrize(
    "error_code", ["point_cloud_timeout", "point_cloud_mower_request_failed"]
)
def test_failed_legacy_baseline_and_polls_remain_retryable(monkeypatch, error_code):
    client = _client()
    clock = [100.0]
    generation_calls = 0

    def action(payload, **kwargs):
        nonlocal generation_calls
        if payload["m"] == "a":
            generation_calls += 1
            kwargs["on_dispatch"]()
            return {"r": 0}
        raise DreameLawnMowerPointCloudError("private", code=error_code)

    client._sync_get_cloud_protocol = lambda **kwargs: SimpleNamespace(
        get_properties=lambda *args, **kwargs: [],
        get_interim_file_url=lambda *args, **kwargs: None,
    )
    client._sync_call_app_action = action
    monkeypatch.setattr(client_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(client_module.time, "time", lambda: 100.0)
    monkeypatch.setattr(
        client_module.time,
        "sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    with pytest.raises(DreameLawnMowerPointCloudError) as failure:
        client._sync_download_app_map_point_cloud(0, 5, 0.25, 10, 1024)
    assert failure.value.code == "point_cloud_timeout"
    assert failure.value.retryable is True
    assert failure.value.safe_diagnostics()["reason"] == "polling_inconclusive"
    assert generation_calls == 1


@pytest.mark.parametrize("malformed_at", ["preflight", "poll", "baseline_recovery"])
@pytest.mark.parametrize("extension", ["pcd", "bin"])
@pytest.mark.parametrize("recovery", ["unchanged", "clear", "changed"])
@pytest.mark.parametrize("slot_shape", ["mapping", "missing"])
def test_unfamiliar_slots_cannot_establish_freshness(
    monkeypatch,
    malformed_at,
    extension,
    recovery,
    slot_shape,
):
    """Unreadable slots are neither an empty baseline nor a confirmed clear."""
    client = _client()
    clock = [100.0]
    old_name = f"private/old.{extension}"
    new_name = f"private/new.{extension}"
    malformed = {"name": [{"name": old_name}] if slot_shape == "mapping" else []}
    preflight = (
        malformed
        if malformed_at == "preflight"
        else DreameLawnMowerPointCloudError("private", code="point_cloud_timeout")
        if malformed_at == "baseline_recovery"
        else {"name": [old_name]}
    )
    replies = [preflight]
    if malformed_at != "preflight":
        replies.append(malformed)
    replies.append({"name": [old_name]})
    if recovery == "clear":
        replies.append({"name": [""]})
    final = {"name": [new_name if recovery == "changed" else old_name]}

    def action(payload, **kwargs):
        if payload["m"] == "a":
            kwargs["on_dispatch"]()
            return {"r": 0}
        reply = replies.pop(0) if replies else final
        if isinstance(reply, DreameLawnMowerPointCloudError):
            raise reply
        return {"r": 0, "d": reply}

    content = _binary_pcd((1.0, 2.0, 3.0, 0x123456))
    client._sync_call_app_action = action
    client._sync_get_cloud_protocol = lambda **kwargs: SimpleNamespace(
        get_properties=lambda *args, **kwargs: [],
        get_interim_file_url=lambda *args, **kwargs: (
            "https://downloads.example.invalid/object"
        ),
    )
    monkeypatch.setattr(
        _internal_client_module,
        "_open_point_cloud_response",
        lambda request, **kwargs: _FakeResponse(content, request.full_url),
    )
    monkeypatch.setattr(client_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(client_module.time, "time", lambda: 100.0)
    monkeypatch.setattr(
        client_module.time,
        "sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    if recovery == "unchanged":
        with pytest.raises(DreameLawnMowerPointCloudError):
            client._sync_download_app_map_point_cloud(0, 5, 0.25, 10, 1024)
    else:
        result = client._sync_download_app_map_point_cloud(0, 5, 0.25, 10, 1024)
        assert result.content == content
        assert result.source == "generated"
