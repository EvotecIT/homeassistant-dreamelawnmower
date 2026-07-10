"""Regression checks for CMS maintenance counters."""

from __future__ import annotations

import pytest

from dreame_lawn_mower_client import DreameLawnMowerClient
from dreame_lawn_mower_client.maintenance import (
    build_cms_set_request,
    maintenance_item_status,
    maintenance_status_from_app_data,
    maintenance_status_from_cms,
    reset_cms_counter,
)
from dreame_lawn_mower_client.models import DreameLawnMowerDescriptor


class _FakeMaintenanceCloud:
    logged_in = True

    def __init__(self, *, cms_error: bool = False) -> None:
        self.calls: list[dict[str, object]] = []
        self.cms_error = cms_error
        self.values = [4896, 16752, 6849, -1]

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
        method = payload.get("m")
        if command == "CMS" and method == "g":
            if self.cms_error:
                raise RuntimeError("CMS unavailable")
            return {"out": [{"m": "r", "r": 0, "d": {"value": self.values}}]}
        if command == "CFG" and method == "g":
            return {"out": [{"m": "r", "r": 0, "d": {"CMS": self.values}}]}
        if command == "CMS" and method == "s":
            data = payload.get("d")
            assert isinstance(data, dict)
            value = data.get("value")
            assert isinstance(value, list)
            self.values = value
            return {"out": [{"m": "r", "r": 0, "d": {"ok": True}}]}
        raise AssertionError(f"Unexpected app command: {payload}")


def _client() -> DreameLawnMowerClient:
    return DreameLawnMowerClient(
        username="user@example.invalid",
        password="secret",
        country="eu",
        account_type="dreame",
        descriptor=DreameLawnMowerDescriptor(
            did="device-1",
            name="Garden Mower",
            model="dreame.mower.g2408",
            display_model="A2",
            account_type="dreame",
            country="eu",
        ),
    )


def test_maintenance_status_decodes_known_cms_counters() -> None:
    status = maintenance_status_from_cms(
        [4896, 16752, 6849, -1],
        source="test",
    )

    blade = maintenance_item_status(status, "blade")
    brush = maintenance_item_status(status, "brush")
    robot = maintenance_item_status(status, "robot")

    assert status["available"] is True
    assert status["raw_cms"] == [4896, 16752, 6849, -1]
    assert status["extra_values"] == [-1]
    assert status["due_items"] == ["robot"]
    assert status["warning_items"] == ["blade", "robot"]
    assert status["warning"] is True
    assert blade["remaining_percent"] == 18.4
    assert blade["used_hours"] == 81.6
    assert blade["status"] == "replace_soon"
    assert blade["warning"] is True
    assert brush["remaining_hours"] == 220.8
    assert brush["status"] == "normal"
    assert brush["warning"] is False
    assert robot["due"] is True
    assert robot["status"] == "due"
    assert robot["warning"] is True
    assert robot["remaining_percent"] == 0.0


def test_maintenance_status_marks_fresh_counters_good() -> None:
    status = maintenance_status_from_cms(
        [0, 0, 0, -1],
        source="test",
    )

    assert status["due"] is False
    assert status["due_items"] == []
    assert status["warning"] is False
    assert status["warning_items"] == []
    assert [item["status"] for item in status["items"]] == [
        "normal",
        "normal",
        "normal",
    ]


def test_maintenance_status_accepts_direct_cms_and_cfg_payloads() -> None:
    direct = maintenance_status_from_app_data(
        {"value": [1, 2, 3, -1]},
        source="direct",
    )
    cfg = maintenance_status_from_app_data(
        {"CMS": [1, 2, 3, -1]},
        source="config",
    )

    assert direct["available"] is True
    assert direct["raw_cms"] == [1, 2, 3, -1]
    assert cfg["available"] is True
    assert cfg["raw_cms"] == [1, 2, 3, -1]


def test_cms_reset_preserves_unknown_extra_values() -> None:
    updated = reset_cms_counter([4896, 16752, 6849, -1], "blade")

    assert updated == [0, 16752, 6849, -1]
    assert build_cms_set_request(updated) == {
        "m": "s",
        "t": "CMS",
        "d": {"value": [0, 16752, 6849, -1]},
    }


def test_get_maintenance_status_uses_read_only_cms_action() -> None:
    client = _client()
    cloud = _FakeMaintenanceCloud()
    client._sync_get_cloud_protocol = lambda: cloud

    result = client._sync_get_maintenance_status()

    assert result["source"] == "app_action_maintenance_cms"
    assert result["available"] is True
    assert result["raw_cms"] == [4896, 16752, 6849, -1]
    assert [call["t"] for call in cloud.calls] == ["CMS"]


def test_get_maintenance_status_falls_back_to_cfg_cms() -> None:
    client = _client()
    cloud = _FakeMaintenanceCloud(cms_error=True)
    client._sync_get_cloud_protocol = lambda: cloud

    result = client._sync_get_maintenance_status()

    assert result["source"] == "app_action_config_cms"
    assert result["available"] is True
    assert result["raw_cms"] == [4896, 16752, 6849, -1]
    assert result["errors"] == [{"stage": "cms", "error": "CMS unavailable"}]
    assert [call["t"] for call in cloud.calls] == ["CMS", "CFG"]


def test_plan_maintenance_reset_builds_dry_run_request() -> None:
    client = _client()
    cloud = _FakeMaintenanceCloud()
    client._sync_get_cloud_protocol = lambda: cloud

    result = client._sync_plan_maintenance_reset("blade")

    assert result["dry_run"] is True
    assert result["executed"] is False
    assert result["changed"] is True
    assert result["previous_cms"] == [4896, 16752, 6849, -1]
    assert result["updated_cms"] == [0, 16752, 6849, -1]
    assert result["request"] == {
        "m": "s",
        "t": "CMS",
        "d": {"value": [0, 16752, 6849, -1]},
    }
    assert [call["t"] for call in cloud.calls] == ["CMS"]


def test_execute_maintenance_reset_requires_confirmation() -> None:
    client = _client()
    client._sync_get_cloud_protocol = lambda: _FakeMaintenanceCloud()

    with pytest.raises(ValueError, match="confirm_write=True"):
        client._sync_plan_maintenance_reset("blade", execute=True)


def test_execute_maintenance_reset_sends_cms_set_and_refreshes() -> None:
    client = _client()
    cloud = _FakeMaintenanceCloud()
    client._sync_get_cloud_protocol = lambda: cloud

    result = client._sync_plan_maintenance_reset(
        "brush",
        execute=True,
        confirm_write=True,
    )

    assert result["dry_run"] is False
    assert result["executed"] is True
    assert result["updated_cms"] == [4896, 0, 6849, -1]
    assert result["refreshed_cms"] == [4896, 0, 6849, -1]
    assert result["refreshed_item"]["used_minutes"] == 0
    assert [call["t"] for call in cloud.calls] == ["CMS", "CMS", "CMS"]
