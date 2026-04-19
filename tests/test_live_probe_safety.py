"""Regression checks for live probe safety gates."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from examples.app_map_probe import summarize_app_map_payload
from examples.field_trip_probe import (
    _raise_if_unsafe_execute as raise_if_unsafe_field_trip,
)
from examples.remote_control_probe import _support_probe_payload
from examples.remote_control_smoke import (
    _raise_if_unsafe_execute as raise_if_unsafe_remote_control,
)
from examples.remote_control_smoke import (
    _settings_summary as remote_control_settings,
)
from examples.remote_control_smoke import (
    _snapshot_summary as remote_control_snapshot_summary,
)
from examples.remote_control_smoke import (
    _write_output as write_remote_control_output,
)
from examples.status_blob_probe import _summarize_samples
from examples.task_status_probe import summarize_task_samples, task_samples_changed


def test_remote_control_smoke_blocks_low_battery() -> None:
    with pytest.raises(RuntimeError, match="battery is low"):
        raise_if_unsafe_remote_control(
            SimpleNamespace(
                activity="docked",
                battery_level=19,
                mowing=False,
                returning=False,
                raw_attributes={},
            )
        )


def test_remote_control_smoke_allows_safe_docked_snapshot() -> None:
    raise_if_unsafe_remote_control(
        SimpleNamespace(
            activity="docked",
            battery_level=80,
            mowing=False,
            returning=False,
            raw_attributes={},
        )
    )


def test_remote_control_smoke_summary_includes_manual_drive_safety() -> None:
    summary = remote_control_snapshot_summary(
        SimpleNamespace(
            activity="docked",
            battery_level=80,
            charging=False,
            descriptor=SimpleNamespace(title="Garage Mower (A2)"),
            docked=True,
            error_code=-1,
            error_display=None,
            mowing=False,
            paused=False,
            raw_attributes={},
            realtime_property_count=0,
            returning=False,
            state="charging_completed",
        ),
        SimpleNamespace(as_dict=lambda: {"supported": True}),
    )

    assert summary["manual_drive_safe"] is True
    assert summary["manual_drive_block_reason"] is None


def test_remote_control_smoke_settings_are_recorded() -> None:
    settings = remote_control_settings(
        SimpleNamespace(
            device_index=1,
            dock=True,
            duration=0.35,
            rotation=25,
            velocity=30,
        )
    )

    assert settings == {
        "velocity": 30,
        "rotation": 25,
        "duration": 0.35,
        "dock": True,
        "device_index": 1,
    }


def test_remote_control_smoke_writes_output_file(tmp_path) -> None:
    out = tmp_path / "remote-control-smoke.json"

    write_remote_control_output(
        SimpleNamespace(out=out),
        {"execute": False, "steps": [{"label": "read_only", "ok": True}]},
    )

    assert out.read_text(encoding="utf-8") == (
        '{\n'
        '  "execute": false,\n'
        '  "steps": [\n'
        "    {\n"
        '      "label": "read_only",\n'
        '      "ok": true\n'
        "    }\n"
        "  ]\n"
        "}\n"
    )


def test_remote_control_probe_payload_includes_support_and_safety() -> None:
    payload = _support_probe_payload(
        SimpleNamespace(
            activity="docked",
            battery_level=80,
            descriptor=SimpleNamespace(title="Garage Mower (A2)"),
            raw_attributes={},
            state="charging_completed",
        ),
        SimpleNamespace(
            as_dict=lambda: {
                "supported": True,
                "state_safe": True,
                "state_block_reason": None,
            }
        ),
    )

    assert payload == {
        "device": "Garage Mower (A2)",
        "state": "charging_completed",
        "activity": "docked",
        "battery_level": 80,
        "manual_drive_safe": True,
        "manual_drive_block_reason": None,
        "remote_control_support": {
            "supported": True,
            "state_safe": True,
            "state_block_reason": None,
        },
    }


def test_field_trip_blocks_mapping_snapshot() -> None:
    with pytest.raises(RuntimeError, match="mapping"):
        raise_if_unsafe_field_trip(
            {
                "snapshot": {
                    "activity": "docked",
                    "battery_level": 80,
                    "mowing": False,
                    "returning": False,
                    "raw_state_signals": {"mapping": True},
                }
            }
        )


def test_field_trip_prefers_operation_snapshot_manual_drive_guard() -> None:
    with pytest.raises(RuntimeError, match="battery is low"):
        raise_if_unsafe_field_trip(
            {
                "snapshot": {
                    "activity": "docked",
                    "battery_level": 80,
                    "manual_drive_safe": False,
                    "manual_drive_block_reason": (
                        "Remote control is blocked while battery is low."
                    ),
                    "mowing": False,
                    "returning": False,
                    "raw_state_signals": {},
                }
            }
        )


def test_field_trip_allows_unknown_battery_level() -> None:
    raise_if_unsafe_field_trip(
        {
            "snapshot": {
                "activity": "docked",
                "battery_level": None,
                "mowing": False,
                "returning": False,
                "raw_state_signals": {},
            }
        }
    )


def test_status_blob_sample_summary_tracks_battery_and_changed_bytes() -> None:
    summary = _summarize_samples(
        [
            {
                "state": "mowing",
                "activity": "mowing",
                "battery_level": 84,
                "mowing": True,
                "returning": False,
                "docked": False,
                "status_blob": {
                    "candidate_battery_level": 84,
                    "hex": "ce0054bace",
                    "bytes_by_index": {"0": 206, "11": 84, "17": 186},
                },
            },
            {
                "state": "mowing",
                "activity": "mowing",
                "battery_level": 84,
                "mowing": True,
                "returning": False,
                "docked": False,
                "status_blob": {
                    "candidate_battery_level": 84,
                    "hex": "ce0054c0ce",
                    "bytes_by_index": {"0": 206, "11": 84, "17": 192},
                },
            },
        ]
    )

    assert summary["states"] == ["mowing"]
    assert summary["activities"] == ["mowing"]
    assert summary["mowing_flags"] == [True]
    assert summary["returning_flags"] == [False]
    assert summary["docked_flags"] == [False]
    assert summary["battery_levels"] == [84]
    assert summary["candidate_battery_levels"] == [84]
    assert summary["candidate_battery_matches_snapshot"] is True
    assert summary["unique_status_blob_hex_count"] == 2
    assert summary["changed_byte_indices"] == [{"index": 17, "values": [186, 192]}]


def test_task_status_sample_summary_tracks_state_and_task_changes() -> None:
    summary = summarize_task_samples(
        [
            {
                "entries": [
                    {
                        "key": "2.1",
                        "value": "1",
                        "decoded_label": "Mowing",
                        "state_key": "mowing",
                    },
                    {
                        "key": "2.50",
                        "task_status": {
                            "type": "TASK",
                            "executing": True,
                            "status": True,
                            "operation": 6,
                        },
                    },
                    {"key": "3.1", "value": "56"},
                    {"key": "5.106", "value": "6"},
                ],
                "unknown_non_empty_keys": ["5.106"],
            },
            {
                "entries": [
                    {
                        "key": "2.1",
                        "value": "5",
                        "decoded_label": "Returning Charge",
                        "state_key": "returning",
                    },
                    {
                        "key": "2.50",
                        "task_status": {
                            "type": "TASK",
                            "executing": False,
                            "status": False,
                            "operation": 6,
                        },
                    },
                    {"key": "3.1", "value": "55"},
                    {"key": "5.106", "value": "7"},
                ],
                "unknown_non_empty_keys": ["5.106"],
            },
        ]
    )

    assert summary["states"] == [
        {"value": "1", "label": "Mowing", "state_key": "mowing"},
        {"value": "5", "label": "Returning Charge", "state_key": "returning"},
    ]
    assert summary["state_keys"] == ["mowing", "returning"]
    assert summary["task_status_changed"] is True
    assert summary["state_changed"] is True
    assert summary["battery_levels"] == ["56", "55"]
    assert summary["unknown_non_empty_keys"] == ["5.106"]
    assert summary["unknown_values"] == {"5.106": ["6", "7"]}


def test_task_status_change_detection_uses_state_or_task_status() -> None:
    samples = [
        {
            "entries": [
                {
                    "key": "2.1",
                    "value": "1",
                    "decoded_label": "Mowing",
                    "state_key": "mowing",
                },
                {
                    "key": "2.50",
                    "task_status": {
                        "type": "TASK",
                        "executing": True,
                        "status": True,
                        "operation": 6,
                    },
                },
            ],
        },
        {
            "entries": [
                {
                    "key": "2.1",
                    "value": "1",
                    "decoded_label": "Mowing",
                    "state_key": "mowing",
                },
                {
                    "key": "2.50",
                    "task_status": {
                        "type": "TASK",
                        "executing": False,
                        "status": False,
                        "operation": 6,
                    },
                },
            ],
        },
    ]

    assert task_samples_changed(samples) is True
    assert task_samples_changed(samples[:1]) is False


def test_app_map_probe_summary_keeps_compact_current_map_evidence() -> None:
    summary = summarize_app_map_payload(
        {
            "available": True,
            "source": "app_action_map",
            "current_map_index": 0,
            "map_count": 2,
            "maps": [
                {
                    "idx": 0,
                    "current": True,
                    "summary": {
                        "map_area_count": 2,
                        "total_area": 531,
                        "trajectory_point_count": 63,
                    },
                },
                {
                    "idx": 1,
                    "current": False,
                    "summary": {"map_area_count": 2},
                },
            ],
            "objects": {"object_count": 2},
            "errors": [],
        }
    )

    assert summary == {
        "available": True,
        "source": "app_action_map",
        "map_count": 2,
        "current_map_index": 0,
        "current_map_summary": {
            "map_area_count": 2,
            "total_area": 531,
            "trajectory_point_count": 63,
        },
        "object_count": 2,
        "errors": [],
    }
