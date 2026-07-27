"""Regression checks for mower-native app map retrieval."""

from __future__ import annotations

import hashlib
import json

from dreame_lawn_mower_client import (
    DreameLawnMowerClient,
    DreameLawnMowerConnectionError,
    render_app_map_payload_png,
)
from dreame_lawn_mower_client.models import (
    DreameLawnMowerDescriptor,
    DreameLawnMowerMapSummary,
    DreameLawnMowerMapView,
)


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
        redact_response: bool = False,
    ) -> dict[str, object]:
        assert siid == 2
        assert aiid == 50
        assert redact_response is (payload.get("t") == "OBJ")
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
                            "name": ["ali_dreame/2025/04/23/device/map-one.0233.bin"]
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


def test_app_map_renderer_labels_areas_and_spots_with_scale() -> None:
    payload = {
        "map": [
            {
                "id": 7,
                "name": "Front",
                "area": 12.5,
                "data": [[0, 0], [100, 0], [100, 100], [0, 100]],
            }
        ],
        "spot": [
            {
                "id": 2,
                "data": [[30, 30], [70, 30], [70, 70], [30, 70]],
            }
        ],
        "trajectory": [[[0, 0], [100, 100]]],
    }

    normal_png, normal_width, normal_height = render_app_map_payload_png(
        payload,
        label_scale=1.0,
    )
    large_png, large_width, large_height = render_app_map_payload_png(
        payload,
        label_scale=2.5,
    )

    assert normal_png
    assert large_png
    assert (normal_width, normal_height) == (large_width, large_height)
    assert normal_png != large_png


