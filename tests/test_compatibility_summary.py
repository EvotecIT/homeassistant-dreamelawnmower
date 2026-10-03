"""Compatibility reports preserve unknown support without account identifiers."""

from __future__ import annotations

import json
from types import SimpleNamespace

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import compatibility


def test_support_summary_keeps_evidence_but_omits_identifiers_and_plan_names():
    descriptor = SimpleNamespace(
        model="mova.mower.g2529c",
        display_model="LiDAX Ultra 1000",
        account_type="mova",
        country="eu",
        did="private-device-id",
        name="Private garden",
        username="private@example.invalid",
    )
    result = compatibility.build_compatibility_summary(
        descriptor,
        SimpleNamespace(firmware_version="1.2.3", serial_number="private-sn"),
        features={"video": {"state": "unknown", "reason": "no_runtime_evidence"}},
        schedules={
            "schedules": [
                {
                    "idx": 0,
                    "protocol": "tables",
                    "read_status": "partial",
                    "plans": [{"name": "Private plan"}],
                    "task_errors": [{"error": "private"}],
                },
                {"idx": -1, "error": "private error"},
            ]
        },
    )
    assert result["brand"] == "MOVA"
    assert result["schedule_protocol"] == "tables"
    assert result["schedule_slots"][1]["protocol"] == "unknown"
    assert result["schedule_slots"][0]["reason"] == "tasks_incomplete"
    assert result["features"]["video"]["state"] == "unknown"
    assert "private" not in json.dumps(result).lower()
