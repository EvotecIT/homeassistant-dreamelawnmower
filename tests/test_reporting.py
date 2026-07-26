"""Contract tests for downloaded issue-report context."""

from datetime import timedelta
from types import SimpleNamespace

from custom_components.dreame_lawn_mower.performance import (
    DreameLawnMowerPerformanceTracker,
)
from custom_components.dreame_lawn_mower.reporting import (
    build_coordinator_diagnostics,
    build_entity_diagnostics,
    build_maintenance_point_diagnostics,
    build_report_context,
)


def test_report_context_keeps_reproduction_facts_and_drops_local_user() -> None:
    context = build_report_context(
        system_info={
            "installation_type": "Home Assistant OS",
            "version": "2026.7.1",
            "python_version": "3.13.5",
            "arch": "x86_64",
            "os_name": "Linux",
            "user": "private-user",
            "timezone": "Europe/Warsaw",
        },
        integration_version="0.3.0",
        config_entry=SimpleNamespace(
            state=SimpleNamespace(value="loaded"),
            disabled_by=None,
            version=2,
            minor_version=1,
        ),
    )

    assert context["integration_version"] == "0.3.0"
    assert context["home_assistant"] == {
        "installation_type": "Home Assistant OS",
        "version": "2026.7.1",
        "python_version": "3.13.5",
        "arch": "x86_64",
        "os_name": "Linux",
    }
    assert context["config_entry"] == {
        "state": "loaded",
        "disabled_by": None,
        "version": 2,
        "minor_version": 1,
    }


def test_entity_diagnostics_capture_runtime_failure_without_identifiers() -> None:
    entries = [
        SimpleNamespace(
            entity_id="camera.garden_live_video",
            original_name="Live Video",
            translation_key=None,
            entity_category=None,
            disabled_by=None,
        )
    ]
    state = SimpleNamespace(
        state="idle",
        attributes={
            "last_stream_error": "accessToken=secret failed",
            "last_stream_error_code": "video_cloud_start_failed",
            "managed_xp2p_runtime_supported": True,
            "entity_picture": "/api/camera_proxy/camera.garden?token=secret",
        },
    )

    entities = build_entity_diagnostics(entries, lambda _entity_id: state)

    assert entities == [
        {
            "domain": "camera",
            "original_name": "Live Video",
            "translation_key": None,
            "entity_category": None,
            "disabled_by": None,
            "loaded": True,
            "available": True,
            "state": "idle",
            "attributes": {
                "last_stream_error": "accessToken=**REDACTED** failed",
                "last_stream_error_code": "video_cloud_start_failed",
                "managed_xp2p_runtime_supported": True,
                "entity_picture": "**REDACTED**",
            },
        }
    ]
    assert "garden_live_video" not in repr(entities)


def test_coordinator_diagnostics_sanitize_last_failure() -> None:
    diagnostics = build_coordinator_diagnostics(
        SimpleNamespace(
            last_update_success=False,
            last_exception=RuntimeError("refresh failed token=secret"),
            update_interval=timedelta(seconds=30),
        )
    )

    assert diagnostics == {
        "last_update_success": False,
        "last_exception_type": "RuntimeError",
        "last_exception": "refresh failed token=**REDACTED**",
        "update_interval_seconds": 30.0,
        "performance": None,
        "maintenance_points": {
            "source": "coordinator_cache",
            "current_map_index": None,
            "selected_point_id": None,
            "control_ready": False,
            "app_points_without_vector_ids": False,
            "app_maps": [],
            "vector_maps": [],
        },
        "last_maintenance_point_probe": None,
    }


def test_coordinator_diagnostics_include_privacy_safe_performance_history() -> None:
    values = iter((0.0, 0.5))
    performance = DreameLawnMowerPerformanceTracker(
        clock=lambda: next(values),
    )
    performance.start("foreground_refresh").finish()

    diagnostics = build_coordinator_diagnostics(
        SimpleNamespace(
            last_update_success=True,
            last_exception=None,
            update_interval=timedelta(seconds=30),
            performance=performance,
        )
    )

    assert diagnostics["performance"]["summary"]["foreground_refresh"] == {
        "count": 1,
        "latest_ms": 500.0,
        "average_ms": 500.0,
        "maximum_ms": 500.0,
        "outcomes": {"completed": 1},
    }
    assert diagnostics["performance"]["samples"][0]["phases_ms"] == {}


def test_coordinator_diagnostics_keep_current_and_captured_probe_evidence() -> None:
    captured = {
        "source": "map_probe",
        "captured_at": "2026-07-26T14:30:00+00:00",
        "current_map_index": 1,
        "selected_point_id": None,
        "control_ready": False,
        "app_points_without_vector_ids": True,
        "app_maps": [],
        "vector_maps": [],
    }

    diagnostics = build_coordinator_diagnostics(
        SimpleNamespace(
            last_update_success=True,
            last_exception=None,
            update_interval=timedelta(seconds=30),
            last_map_probe_result=captured,
        )
    )

    assert diagnostics["maintenance_points"]["source"] == "coordinator_cache"
    assert diagnostics["last_maintenance_point_probe"] == captured
    assert diagnostics["last_maintenance_point_probe"] is not captured


