"""Regression checks for mower-native app map retrieval."""

from __future__ import annotations

import hashlib
import json

from dreame_lawn_mower_client import (
    DreameLawnMowerClient,
    DreameLawnMowerConnectionError,
)
from dreame_lawn_mower_client.models import DreameLawnMowerDescriptor


class _FakeAppMapCloud:
    logged_in = True

    def __init__(
        self,
        payload: dict[str, object],
        *,
        chunk_overrides: dict[int, tuple[str, int]] | None = None,
    ) -> None:
        self.payload_text = json.dumps(payload, separators=(",", ":"))
        self.payload_hash = hashlib.md5(self.payload_text.encode("utf-8")).hexdigest()
        self.chunk_overrides = chunk_overrides or {}
        self.calls: list[dict[str, object]] = []

    def call_app_action(
        self,
        payload: dict[str, object],
        *,
        siid: int = 2,
        aiid: int = 50,
    ) -> dict[str, object]:
        assert siid == 2
        assert aiid == 50
        self.calls.append(payload)
        command = payload.get("t")
        if command == "MAPL":
            return {
                "out": [
                    {
                        "m": "r",
                        "r": 0,
                        "d": [[0, 1, 1, 1, 0], [1, 0, 0, 0, 0]],
                    }
                ]
            }
        if command == "MAPI":
            return {
                "out": [
                    {
                        "m": "r",
                        "r": 0,
                        "d": {
                            "idx": payload["d"]["idx"],
                            "size": len(self.payload_text.encode("utf-8")),
                            "hash": self.payload_hash,
                        },
                    }
                ]
            }
        if command == "MAPD":
            data = payload["d"]
            start = int(data["start"])
            size = int(data["size"])
            if start in self.chunk_overrides:
                text, reported_size = self.chunk_overrides[start]
                size = reported_size
            else:
                payload_bytes = self.payload_text.encode("utf-8")
                text = payload_bytes[start : start + size].decode("utf-8")
                size = len(text.encode("utf-8"))
            return {
                "out": [
                    {
                        "m": "r",
                        "r": 0,
                        "d": {"size": size, "data": text},
                    }
                ]
            }
        if command == "OBJ":
            return {
                "out": [
                    {
                        "m": "r",
                        "r": 0,
                        "d": {
                            "name": [
                                "ali_dreame/2025/04/23/device/map-one.0233.bin"
                            ]
                        },
                    }
                ]
            }
        raise AssertionError(f"Unexpected app command: {payload}")

    def get_interim_file_url(self, name: str) -> str:
        return f"https://example.invalid/{name}?signature=redacted"


def _client() -> DreameLawnMowerClient:
    return DreameLawnMowerClient(
        username="user@example.invalid",
        password="secret",
        country="eu",
        account_type="dreame",
        descriptor=DreameLawnMowerDescriptor(
            did="device-1",
            name="Garage Mower",
            model="dreame.mower.g2408",
            display_model="A2",
            account_type="dreame",
            country="eu",
        ),
    )


def test_app_maps_downloads_chunks_and_summarizes_payload() -> None:
    client = _client()
    cloud = _FakeAppMapCloud(
        {
            "name": "Garden",
            "total_area": 12.5,
            "map": [{"area": 12.5, "data": [[1, 2], [3, 4], [5, 6]]}],
            "spot": [{"id": 1}],
            "point": [[7, 8]],
            "semantic": [
                {"data": [[9, 9], [10, 9], [10, 10]], "type": "unknown"},
                {"type": "unknown", "label": "future"},
            ],
            "trajectory": [{"data": [[1, 1]]}],
            "cut_relation": [],
        }
    )
    client._sync_get_cloud_protocol = lambda: cloud

    result = client._sync_get_app_maps(chunk_size=40, include_payload=True)

    assert result["available"] is True
    assert result["map_count"] == 2
    assert result["current_map_index"] == 0
    assert result["objects"]["object_count"] == 1
    assert result["objects"]["urls_included"] is False
    assert result["objects"]["objects"][0]["extension"] == "bin"
    assert "url" not in result["objects"]["objects"][0]
    assert result["maps"][0]["available"] is True
    assert result["maps"][0]["hash_match"] is True
    assert result["maps"][0]["summary"] == {
        "name": "Garden",
        "total_area": 12.5,
        "map_area_total": 12.5,
        "map_area_count": 1,
        "boundary_point_count": 3,
        "spot_count": 1,
        "spot_boundary_point_count": 0,
        "point_count": 1,
        "semantic_count": 2,
        "semantic_boundary_point_count": 3,
        "semantic_key_counts": {"data": 1, "label": 1, "type": 2},
        "trajectory_count": 1,
        "trajectory_point_count": 1,
        "cut_relation_count": 0,
    }
    assert result["maps"][0]["payload"]["name"] == "Garden"
    assert result["maps"][1]["created"] is False
    assert [call["t"] for call in cloud.calls][:4] == ["MAPL", "OBJ", "MAPI", "MAPD"]
    mapd_calls = [call for call in cloud.calls if call["t"] == "MAPD"]
    payload_size = len(cloud.payload_text.encode("utf-8"))
    expected_starts = list(range(0, payload_size, 40))
    expected_sizes = [min(40, payload_size - start) for start in expected_starts]
    assert [call["d"]["start"] for call in mapd_calls] == expected_starts
    assert [call["d"]["size"] for call in mapd_calls] == expected_sizes


