"""Contract tests for user-facing mowing preference entities."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.dreame_lawn_mower.coordinator import (
    _app_map_index_hints,
    _app_map_slot_index_hints,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    decode_batch_mowing_preferences,
)
from custom_components.dreame_lawn_mower.mowing_preference_control import (
    mowing_height_limits,
)
from custom_components.dreame_lawn_mower.number import (
    DreameLawnMowerSelectedMapMowingHeightNumber,
    DreameLawnMowerSelectedMowingDirectionNumber,
    DreameLawnMowerSelectedZoneMowingHeightNumber,
)
from custom_components.dreame_lawn_mower.preference_select import (
    PREFERENCE_SELECTS,
    DreameLawnMowerPreferenceSelect,
)
from custom_components.dreame_lawn_mower.preference_switch import (
    AI_CLASS_SWITCHES,
    PREFERENCE_SWITCHES,
    DreameLawnMowerPreferenceAiClassSwitch,
    DreameLawnMowerPreferenceSwitch,
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
    coordinator = SimpleNamespace(
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
                                "area_id": 0,
                                "mowing_height_cm": 5.0,
                                "efficient_mode": 1,
                                "mowing_direction_mode": 1,
                                "mowing_direction_mode_name": "rotation",
                                "mowing_direction_method_name": "mow_at_angle",
                                "mowing_direction_degrees": 5,
                                "edge_mowing_auto": True,
                                "edge_mowing_safe": True,
                                "edge_cutting_attachment": False,
                                "edge_mowing_obstacle_avoidance": True,
                                "obstacle_avoidance_enabled": True,
                                "obstacle_avoidance_height_cm": 5,
                                "obstacle_avoidance_distance_cm": 15,
                                "obstacle_avoidance_ai_classes": [
                                    "people",
                                    "animals",
                                    "objects",
                                ],
                                "edge_mowing_walk_mode": 1,
                            },
                            {
                                "area_id": 5,
                                "mowing_height_cm": 4.0,
                                "efficient_mode": 0,
                                "efficient_mode_name": "standard",
                                "mowing_direction_mode": 2,
                                "mowing_direction_mode_name": "checkerboard",
                                "mowing_direction_degrees": 35,
                                "edge_mowing_auto": False,
                                "edge_mowing_safe": True,
                                "edge_cutting_attachment": True,
                                "edge_mowing_obstacle_avoidance": True,
                                "obstacle_avoidance_enabled": True,
                                "obstacle_avoidance_height_cm": 10,
                                "obstacle_avoidance_distance_cm": 20,
                                "obstacle_avoidance_ai_classes": [
                                    "people",
                                    "animals",
                                ],
                                "edge_mowing_walk_mode": 0,
                                "edge_mowing_walk_mode_name": "line",
                            },
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

    async def plan_mowing_preference_update(**kwargs):
        result = await client.async_plan_app_mowing_preference_update(**kwargs)
        coordinator.last_preference_write_result = result
        if kwargs["execute"]:
            await coordinator.async_refresh_batch_device_data(
                force=True,
                source="mowing_preference_write",
            )
            await coordinator.async_request_refresh()
        return result

    coordinator.async_plan_mowing_preference_update = plan_mowing_preference_update
    return coordinator


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
    assert entity.native_min_value == 3.0
    assert entity.native_max_value == 7.0
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


@pytest.mark.parametrize("value", [2.5, 3.6, 7.5])
def test_mowing_height_number_rejects_unsupported_control_values(value: float) -> None:
    coordinator = _coordinator()
    entity = object.__new__(DreameLawnMowerSelectedZoneMowingHeightNumber)
    entity.coordinator = coordinator

    with pytest.raises(HomeAssistantError):
        asyncio.run(entity.async_set_native_value(value))

    coordinator.client.async_plan_app_mowing_preference_update.assert_not_awaited()


def test_mowing_height_write_rejects_reported_outlier_on_normal_model() -> None:
    coordinator = _coordinator()
    coordinator.batch_device_data["batch_mowing_preferences"]["maps"][0][
        "preferences"
    ][1]["mowing_height_cm"] = 10.0
    entity = object.__new__(DreameLawnMowerSelectedZoneMowingHeightNumber)
    entity.coordinator = coordinator

    assert entity.native_max_value == 10.0
    with pytest.raises(HomeAssistantError, match="between 3 and 7"):
        asyncio.run(entity.async_set_native_value(8.0))

    coordinator.client.async_plan_app_mowing_preference_update.assert_not_awaited()


def test_global_mowing_height_reads_and_writes_area_zero() -> None:
    coordinator = _coordinator(mode_name="global")
    entity = object.__new__(DreameLawnMowerSelectedMapMowingHeightNumber)
    entity.coordinator = coordinator

    assert entity.available is True
    assert entity.native_value == 5.0
    assert entity.native_min_value == 3.0
    assert entity.native_max_value == 7.0
    assert entity.extra_state_attributes["preference_scope"] == "global"

    asyncio.run(entity.async_set_native_value(6.5))

    coordinator.client.async_plan_app_mowing_preference_update.assert_awaited_once_with(
        map_index=1,
        area_id=0,
        changes={"mowing_height_cm": 6.5},
        execute=True,
        confirm_write=True,
    )


def test_global_controls_follow_active_map_after_uncreated_settings_slot() -> None:
    coordinator = _coordinator(mode_name="global")
    coordinator.app_maps = {
        "map_list_valid": True,
        "current_map_index": 1,
        "maps": [
            {"idx": 0, "created": False, "current": False},
            {
                "idx": 1,
                "created": True,
                "current": True,
                "name": "Garden",
            },
        ],
    }
    settings_text = json.dumps(
        [
            {"mode": 0, "settings": {}},
            {
                "mode": 0,
                "settings": {
                    "0": {
                        "id": 0,
                        "version": 78,
                        "mowingHeight": 5,
                        "efficientMode": 0,
                    }
                },
            },
        ],
        separators=(",", ":"),
    )
    coordinator.batch_device_data = {
        "batch_mowing_preferences": decode_batch_mowing_preferences(
            {
                "SETTINGS.0": settings_text,
                "SETTINGS.info": str(len(settings_text)),
            },
            map_index_hints=_app_map_index_hints(coordinator.app_maps),
            map_slot_index_hints=_app_map_slot_index_hints(coordinator.app_maps),
        )
    }

    entity = object.__new__(DreameLawnMowerSelectedMapMowingHeightNumber)
    entity.coordinator = coordinator

    assert entity.available is True
    assert entity.native_value == 5.0
    assert entity.extra_state_attributes["selected_map_index"] == 1
    assert entity.extra_state_attributes["preference_scope"] == "global"


def test_global_mowing_height_requires_global_mode() -> None:
    coordinator = _coordinator(mode_name="custom")
    entity = object.__new__(DreameLawnMowerSelectedMapMowingHeightNumber)
    entity.coordinator = coordinator

    assert entity.available is False
    with pytest.raises(HomeAssistantError, match="Select Global"):
        asyncio.run(entity.async_set_native_value(5.0))


def test_mowing_height_limits_follow_verified_mower_families() -> None:
    assert mowing_height_limits("dreame.mower.g2408") == (3.0, 7.0)
    assert mowing_height_limits("dreame.mower.q2501a") == (3.0, 10.0)
    assert mowing_height_limits("dreame.mower.g2541e") == (3.0, 10.0)


def test_active_preference_select_reads_and_writes_custom_zone() -> None:
    coordinator = _coordinator()
    description = next(
        item for item in PREFERENCE_SELECTS if item.key == "efficient_mode"
    )
    entity = object.__new__(DreameLawnMowerPreferenceSelect)
    entity.coordinator = coordinator
    entity._preference_description = description
    entity._attr_name = description.name

    assert entity.available is True
    assert entity.current_option == "Standard"
    assert entity.extra_state_attributes["preference_scope"] == "zone"

    asyncio.run(entity.async_select_option("Efficient"))

    coordinator.client.async_plan_app_mowing_preference_update.assert_awaited_once_with(
        map_index=1,
        area_id=5,
        changes={"efficient_mode": 1},
        execute=True,
        confirm_write=True,
    )


def test_direction_mode_select_reads_and_writes_custom_zone() -> None:
    coordinator = _coordinator()
    description = next(
        item for item in PREFERENCE_SELECTS if item.key == "mowing_direction_mode"
    )
    entity = object.__new__(DreameLawnMowerPreferenceSelect)
    entity.coordinator = coordinator
    entity._preference_description = description
    entity._attr_name = description.name

    assert entity.options == ["None", "Mow at angle", "Checkerboard"]
    assert entity.current_option == "Checkerboard"

    asyncio.run(entity.async_select_option("Mow at angle"))

    coordinator.client.async_plan_app_mowing_preference_update.assert_awaited_once_with(
        map_index=1,
        area_id=5,
        changes={"mowing_direction_mode": 1},
        execute=True,
        confirm_write=True,
    )


def test_direction_number_reads_and_writes_global_scope() -> None:
    coordinator = _coordinator(mode_name="global")
    entity = object.__new__(DreameLawnMowerSelectedMowingDirectionNumber)
    entity.coordinator = coordinator

    assert entity.available is True
    assert entity.native_value == 5.0
    assert entity.extra_state_attributes == {
        "preference_scope": "global",
        "selected_map_index": 1,
        "selected_map_label": "Back garden (#2)",
        "mowing_direction_mode": 1,
        "mowing_direction_mode_name": "rotation",
        "mowing_direction_method_name": "mow_at_angle",
    }

    asyncio.run(entity.async_set_native_value(15))

    coordinator.client.async_plan_app_mowing_preference_update.assert_awaited_once_with(
        map_index=1,
        area_id=0,
        changes={"mowing_direction_degrees": 15},
        execute=True,
        confirm_write=True,
    )


def test_turning_method_select_uses_app_labels() -> None:
    coordinator = _coordinator(mode_name="global")
    description = next(
        item for item in PREFERENCE_SELECTS if item.key == "edge_mowing_walk_mode"
    )
    entity = object.__new__(DreameLawnMowerPreferenceSelect)
    entity.coordinator = coordinator
    entity._preference_description = description
    entity._attr_name = description.name

    assert description.name == "Selected Turning Method"
    assert entity.options == ["Lawn care", "Efficient"]
    assert entity.current_option == "Efficient"


def test_active_preference_switch_writes_global_scope() -> None:
    coordinator = _coordinator(mode_name="global")
    description = next(
        item for item in PREFERENCE_SWITCHES if item.key == "edge_mowing_auto"
    )
    entity = object.__new__(DreameLawnMowerPreferenceSwitch)
    entity.coordinator = coordinator
    entity._preference_description = description

    assert entity.available is True
    assert entity.is_on is True
    assert entity.extra_state_attributes["preference_scope"] == "global"

    asyncio.run(entity.async_turn_off())

    coordinator.client.async_plan_app_mowing_preference_update.assert_awaited_once_with(
        map_index=1,
        area_id=0,
        changes={"edge_mowing_auto": False},
        execute=True,
        confirm_write=True,
    )


def test_edgemaster_switch_uses_optional_attachment_field() -> None:
    coordinator = _coordinator()
    description = next(
        item for item in PREFERENCE_SWITCHES if item.key == "edge_cutting_attachment"
    )
    entity = object.__new__(DreameLawnMowerPreferenceSwitch)
    entity.coordinator = coordinator
    entity._preference_description = description

    assert entity.available is True
    assert entity.is_on is True

    asyncio.run(entity.async_turn_off())

    coordinator.client.async_plan_app_mowing_preference_update.assert_awaited_once_with(
        map_index=1,
        area_id=5,
        changes={"edge_cutting_attachment": False},
        execute=True,
        confirm_write=True,
    )


def test_active_ai_class_switch_preserves_other_classes() -> None:
    coordinator = _coordinator()
    description = next(item for item in AI_CLASS_SWITCHES if item.key == "objects")
    entity = object.__new__(DreameLawnMowerPreferenceAiClassSwitch)
    entity.coordinator = coordinator
    entity._preference_description = description

    assert entity.is_on is False
    asyncio.run(entity.async_turn_on())

    coordinator.client.async_plan_app_mowing_preference_update.assert_awaited_once_with(
        map_index=1,
        area_id=5,
        changes={
            "obstacle_avoidance_ai_classes": [
                "people",
                "animals",
                "objects",
            ]
        },
        execute=True,
        confirm_write=True,
    )


def test_active_ai_class_switch_can_enable_from_empty_mask() -> None:
    coordinator = _coordinator(mode_name="global")
    preference = coordinator.batch_device_data["batch_mowing_preferences"]["maps"][0][
        "preferences"
    ][0]
    preference.pop("obstacle_avoidance_ai_classes")
    preference["obstacle_avoidance_ai"] = 0
    description = next(item for item in AI_CLASS_SWITCHES if item.key == "people")
    entity = object.__new__(DreameLawnMowerPreferenceAiClassSwitch)
    entity.coordinator = coordinator
    entity._preference_description = description

    assert entity.available is True
    assert entity.is_on is False
    asyncio.run(entity.async_turn_on())

    coordinator.client.async_plan_app_mowing_preference_update.assert_awaited_once_with(
        map_index=1,
        area_id=0,
        changes={"obstacle_avoidance_ai_classes": ["people"]},
        execute=True,
        confirm_write=True,
    )