def test_app_maps_downloads_chunks_and_summarizes_payload() -> None:
    client = _client()
    cloud = _FakeAppMapCloud(
        {
            "name": "Garden",
            "total_area": 12.5,
            "map": [{"area": 12.5, "data": [[1, 2], [3, 4], [5, 6]]}],
            "spot": [{"id": 1}],
            "point": [
                {
                    "id": 301,
                    "param": 0,
                    "point": [7, 8],
                    "time": 0,
                    "type": 1,
                },
                {
                    "id": 302,
                    "param": 0,
                    "point": [9, 10],
                    "time": 0,
                    "type": 2,
                },
                {"id": 303, "type": "obstacle"},
                {
                    "id": 304,
                    "param": 0,
                    "point": [13, 14],
                    "time": 0,
                    "type": "BackGarden",
                },
                [11, 12],
            ],
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
    assert result["created_map_count"] == 1
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
        "point_count": 5,
        "point_entry_shapes": [
            {
                "kind": "array",
                "count": 1,
                "length": 2,
                "item_types": ["number"],
            },
            {
                "kind": "object",
                "count": 3,
                "keys": ["id", "param", "point", "time", "type"],
            },
            {
                "kind": "object",
                "count": 1,
                "keys": ["id", "type"],
            },
        ],
        "maintenance_point_ids": [301, 302],
        "point_type_codes": [1, 2],
        "point_record_validation": {
            "total_count": 5,
            "exact_shape_count": 3,
            "parser_accepted_count": 2,
            "identified_count": 2,
            "rejection_reason_counts": [
                {"reason": "not_object", "count": 1},
                {"reason": "type_not_integer", "count": 1},
                {"reason": "unexpected_keys", "count": 1},
            ],
            "value_type_shapes": [
                {
                    "count": 2,
                    "id_type": "number",
                    "param_type": "number",
                    "point_type": "array",
                    "time_type": "number",
                    "type_type": "number",
                    "point_length": 2,
                    "point_item_types": ["number"],
                },
                {
                    "count": 1,
                    "id_type": "number",
                    "param_type": "number",
                    "point_type": "array",
                    "time_type": "number",
                    "type_type": "string",
                    "point_length": 2,
                    "point_item_types": ["number"],
                },
            ],
        },
        "semantic_count": 2,
        "semantic_boundary_point_count": 3,
        "semantic_key_counts": {"data": 1, "label": 1, "type": 2},
        "trajectory_count": 1,
        "trajectory_point_count": 1,
        "trajectory_length_m": 0.0,
        "cut_relation_count": 0,
    }
    assert result["maps"][0]["payload"]["name"] == "Garden"
    assert result["maps"][1]["created"] is False
    call_types = [call["t"] for call in cloud.calls]
    assert call_types[:2] == ["MAPL", "MAPI"]
    assert set(call_types[2:-1]) == {"MAPD"}
    assert call_types[-1] == "OBJ"
    mapd_calls = [call for call in cloud.calls if call["t"] == "MAPD"]
    payload_size = len(cloud.payload_text.encode("utf-8"))
    expected_starts = list(range(0, payload_size, 40))
    expected_sizes = [min(40, payload_size - start) for start in expected_starts]
    assert [call["d"]["start"] for call in mapd_calls] == expected_starts
    assert [call["d"]["size"] for call in mapd_calls] == expected_sizes


def test_app_maps_explain_rejected_point_values_without_exposing_them() -> None:
    client = _client()
    cloud = _FakeAppMapCloud(
        {
            "point": [
                {
                    "id": 401,
                    "param": {},
                    "point": {"x": 5910, "y": 12400},
                    "time": "created",
                    "type": "maintenance",
                },
                {
                    "id": "402",
                    "param": 0,
                    "point": ["7100", "8300"],
                    "time": 0,
                    "type": 1,
                },
            ]
        }
    )
    client._sync_get_cloud_protocol = lambda: cloud

    result = client._sync_get_app_maps(include_payload=False)

    validation = result["maps"][0]["summary"]["point_record_validation"]
    assert validation == {
        "total_count": 2,
        "exact_shape_count": 2,
        "parser_accepted_count": 0,
        "identified_count": 0,
        "rejection_reason_counts": [
            {"reason": "id_not_positive_integer", "count": 1},
            {"reason": "point_coordinates_not_finite_numbers", "count": 1},
            {"reason": "point_not_coordinate_pair", "count": 1},
            {"reason": "type_not_integer", "count": 1},
        ],
        "value_type_shapes": [
            {
                "count": 1,
                "id_type": "number",
                "param_type": "object",
                "point_type": "object",
                "time_type": "string",
                "type_type": "string",
            },
            {
                "count": 1,
                "id_type": "string",
                "param_type": "number",
                "point_type": "array",
                "time_type": "number",
                "type_type": "number",
                "point_length": 2,
                "point_item_types": ["string"],
            },
        ],
    }
    assert "401" not in repr(validation)
    assert "402" not in repr(validation)
    assert "5910" not in repr(validation)
    assert "12400" not in repr(validation)
    assert "maintenance" not in repr(validation)


def test_app_maps_accept_three_value_maintenance_point_vectors() -> None:
    client = _client()
    cloud = _FakeAppMapCloud(
        {
            "point": [
                {
                    "id": 501,
                    "param": {},
                    "point": [5910, 12400, 270],
                    "time": 1,
                    "type": 9,
                },
                {
                    "id": 502,
                    "param": {},
                    "point": [7100, 8300, 90],
                    "time": 2,
                    "type": 9,
                },
                {
                    "id": 503,
                    "param": {},
                    "point": [1, 2, 3, 4],
                    "time": 3,
                    "type": 9,
                },
            ]
        }
    )
    client._sync_get_cloud_protocol = lambda: cloud

    result = client._sync_get_app_maps(include_payload=False)

    summary = result["maps"][0]["summary"]
    assert summary["maintenance_point_ids"] == [501, 502]
    assert summary["point_type_codes"] == [9]
    assert summary["point_record_validation"] == {
        "total_count": 3,
        "exact_shape_count": 3,
        "parser_accepted_count": 2,
        "identified_count": 2,
        "rejection_reason_counts": [
            {"reason": "point_not_coordinate_pair", "count": 1},
        ],
        "value_type_shapes": [
            {
                "count": 2,
                "id_type": "number",
                "param_type": "object",
                "point_type": "array",
                "time_type": "number",
                "type_type": "number",
                "point_length": 3,
                "point_item_types": ["number"],
            },
            {
                "count": 1,
                "id_type": "number",
                "param_type": "object",
                "point_type": "array",
                "time_type": "number",
                "type_type": "number",
                "point_length": 4,
                "point_item_types": ["number"],
            },
        ],
    }
    validation = repr(summary["point_record_validation"])
    assert "5910" not in validation
    assert "12400" not in validation
    assert "7100" not in validation
    assert "8300" not in validation


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
    assert objects["objects"][0]["name"].endswith("map-one.0233.bin")
    assert objects["objects"][0]["url_present"] is True
    assert objects["objects"][0]["url"].startswith("https://example.invalid/")


def test_app_map_object_state_omits_names_and_redacts_action_logs() -> None:
    client = _client()
    call_options: list[dict[str, object]] = []

    def call_app_action(
        payload: dict[str, object],
        **kwargs: object,
    ) -> dict[str, object]:
        call_options.append(kwargs)
        return {
            "r": 0,
            "d": {"name": ["private/generated-map.pcd"]},
        }

    client._sync_call_app_action = call_app_action
    client._sync_get_cloud_protocol = lambda: object()

    objects = client._sync_get_app_map_objects(include_urls=False)

    assert call_options == [{"redact_response": True}]
    assert objects == {
        "source": "app_action_obj_3dmap",
        "object_count": 1,
        "objects": [{"extension": "pcd", "url_present": False}],
        "urls_included": False,
    }


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
    client._sync_get_vector_map_batch_data = lambda: None

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
        "object_count": 1,
        "object_error": None,
        "objects": [
            {
                "extension": "bin",
                "url_present": False,
            }
        ],
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
                "point_entry_shapes": [
                    {
                        "kind": "array",
                        "count": 1,
                        "length": 2,
                        "item_types": ["number"],
                    }
                ],
                "point_record_validation": {
                    "total_count": 1,
                    "exact_shape_count": 0,
                    "parser_accepted_count": 0,
                    "identified_count": 0,
                    "rejection_reason_counts": [{"reason": "not_object", "count": 1}],
                    "value_type_shapes": [],
                },
                "trajectory_count": 1,
                "trajectory_point_count": 3,
                "trajectory_length_m": 1.41,
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
    assert "OBJ" in [call["t"] for call in cloud.calls]


def test_map_view_prefers_vector_render_when_live_path_is_available() -> None:
    client = _client()
    vector_refresh_kwargs: dict[str, object] = {}
    app_view = DreameLawnMowerMapView(
        source="app_action_map",
        summary=DreameLawnMowerMapSummary(available=True, map_id=0),
        image_png=b"app",
        app_maps={
            "source": "app_action_map",
            "map_count": 2,
            "current_map_index": 1,
        },
        details={"has_live_path": False},
    )
    vector_view = DreameLawnMowerMapView(
        source="batch_vector_map",
        summary=DreameLawnMowerMapSummary(available=True, map_id=0),
        image_png=b"vector",
        details={"has_live_path": True, "mow_path_point_count": 42},
    )

    client._sync_refresh_app_map_view = lambda **kwargs: app_view
    client._sync_refresh_vector_map_view = lambda **kwargs: (
        vector_refresh_kwargs.update(kwargs) or vector_view
    )
    client._sync_refresh_legacy_map_view = lambda timeout, interval: (
        _ for _ in ()
    ).throw(  # noqa: ARG005
        AssertionError("legacy map path should not run when live vector data exists")
    )

    view = client._sync_refresh_map_view(timeout=0, interval=0)

    assert view.source == "batch_vector_map"
    assert view.image_png == b"vector"
    assert view.details == {"has_live_path": True, "mow_path_point_count": 42}
    assert view.app_maps == {
        "source": "app_action_map",
        "map_count": 2,
        "current_map_index": 1,
    }
    assert vector_refresh_kwargs["current_map_index"] == 1


def test_map_view_keeps_app_render_when_vector_has_no_live_path() -> None:
    client = _client()
    app_view = DreameLawnMowerMapView(
        source="app_action_map",
        summary=DreameLawnMowerMapSummary(available=True, map_id=0),
        image_png=b"app",
        app_maps={"source": "app_action_map", "map_count": 2},
        details={"has_live_path": False, "trajectory_point_count": 3},
    )
    vector_view = DreameLawnMowerMapView(
        source="batch_vector_map",
        summary=DreameLawnMowerMapSummary(available=True, map_id=0),
        image_png=b"vector",
        details={"has_live_path": False, "mow_path_point_count": 0},
    )

    client._sync_refresh_app_map_view = lambda **kwargs: app_view
    client._sync_refresh_vector_map_view = lambda **kwargs: vector_view
    client._sync_refresh_legacy_map_view = lambda timeout, interval: (
        _ for _ in ()
    ).throw(  # noqa: ARG005
        AssertionError("legacy map path should not run when app map works")
    )

    view = client._sync_refresh_map_view(timeout=0, interval=0)

    assert view is app_view
    assert view.source == "app_action_map"
    assert view.image_png == b"app"


def test_map_view_preserves_runtime_pose_when_app_render_wins() -> None:
    client = _client()
    app_view = DreameLawnMowerMapView(
        source="app_action_map",
        summary=DreameLawnMowerMapSummary(available=True, map_id=0),
        image_png=b"app",
        details={"has_live_path": False, "trajectory_point_count": 3},
    )
    vector_view = DreameLawnMowerMapView(
        source="batch_vector_map",
        summary=DreameLawnMowerMapSummary(available=True, map_id=0),
        image_png=b"vector",
        details={
            "has_live_path": False,
            "runtime_pose_x": 50,
            "runtime_pose_y": 40,
            "runtime_heading_deg": 90.0,
            "runtime_region_id": 7,
            "runtime_position_updated_at": "2026-07-16T17:18:12+00:00",
        },
    )

    client._sync_refresh_app_map_view = lambda **kwargs: app_view
    client._sync_refresh_vector_map_view = lambda **kwargs: vector_view
    client._sync_refresh_legacy_map_view = lambda timeout, interval: (
        _ for _ in ()
    ).throw(  # noqa: ARG005
        AssertionError("legacy map path should not run when app map works")
    )

    view = client._sync_refresh_map_view(timeout=0, interval=0)

    assert view.source == "app_action_map"
    assert view.image_png == b"app"
    assert view.details == {
        "has_live_path": False,
        "trajectory_point_count": 3,
        "runtime_pose_x": 50,
        "runtime_pose_y": 40,
        "runtime_heading_deg": 90.0,
        "runtime_region_id": 7,
        "runtime_position_updated_at": "2026-07-16T17:18:12+00:00",
    }


def test_map_view_uses_legacy_path_when_app_map_fails() -> None:
    client = _client()
    client._sync_get_app_maps = lambda **kwargs: {
        "available": False,
        "maps": [],
        "errors": [{"error": "no app map"}],
    }
    client._sync_get_vector_map_batch_data = lambda: None
    client._sync_wait_for_map = lambda timeout, interval: None
    client._safe_map_diagnostics = lambda **kwargs: None

    view = client._sync_refresh_map_view(timeout=0, interval=0)

    assert view.source == "app_action_map"
    assert view.available is False
    assert view.error == "No app-map payload was returned."


def test_map_view_threads_presentation_options_to_legacy_fallback() -> None:
    client = _client()
    captured: dict[str, object] = {}
    style = object()
    client._sync_refresh_app_map_view = lambda **kwargs: DreameLawnMowerMapView(
        source="app_action_map",
        error="no app map",
    )
    client._sync_refresh_vector_map_view = lambda **kwargs: DreameLawnMowerMapView(
        source="batch_vector_map",
        error="no vector map",
    )

    def legacy_view(
        timeout: float,
        interval: float,
        **kwargs: object,
    ) -> DreameLawnMowerMapView:
        captured.update(
            timeout=timeout,
            interval=interval,
            **kwargs,
        )
        return DreameLawnMowerMapView(
            source="legacy_current_map",
            summary=DreameLawnMowerMapSummary(available=True, map_id=1),
            image_png=b"legacy",
        )

    client._sync_refresh_legacy_map_view = legacy_view

    view = client._sync_refresh_map_view(
        timeout=3,
        interval=0.25,
        label_scale=2.5,
        style=style,
    )

    assert view.image_png == b"legacy"
    assert captured == {
        "timeout": 3,
        "interval": 0.25,
        "label_scale": 2.5,
        "style": style,
    }
