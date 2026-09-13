"""Evidence must survive retries, outer deadlines, and public serialization."""

from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from dreame_lawn_mower_client import DreameLawnMowerPointCloudError, parse_pcd_metadata
from dreame_lawn_mower_client._loader import load_internal_module
from tests.test_point_cloud import _binary_pcd, _client

trace_module = load_internal_module("point_cloud_trace")
diagnostics = load_internal_module("point_cloud_diagnostics")
client_module = load_internal_module("client")
helpers = load_internal_module("client_map_helpers")


def test_timeline_keeps_early_failure_and_latest_observations_bounded():
    trace = trace_module.PointCloudTrace()
    trace.record("queue")
    trace.record(
        "validation_result", {"validation_reason": "encoding", "url": "private"}
    )
    for count in range(100):
        trace.record("poll_result", {"announcement_polls": count})
    report = trace.snapshot(complete=False)
    assert len(report["timeline"]) == 32
    assert report["trace_event_count"] == 102
    assert report["trace_dropped_events"] == 70
    assert report["timeline"][1]["observation"]["validation_reason"] == "encoding"
    assert report["timeline"][-1]["observation"]["announcement_polls"] == 99
    assert report["worker_finished"] is False
    assert "private" not in json.dumps(report)


def test_first_failure_survives_when_middle_of_timeline_is_dropped():
    trace = trace_module.PointCloudTrace()
    for count in range(100):
        trace.record("poll_result", {"announcement_polls": count})
        if count == 20:
            trace.record(
                "download_result",
                {
                    "download_reason": "http_error",
                    "download_http_status": 403,
                },
            )
    report = trace.snapshot(complete=True)
    assert report["first_failure"] == {
        "download_reason": "http_error",
        "download_http_status": 403,
    }
    assert not any(
        event["observation"].get("download_reason") for event in report["timeline"]
    )


def test_outer_timeout_keeps_thread_evidence_and_immutable_snapshot(monkeypatch):
    client = _client()
    release = threading.Event()
    started = threading.Event()
    monkeypatch.setattr(client_module, "_POINT_CLOUD_CLOUD_SETUP_TIMEOUT_SECONDS", 0)
    monkeypatch.setattr(
        client_module, "_POINT_CLOUD_GENERATION_PREFLIGHT_BUDGET_SECONDS", 0
    )

    def blocked_download(*args):
        trace_module.record_point_cloud_stage("signer", {"download_attempts": 2})
        started.set()
        assert release.wait(3)
        trace_module.record_point_cloud_stage("download", {"download_attempts": 3})
        raise DreameLawnMowerPointCloudError("private late failure")

    client._sync_download_app_map_point_cloud = blocked_download

    async def run():
        task = asyncio.create_task(
            client.async_download_app_map_point_cloud(timeout=0.1)
        )
        try:
            assert await asyncio.to_thread(started.wait, 2)
            with pytest.raises(DreameLawnMowerPointCloudError) as failure:
                await task
            report = failure.value.safe_diagnostics()
            before = json.dumps(report, sort_keys=True)
            assert report["attempt"]["worker_finished"] is False
            assert [
                event["trace_stage"] for event in report["attempt"]["timeline"]
            ] == [
                "queue",
                "signer",
                "outer_timeout",
            ]
            assert report["attempt"]["download_attempts"] == 2
        finally:
            release.set()
        await asyncio.sleep(0.02)
        assert json.dumps(report, sort_keys=True) == before
        assert trace_module.active_point_cloud_trace.get() is None

    asyncio.run(run())


def test_concurrent_request_traces_are_isolated():
    clients = [_client(), _client()]
    barrier = threading.Barrier(2)

    def fail(map_index, *args):
        trace_module.record_point_cloud_stage("indexed_read", {"map_index": map_index})
        barrier.wait(timeout=2)
        raise DreameLawnMowerPointCloudError("private")

    for client in clients:
        client._sync_download_app_map_point_cloud = fail

    async def run():
        failures = await asyncio.gather(
            *(
                client.async_download_app_map_point_cloud(map_index=index)
                for index, client in enumerate(clients)
            ),
            return_exceptions=True,
        )
        reports = [error.safe_diagnostics()["attempt"] for error in failures]
        assert reports[0]["attempt_id"] != reports[1]["attempt_id"]
        for index, report in enumerate(reports):
            assert report["worker_finished"] is True
            assert report["timeline"][1]["observation"]["map_index"] == index
        assert trace_module.active_point_cloud_trace.get() is None

    asyncio.run(run())