def test_app_maps_reject_hash_mismatched_payload() -> None:
    client = _client()
    payload = {"map": [{"area": 1, "data": [[1, 2], [3, 4], [5, 6]]}]}
    payload_text = json.dumps(payload, separators=(",", ":"))
    corrupt_payload_text = '{"map":[]}'
    cloud = _FakeAppMapCloud(
        payload,
        chunk_overrides={0: (corrupt_payload_text, len(payload_text))},
    )
    client._sync_get_cloud_protocol = lambda: cloud

    result = client._sync_get_app_maps(
        chunk_size=40,
        include_payload=True,
        include_objects=False,
    )

    assert result["available"] is False
    assert result["maps"][0]["available"] is False
    assert result["errors"][0]["error"] == "App map payload hash mismatch."
    assert payload_text != corrupt_payload_text


def test_app_map_text_rejects_oversized_chunk() -> None:
    client = _client()
    client._sync_call_app_action = lambda payload: {
        "r": 0,
        "d": {"data": "abcdef"},
    }

    try:
        client._sync_get_app_map_text(size=5, chunk_size=5)
    except DreameLawnMowerConnectionError as err:
        assert str(err) == "MAPD returned too much data at offset 0."
    else:  # pragma: no cover - explicit failure branch for readability
        raise AssertionError("Expected mismatched MAPD chunk to fail")


def test_app_maps_can_omit_sensitive_payload_coordinates() -> None:
    client = _client()
    cloud = _FakeAppMapCloud({"map": [{"area": 1, "data": [[1, 2]]}]})
    client._sync_get_cloud_protocol = lambda: cloud

    result = client._sync_get_app_maps(chunk_size=400, include_payload=False)

    assert result["available"] is True
    assert "payload" not in result["maps"][0]
    assert result["maps"][0]["payload_keys"] == ["map"]


def test_app_map_object_urls_are_opt_in() -> None:
    client = _client()
    cloud = _FakeAppMapCloud({"map": [{"area": 1, "data": [[1, 2]]}]})
    client._sync_get_cloud_protocol = lambda: cloud

    objects = client._sync_get_app_map_objects(include_urls=True)

    assert objects["source"] == "app_action_obj_3dmap"
    assert objects["object_count"] == 1
    assert objects["urls_included"] is True
    assert objects["objects"][0]["url_present"] is True
    assert objects["objects"][0]["url"].startswith("https://example.invalid/")


def test_map_view_falls_back_to_rendered_app_map() -> None:
    client = _client()
    cloud = _FakeAppMapCloud(
        {
            "total_area": 1,
            "map": [
                {
                    "area": 1,
                    "data": [[0, 0], [100, 0], [100, 100], [0, 100]],
                }
            ],
            "spot": [{"data": [[20, 20], [40, 20], [40, 40], [20, 40]]}],
            "point": [[50, 50]],
            "trajectory": [{"data": [[0, 0], [50, 50], [100, 100]]}],
        }
    )
    client._sync_get_cloud_protocol = lambda: cloud
    def fail_if_legacy_map_is_called(timeout, interval):  # noqa: ARG001
        raise AssertionError("legacy map path should not run when app map works")

    client._sync_wait_for_map = fail_if_legacy_map_is_called
    client._safe_map_diagnostics = lambda **kwargs: None

    view = client._sync_refresh_map_view(timeout=0, interval=0)

    assert view.source == "app_action_map"
    assert view.error is None
    assert view.image_png is not None
    assert view.image_png.startswith(b"\x89PNG")
    assert view.summary is not None
    assert view.summary.available is True
    assert view.summary.map_id == 0
    assert view.summary.segment_count == 1
    assert view.summary.no_go_area_count == 0
    assert view.summary.spot_area_count == 1
    assert view.summary.path_point_count == 3
    assert view.app_maps == {
        "source": "app_action_map",
        "available": True,
        "map_count": 2,
        "current_map_index": 0,
        "available_map_count": 1,
        "created_map_count": 1,
        "maps": [
            {
                "idx": 0,
                "current": True,
                "created": True,
                "available": True,
                "has_backup": True,
                "force_load": False,
                "reported_size": len(cloud.payload_text.encode("utf-8")),
                "received_size": len(cloud.payload_text.encode("utf-8")),
                "chunk_count": 1,
                "hash_match": True,
                "payload_keys": ["map", "point", "spot", "total_area", "trajectory"],
                "total_area": 1,
                "map_area_count": 1,
                "map_area_total": 1.0,
                "boundary_point_count": 4,
                "spot_count": 1,
                "point_count": 1,
                "trajectory_count": 1,
                "trajectory_point_count": 3,
                "semantic_count": 0,
                "cut_relation_count": 0,
            },
            {
                "idx": 1,
                "current": False,
                "created": False,
                "available": False,
                "has_backup": False,
                "force_load": False,
            },
        ],
        "error_count": 0,
    }
    assert "OBJ" not in [call["t"] for call in cloud.calls]


def test_map_view_uses_legacy_path_when_app_map_fails() -> None:
    client = _client()
    client._sync_get_app_maps = lambda **kwargs: {
        "available": False,
        "maps": [],
        "errors": [{"error": "no app map"}],
    }
    client._sync_wait_for_map = lambda timeout, interval: None
    client._safe_map_diagnostics = lambda **kwargs: None

    view = client._sync_refresh_map_view(timeout=0, interval=0)

    assert view.source == "app_action_map"
    assert view.available is False
    assert view.error == "No app-map payload was returned."
