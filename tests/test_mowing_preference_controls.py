"""Contract tests for user-facing mowing preference entities."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.dreame_lawn_mower.number import (
    DreameLawnMowerSelectedZoneMowingHeightNumber,
)
from custom_components.dreame_lawn_mower.select import (
    DreameLawnMowerSelectedMapPreferenceModeSelect,
)


def _coordinator(*, mode_name: str = "custom") -> SimpleNamespace:
    client = SimpleNamespace(
        async_plan_app_mowing_preference_update=AsyncMock(
            return_value={
                "source": "app_action_mowing_preference_write",
                "changed_fields": ["mowing_height_cm"],
            }
        )
    )
    return SimpleNamespace(
        client=client,
        data=SimpleNamespace(available=True),
        app_maps={
            "current_map_index": 1,
            "maps": [
                {
                    "idx": 1,
                    "name": "Back garden",
                    "current": True,
                    "available": True,
                }
            ],
        },
        batch_device_data={
            "batch_mowing_preferences": {
                "maps": [
                    {
                        "idx": 1,
                        "mode": 1 if mode_name == "custom" else 0,
                        "mode_name": mode_name,
                        "area_count": 1,
                        "preferences": [
                            {
                                "area_id": 5,
                                "mowing_height_cm": 4.0,
                            }
                        ],
                    }
                ]
            }
        },
        vector_map_details=None,
        selected_map_index=1,
        selected_zone_id=5,
        last_preference_write_result=None,
        async_refresh_batch_device_data=AsyncMock(),
        async_request_refresh=AsyncMock(),
        async_update_listeners=lambda: None,
    )


def test_preference_mode_select_reads_and_writes_selected_map_mode() -> None:
    coordinator = _coordinator(mode_name="global")
    entity = object.__new__(DreameLawnMowerSelectedMapPreferenceModeSelect)
    entity.coordinator = coordinator

    assert entity.available is True
    assert entity.options == ["Global", "Custom"]
    assert entity.current_option == "Global"

    asyncio.run(entity.async_select_option("Custom"))

    coordinator.client.async_plan_app_mowing_preference_update.assert_awaited_once_with(
        map_index=1,
        area_id=None,
        changes={"preference_mode": "custom"},
        execute=True,
        confirm_write=True,
    )
    coordinator.async_refresh_batch_device_data.assert_awaited_once_with(
        force=True,
        source="mowing_preference_write",
    )
    coordinator.async_request_refresh.assert_awaited_once()


def test_mowing_height_number_reads_and_writes_selected_zone() -> None:
    coordinator = _coordinator()
    entity = object.__new__(DreameLawnMowerSelectedZoneMowingHeightNumber)
    entity.coordinator = coordinator

    assert entity.available is True
    assert entity.native_value == 4.0
    assert entity.native_min_value == 3.5
    assert entity.native_max_value == 6.0
    assert entity.extra_state_attributes["selected_zone_id"] == 5
    assert entity.extra_state_attributes["write_available"] is True

    asyncio.run(entity.async_set_native_value(4.5))

    coordinator.client.async_plan_app_mowing_preference_update.assert_awaited_once_with(
        map_index=1,
        area_id=5,
        changes={"mowing_height_cm": 4.5},
        execute=True,
        confirm_write=True,
    )
    coordinator.async_refresh_batch_device_data.assert_awaited_once_with(
        force=True,
        source="mowing_preference_write",
    )
    coordinator.async_request_refresh.assert_awaited_once()


def test_mowing_height_number_requires_custom_mode() -> None:
    coordinator = _coordinator(mode_name="global")
    entity = object.__new__(DreameLawnMowerSelectedZoneMowingHeightNumber)
    entity.coordinator = coordinator

    assert entity.available is False
    assert entity.native_value == 4.0
    assert "Custom" in entity.extra_state_attributes["write_unavailable_reason"]

    with pytest.raises(HomeAssistantError, match="Select Custom"):
        asyncio.run(entity.async_set_native_value(4.5))

    coordinator.client.async_plan_app_mowing_preference_update.assert_not_awaited()


@pytest.mark.parametrize("value", [3.6, 6.5])
def test_mowing_height_number_rejects_unsupported_control_values(value: float) -> None:
    coordinator = _coordinator()
    entity = object.__new__(DreameLawnMowerSelectedZoneMowingHeightNumber)
    entity.coordinator = coordinator

    with pytest.raises(HomeAssistantError):
        asyncio.run(entity.async_set_native_value(value))

    coordinator.client.async_plan_app_mowing_preference_update.assert_not_awaited()
