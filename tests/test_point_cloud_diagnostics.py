"""One-attempt evidence must distinguish protocol states without leaking values."""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
from types import SimpleNamespace

import pytest

from dreame_lawn_mower_client import DreameLawnMowerPointCloudError
from dreame_lawn_mower_client._loader import load_internal_module
from tests.test_point_cloud import _client, _mova_client

helpers = load_internal_module("client_map_helpers")
diagnostics = load_internal_module("point_cloud_diagnostics")


@pytest.mark.parametrize(
    ("names", "index", "expected"),
    [
        ([None, None], 0, "null"),
        (["", ""], 0, "empty_string"),
        (["private-key", ""], 0, "nonempty_string"),
        ([{"private": "key"}], 0, "mapping"),
        (["private.bin"], 1, "missing"),
    ],
)
def test_indexed_observation_distinguishes_ambiguous_slots(names, index, expected):
    result = diagnostics.indexed_object_observation({"name": names}, index)
    assert result == {
        "data_shape": "mapping",
        "names_shape": "sequence",
        "slot_count": len(names),
        "selected_shape": expected,
    }
    assert "private" not in json.dumps(result)


def test_inventory_reports_names_and_unchecked_signer_without_network():
    client = _client()
    client._sync_call_app_action = lambda *args, **kwargs: {
        "r": 0,
        "d": {"name": [None, "", "private-key", "private/file.bin"]},
    }
    client._sync_get_cloud_protocol = lambda **kwargs: pytest.fail(
        "No signer requested"
    )
    inventory = client._sync_get_app_map_objects()
    view = helpers._app_map_objects_view_metadata(inventory)
    assert inventory["object_count"] == view["object_count"] == 4
    assert inventory["named_object_count"] == view["named_object_count"] == 2
    assert [item["name_shape"] for item in view["objects"]] == [
        "null",
        "empty_string",
        "nonempty_string",
        "nonempty_string",
    ]
    assert all(item["url_checked"] is False for item in view["objects"])
    assert "private" not in json.dumps(view)


@pytest.mark.parametrize(
    ("value", "timestamp", "status", "extension"),
    [
        ("", 1, "invalid_value", None),
        (None, 1, "invalid_value", None),
        ({"private": "key"}, 1, "invalid_value", None),
        ("private/file.pcd", None, "invalid_timestamp", None),
        ("private/file.pcd", "invalid", "invalid_timestamp", None),
        ("private/key", 1_800_000_000_000, "unsupported_extension", "missing"),
        (
            "private/file.secret",
            1_800_000_000_000,
            "unsupported_extension",
            "unsupported",
        ),
        ("private/file.pcd", 1_800_000_000, "stale", "pcd"),
        ("private/file.BIN", 1_800_000_000_000, "fresh", "bin"),
    ],
)
def test_announcement_explains_filtered_values_without_changing_acceptance(
    value,
    timestamp,
    status,
    extension,
):
    client = _client()
    observation = {}
    cloud = SimpleNamespace(
        get_properties=lambda *args, **kwargs: [
            {"key": "99.20", "value": value, "updateDate": timestamp},
        ]
    )
    available, name, _ = client._sync_get_announced_point_cloud_object(
        cloud,
        requested_after_ms=1_799_999_999_000,
        require_post_request=True,
        deadline=time.monotonic() + 1,
        observation=observation,
    )
    assert available is True
    assert (name is not None) is (status == "fresh")
    assert observation["status"] == status
    assert observation.get("object_extension") == extension
    if timestamp == 1_800_000_000:
        assert observation["timestamp_unit"] == "seconds"
    assert "private" not in json.dumps(observation)


