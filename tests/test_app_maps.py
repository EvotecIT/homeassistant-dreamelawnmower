"""Regression checks for mower-native app map retrieval."""

from __future__ import annotations

import hashlib
import json

from dreame_lawn_mower_client import DreameLawnMowerClient
from dreame_lawn_mower_client.models import DreameLawnMowerDescriptor


class _FakeAppMapCloud:
    logged_in = True

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload_text = json.dumps(payload, separators=(",", ":"))
        self.payload_hash = hashlib.md5(self.payload_text.encode("utf-8")).hexdigest()
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
            text = self.payload_text[start : start + size]
            return {
                "out": [
                    {
                        "m": "r",
                        "r": 0,
                        "d": {"size": len(text.encode("utf-8")), "data": text},
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
            "semantic": [],
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
        "semantic_count": 0,
        "trajectory_count": 1,
        "trajectory_point_count": 1,
        "cut_relation_count": 0,
    }
    assert result["maps"][0]["payload"]["name"] == "Garden"
    assert result["maps"][1]["created"] is False
    assert [call["t"] for call in cloud.calls][:4] == ["MAPL", "OBJ", "MAPI", "MAPD"]


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
    client._sync_wait_for_map = lambda timeout, interval: None
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
    assert view.summary.no_go_area_count == 1
    assert view.summary.path_point_count == 3