def test_unexpected_transport_failure_keeps_last_stage_without_private_text():
    client = _client()

    def fail(*args, **kwargs):
        raise KeyError("private object URL")

    client._sync_get_cloud_protocol = lambda **options: SimpleNamespace(
        get_properties=fail,
        get_interim_file_url=lambda *args, **kwargs: None,
    )
    with pytest.raises(DreameLawnMowerPointCloudError) as failure:
        asyncio.run(client.async_download_app_map_point_cloud())
    report = failure.value.safe_diagnostics()["attempt"]
    assert report["exception_kind"] == "KeyError"
    assert report["worker_finished"] is True
    assert [event["trace_stage"] for event in report["timeline"]][-2:] == [
        "announcement_read",
        "failed",
    ]
    assert "private" not in json.dumps(report)


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b"", "empty_content"),
        (b"private\xff\n", "header_non_ascii"),
        (b"private text\n", "missing_data_declaration"),
        (
            _binary_pcd((1, 2, 3, 0)).replace(b"binary\n", b"binary_compressed\n"),
            "encoding",
        ),
        (_binary_pcd((1, 2, 3, 0))[:-1], "binary_payload_length"),
        (_binary_pcd((float("nan"), 2, 3, 0)), "nonfinite_coordinates"),
        (
            _binary_pcd((1, 2, 3, 0)).replace(b"SIZE 4 4 4 4\n", b""),
            "missing_header_value",
        ),
    ],
)
def test_validation_rule_survives_public_projection(content, reason):
    with pytest.raises(DreameLawnMowerPointCloudError) as failure:
        parse_pcd_metadata(content)
    assert failure.value.safe_diagnostics()["attempt"]["validation_reason"] == reason


def test_unfamiliar_json_schema_is_described_without_values_or_unknown_keys():
    value = json.dumps({"objectName": "private/key", "idx": 1, "private-key": "secret"})
    observation = diagnostics.property_value_observation(value)
    safe = diagnostics.safe_attempt_diagnostics({"latest_announcement": observation})
    assert safe["latest_announcement"] == {
        "value_shape": "nonempty_string",
        "string_form": "json_object",
        "member_shapes": {"objectName": "nonempty_string", "idx": "number"},
        "unknown_member_count": 1,
    }
    assert "private" not in json.dumps(safe)
    assert "secret" not in json.dumps(safe)


def test_trace_boundary_does_not_recurse_into_nested_timeline():
    cyclic = {"trace_stage": "download", "elapsed_ms": 1}
    cyclic["observation"] = {"timeline": [cyclic], "member_shapes": {"url": "private"}}
    safe = diagnostics.safe_attempt_diagnostics({"timeline": [cyclic] * 100})
    assert len(safe["timeline"]) == 32
    assert safe["timeline"][0]["observation"] == {"member_shapes": {}}


def test_real_client_trace_reaches_http_and_saved_event(monkeypatch):
    from custom_components.dreame_lawn_mower.const import DOMAIN
    from custom_components.dreame_lawn_mower.diagnostic_events import (
        DreameLawnMowerDiagnosticEventStore,
    )
    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
        DreameLawnMowerClient,
    )
    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
        client_map_helpers as ha_helpers,
    )
    from custom_components.dreame_lawn_mower.point_cloud_api import (
        DreameLawnMowerPointCloudAPI,
        DreameLawnMowerPointCloudView,
    )
    from tests.test_point_cloud import _FakeResponse
    from tests.test_point_cloud_api import _FakeRequest

    client = DreameLawnMowerClient(
        username="private",
        password="private",
        country="eu",
        account_type="dreame",
        descriptor=_client()._descriptor,
    )
    generation = []

    def action(payload, **options):
        if payload.get("m") == "a":
            generation.append(payload)
            options["on_dispatch"]()
        return {"r": 0, "d": {"name": []}}

    client._sync_call_app_action = action
    client._sync_get_cloud_protocol = lambda **options: SimpleNamespace(
        get_properties=lambda key, **kwargs: [
            {
                "key": key,
                "value": "private/cloud.pcd" if generation else "",
                "updateDate": 1_900_000_000_000,
            }
        ],
        get_interim_file_url=lambda *args, **kwargs: "https://example.invalid/private",
    )
    content = _binary_pcd((1, 2, 3, 0)).replace(b"binary\n", b"binary_compressed\n")
    monkeypatch.setattr(
        ha_helpers,
        "_open_point_cloud_response",
        lambda *args, **kwargs: _FakeResponse(
            content, "https://example.invalid/private"
        ),
    )
    coordinator = SimpleNamespace(
        client=client, diagnostic_events=DreameLawnMowerDiagnosticEventStore()
    )
    api = DreameLawnMowerPointCloudAPI(
        SimpleNamespace(data={DOMAIN: {"entry-1": coordinator}})
    )
    # Use the real client's public timing option to keep the failure test short.
    download = client.async_download_app_map_point_cloud

    async def short_download(**options):
        return await download(timeout=0.05, poll_interval=0.001, **options)

    client.async_download_app_map_point_cloud = short_download
    view = DreameLawnMowerPointCloudView(api)

    async def run():
        first = await view.get(_FakeRequest(is_admin=True), "entry-1", "0")
        repeated = await view.get(_FakeRequest(is_admin=True), "entry-1", "0")
        return json.loads(first.text), json.loads(repeated.text)

    response, repeated = asyncio.run(run())
    report = response["diagnostics"]["attempt"]
    assert report["worker_finished"] is True
    assert any(
        event["observation"].get("validation_reason") == "encoding"
        for event in report["timeline"]
    )
    assert report == repeated["diagnostics"]["attempt"]
    event = coordinator.diagnostic_events.as_list()[0]
    assert event["context"]["attempt"] == report
    assert len(generation) == 1
    assert "private" not in json.dumps(report)