def test_maintenance_point_diagnostics_explain_app_vector_mismatch_privately() -> None:
    diagnostics = build_maintenance_point_diagnostics(
        SimpleNamespace(
            selected_map_index=0,
            selected_maintenance_point_id=None,
            app_maps={
                "current_map_index": 0,
                "maps": [
                    {
                        "idx": 0,
                        "current": True,
                        "available": True,
                        "payload_keys": ["map", "point"],
                        "summary": {
                            "point_count": 1,
                            "point_entry_shapes": [
                                {
                                    "kind": "array",
                                    "count": 1,
                                    "length": 2,
                                    "item_types": ["number"],
                                }
                            ],
                        },
                        "payload": {"point": [[5910, 12400]]},
                    }
                ],
            },
            vector_map_details={
                "maps": [
                    {
                        "map_index": 0,
                        "clean_point_count": 0,
                        "clean_point_ids": [],
                    }
                ]
            },
        )
    )

    assert diagnostics == {
        "source": "coordinator_cache",
        "current_map_index": 0,
        "selected_point_id": None,
        "control_ready": False,
        "app_points_without_vector_ids": True,
        "app_maps": [
            {
                "map_index": 0,
                "current": True,
                "available": True,
                "point_payload_present": True,
                "point_count": 1,
                "point_entry_shapes": [
                    {
                        "kind": "array",
                        "count": 1,
                        "length": 2,
                        "item_types": ["number"],
                    }
                ],
            }
        ],
        "vector_maps": [
            {
                "map_index": 0,
                "point_count": 0,
                "point_ids": [],
            }
        ],
    }
    assert "5910" not in repr(diagnostics)
    assert "12400" not in repr(diagnostics)


def test_map_probe_uses_probed_current_map_over_stale_coordinator_selection() -> None:
    diagnostics = build_maintenance_point_diagnostics(
        SimpleNamespace(
            selected_map_index=0,
            selected_maintenance_point_id=None,
            app_maps=None,
            vector_map_details=None,
        ),
        map_probe_payload={
            "app_maps": {
                "current_map_index": 1,
                "maps": [
                    {
                        "idx": 0,
                        "current": False,
                        "available": True,
                        "payload_keys": ["point"],
                        "summary": {"point_count": 0},
                    },
                    {
                        "idx": 1,
                        "current": True,
                        "available": True,
                        "payload_keys": ["point"],
                        "summary": {
                            "point_count": 1,
                            "point_entry_shapes": [
                                {
                                    "kind": "array",
                                    "count": 1,
                                    "length": 2,
                                    "item_types": ["number"],
                                }
                            ],
                        },
                    },
                ],
            },
            "batch_vector_map": {
                "details": {
                    "maps": [
                        {
                            "map_index": 0,
                            "clean_point_count": 1,
                            "clean_point_ids": [301],
                        },
                        {
                            "map_index": 1,
                            "clean_point_count": 0,
                            "clean_point_ids": [],
                        },
                    ]
                }
            },
        },
        captured_at="2026-07-26T14:30:00+00:00",
    )

    assert diagnostics["source"] == "map_probe"
    assert diagnostics["captured_at"] == "2026-07-26T14:30:00+00:00"
    assert diagnostics["current_map_index"] == 1
    assert diagnostics["control_ready"] is False
    assert diagnostics["app_points_without_vector_ids"] is True


def test_entity_diagnostics_redact_runtime_map_coordinates() -> None:
    entries = [
        SimpleNamespace(
            entity_id="sensor.garden_runtime_position_x",
            original_name="Runtime Position X",
            translation_key=None,
            entity_category=SimpleNamespace(value="diagnostic"),
            disabled_by=SimpleNamespace(value="integration"),
        ),
        SimpleNamespace(
            entity_id="camera.garden_map",
            original_name="Map",
            translation_key=None,
            entity_category=None,
            disabled_by=None,
        ),
    ]
    states = {
        "sensor.garden_runtime_position_x": SimpleNamespace(
            state="5910",
            attributes={
                "source": "cloud",
                "pose_x": 5910,
                "pose_y": 12400,
                "heading_deg": 63.5,
                "track_point_count": 17,
            },
        ),
        "camera.garden_map": SimpleNamespace(
            state="idle",
            attributes={
                "runtime_pose_x": 5910,
                "runtime_pose_y": 12400,
                "position_x": 5910,
                "position_y": 12400,
                "runtime_heading_deg": 63.5,
            },
        ),
    }

    entities = build_entity_diagnostics(entries, states.get)

    map_entity = next(item for item in entities if item["domain"] == "camera")
    position_entity = next(item for item in entities if item["domain"] == "sensor")
    assert position_entity["state"] == "**REDACTED**"
    assert position_entity["attributes"] == {
        "source": "cloud",
        "pose_x": "**REDACTED**",
        "pose_y": "**REDACTED**",
        "heading_deg": 63.5,
        "track_point_count": 17,
    }
    assert map_entity["attributes"] == {
        "runtime_pose_x": "**REDACTED**",
        "runtime_pose_y": "**REDACTED**",
        "position_x": "**REDACTED**",
        "position_y": "**REDACTED**",
        "runtime_heading_deg": 63.5,
    }


def test_entity_diagnostics_redact_identifier_sensor_state() -> None:
    entry = SimpleNamespace(
        entity_id="sensor.garden_mower_serial",
        unique_id="device-1_serial_number",
        original_name="Serial Number",
        translation_key=None,
        entity_category=SimpleNamespace(value="diagnostic"),
        disabled_by=None,
    )
    state = SimpleNamespace(state="SERIAL-123", attributes={"icon": "mdi:barcode"})

    entities = build_entity_diagnostics([entry], lambda _entity_id: state)

    assert entities[0]["state"] == "**REDACTED**"
    assert "SERIAL-123" not in repr(entities)