@pytest.mark.parametrize("names", [[None, None], ["", ""], ["private-key", ""]])
def test_viax_attempt_preserves_empty_and_unrecognized_indexed_evidence(names):
    # These are distinct possible raw states behind issue #52's lossy summaries,
    # not claimed captures of the reporter's raw firmware responses.
    client = _mova_client(model="mova.mower.g2583", display_model="VIAX 500")
    generations = 0

    def action(payload, **options):
        nonlocal generations
        if payload.get("m") == "a":
            generations += 1
            options["on_dispatch"]()
            return {"r": 0}
        return {"r": 0, "d": {"name": names}}

    client._sync_call_app_action = action
    client._sync_get_cloud_protocol = lambda **options: SimpleNamespace(
        get_properties=lambda *args, **kwargs: [{"key": "99.20", "value": ""}],
        get_interim_file_url=lambda *args, **kwargs: pytest.fail("No usable object"),
    )
    with pytest.raises(DreameLawnMowerPointCloudError) as failure:
        client._sync_download_app_map_point_cloud(0, 0.03, 0.001, 10, 1024)
    report = failure.value.safe_diagnostics()
    assert report["generation_acknowledged"] is True
    assert generations == 1
    assert report["attempt"]["download_attempts"] == 0
    assert report["attempt"]["latest_announcement"]["status"] == "invalid_value"
    assert report["attempt"]["latest_indexed"][
        "selected_shape"
    ] == diagnostics.value_shape(names[0])
    assert report["attempt"]["latest_indexed"]["slot_count"] == 2
    assert "private" not in json.dumps(report)


def test_malformed_action_retains_reply_shape_without_echoing_content():
    client = _client()
    client._sync_call_app_action = lambda *args, **kwargs: {
        "r": 0,
        "d": {"name": {"private-map": "private-object"}},
    }
    with pytest.raises(DreameLawnMowerPointCloudError) as failure:
        client._sync_call_point_cloud_action(
            {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
            operation="read objects",
            deadline=time.monotonic() + 1,
            require_data=True,
        )
    report = failure.value.safe_diagnostics()
    assert report["attempt"]["action_reply"]["names_shape"] == "mapping"
    assert report["attempt"]["action_reply"]["result_status"] == "accepted"
    assert "private" not in json.dumps(report)


def test_http_failure_retains_status_without_url(monkeypatch):
    secret = "https://example.invalid/private.pcd?token=private"

    def fail(*args, **kwargs):
        raise urllib.error.HTTPError(secret, 403, "private", {}, None)

    monkeypatch.setattr(helpers, "_open_point_cloud_response", fail)
    client = _client()
    observation = {}
    with pytest.raises(DreameLawnMowerPointCloudError) as failure:
        client._sync_download_point_cloud_object(
            SimpleNamespace(get_interim_file_url=lambda *args, **kwargs: secret),
            "private.pcd",
            deadline=time.monotonic() + 1,
            download_timeout=1,
            max_bytes=1024,
            observation=observation,
        )
    assert observation["last_download_step"] == "download"
    assert observation["signer_shape"] == "nonempty_string"
    assert failure.value.safe_diagnostics()["attempt"]["download_http_status"] == 403
    assert "private" not in json.dumps(observation)


def test_public_trace_filters_recognized_keys_as_well_as_unknown_keys():
    error = DreameLawnMowerPointCloudError(
        "private",
        diagnostic_reason="private",
        discovery_route="private",
        diagnostic_context={
            "download_attempts": True,
            "last_download_result": "private-url",
            "private": "private",
            "initial_announcement": {
                "status": "invalid_value",
                "value_shape": "private",
                "initial_announcement": {"status": "private"},
            },
            "download_http_status": 403,
        },
    )
    assert error.safe_diagnostics() == {
        "attempt": {
            "download_http_status": 403,
            "initial_announcement": {"status": "invalid_value"},
        }
    }


def test_probe_emits_safe_failure_and_closes_client(monkeypatch, capsys):
    from examples import point_cloud_probe

    closed = []

    class Client:
        def __init__(self, **kwargs):
            pass

        @staticmethod
        async def async_discover_devices(**kwargs):
            return [SimpleNamespace()]

        async def async_download_app_map_point_cloud(self, **kwargs):
            raise DreameLawnMowerPointCloudError(
                "private-url",
                diagnostic_context={"download_attempts": 0},
            )

        async def async_close(self):
            closed.append(True)

    monkeypatch.setenv("DREAME_USERNAME", "private")
    monkeypatch.setenv("DREAME_PASSWORD", "private")
    monkeypatch.setattr(point_cloud_probe, "DreameLawnMowerClient", Client)
    monkeypatch.setattr("sys.argv", ["probe"])
    assert asyncio.run(point_cloud_probe.main()) == 1
    output = capsys.readouterr().out
    assert json.loads(output)["diagnostics"]["attempt"]["download_attempts"] == 0
    assert "private" not in output
    assert closed == [True]
