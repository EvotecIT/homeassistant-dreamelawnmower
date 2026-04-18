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
        raise AssertionError(f"Unexpected app command: {payload}")


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
            "trajectory": [[1, 1]],
            "cut_relation": [],
        }
    )
    client._sync_get_cloud_protocol = lambda: cloud

    result = client._sync_get_app_maps(chunk_size=40, include_payload=True)

    assert result["available"] is True
    assert result["map_count"] == 2
    assert result["current_map_index"] == 0
    assert result["maps"][0]["available"] is True
    assert result["maps"][0]["hash_match"] is True
    assert result["maps"][0]["summary"] == {
        "name": "Garden",
        "total_area": 12.5,
        "map_area_total": 12.5,
        "map_area_count": 1,
        "boundary_point_count": 3,
        "spot_count": 1,
        "point_count": 1,
        "semantic_count": 0,
        "trajectory_count": 1,
        "cut_relation_count": 0,
    }
    assert result["maps"][0]["payload"]["name"] == "Garden"
    assert result["maps"][1]["created"] is False
    assert [call["t"] for call in cloud.calls][:3] == ["MAPL", "MAPI", "MAPD"]


def test_app_maps_can_omit_sensitive_payload_coordinates() -> None:
    client = _client()
    cloud = _FakeAppMapCloud({"map": [{"area": 1, "data": [[1, 2]]}]})
    client._sync_get_cloud_protocol = lambda: cloud

    result = client._sync_get_app_maps(chunk_size=400, include_payload=False)

    assert result["available"] is True
    assert "payload" not in result["maps"][0]
    assert result["maps"][0]["payload_keys"] == ["map"]
