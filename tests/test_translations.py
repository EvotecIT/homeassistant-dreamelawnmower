from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from homeassistant.components.automation.config import (
    ValidationStatus,
    async_validate_config_item,
)
from homeassistant.core import State
from homeassistant.helpers.script import Script
from homeassistant.helpers.translation import async_get_translations
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.dreame_lawn_mower.const import (
    COUNTRY_OPTIONS,
    DOMAIN,
    MAP_MOWING_PATH_STYLE_OPTIONS,
    MAP_ROTATION_OPTIONS,
    MAP_SPOT_AREA_STYLE_OPTIONS,
    MAP_THEME_OPTIONS,
    NOTIFICATION_MODE_OPTIONS,
    VIDEO_RETENTION_OPTIONS,
    VIDEO_TRANSPORT_OPTIONS,
    XP2P_RUNNER_MODE_OPTIONS,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.const import (
    STATE_CODE_TO_STATE,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerDescriptor,
    DreameLawnMowerSnapshot,
)
from custom_components.dreame_lawn_mower.sensor import SENSORS
from tests.test_notification_blueprint import _substituted_automation

INTEGRATION_ROOT = Path(__file__).parents[1] / "custom_components" / "dreame_lawn_mower"
EXPECTED_LOCALES = {"de", "en", "es", "fr", "it", "pl", "ru", "uk"}
MAX_UNTRANSLATED_SHARE = 0.10
PLACEHOLDER_PATTERN = re.compile(r"\{[^{}]+\}")
STATE_NAME_SENSOR = next(item for item in SENSORS if item.key == "state_name")
TEST_DESCRIPTOR = DreameLawnMowerDescriptor(
    did="test-mower",
    name="Test mower",
    model="dreame.mower.test",
    display_model="Test mower",
    account_type="dreame",
    country="PL",
)


def _state_translations(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["entity"]["sensor"]["state_name"]["state"]


def _translation_leaves(
    value: Any,
    path: tuple[str, ...] = (),
) -> dict[tuple[str, ...], str]:
    if isinstance(value, dict):
        leaves: dict[tuple[str, ...], str] = {}
        for key, child in value.items():
            leaves.update(_translation_leaves(child, (*path, key)))
        return leaves
    assert isinstance(value, str), ".".join(path)
    return {path: value}


def _home_assistant_category(
    category: str,
    value: dict[str, Any],
) -> dict[str, str]:
    prefix = f"component.{DOMAIN}.{category}"
    return {
        ".".join((prefix, *path)): label
        for path, label in _translation_leaves(value).items()
    }


def _public_mower_state(raw_state: str) -> str:
    snapshot = DreameLawnMowerSnapshot(
        descriptor=TEST_DESCRIPTOR,
        available=True,
        state=raw_state,
        state_name=raw_state,
        activity=raw_state,
    )
    return STATE_NAME_SENSOR.value_fn(snapshot)


def test_state_name_translations_cover_every_mower_state() -> None:
    expected_states = {
        _public_mower_state(raw_state) for raw_state in STATE_CODE_TO_STATE.values()
    }
    translation_files = list((INTEGRATION_ROOT / "translations").glob("*.json"))

    assert set(STATE_NAME_SENSOR.options or ()) == expected_states
    assert len(STATE_NAME_SENSOR.options or ()) == len(expected_states)
    assert EXPECTED_LOCALES <= {path.stem for path in translation_files}
    for path in translation_files:
        states = _state_translations(path)
        assert set(states) == expected_states, path.name
        assert all(
            isinstance(label, str) and label.strip() for label in states.values()
        )


def test_english_translation_matches_strings_source() -> None:
    source = json.loads((INTEGRATION_ROOT / "strings.json").read_text(encoding="utf-8"))
    english = json.loads(
        (INTEGRATION_ROOT / "translations" / "en.json").read_text(encoding="utf-8")
    )

    assert source == english


def test_every_shipped_locale_covers_the_complete_translation_contract() -> None:
    source = json.loads((INTEGRATION_ROOT / "strings.json").read_text(encoding="utf-8"))
    source_leaves = _translation_leaves(source)
    translation_files = list((INTEGRATION_ROOT / "translations").glob("*.json"))

    assert {path.stem for path in translation_files} == EXPECTED_LOCALES
    for path in translation_files:
        translated = json.loads(path.read_text(encoding="utf-8"))
        translated_leaves = _translation_leaves(translated)

        assert translated_leaves.keys() == source_leaves.keys(), path.name
        assert all(value.strip() for value in translated_leaves.values()), path.name
        for key, source_value in source_leaves.items():
            assert PLACEHOLDER_PATTERN.findall(
                translated_leaves[key]
            ) == PLACEHOLDER_PATTERN.findall(source_value), (
                path.name,
                ".".join(key),
            )
        if path.stem != "en":
            untranslated = {
                key
                for key, source_value in source_leaves.items()
                if translated_leaves[key] == source_value
            }
            assert len(untranslated) / len(source_leaves) <= MAX_UNTRANSLATED_SHARE, (
                path.name,
                sorted(".".join(key) for key in untranslated),
            )


def test_localized_selector_options_match_runtime_values() -> None:
    source = json.loads((INTEGRATION_ROOT / "strings.json").read_text(encoding="utf-8"))
    selectors = source["selector"]
    expected_options = {
        "country": COUNTRY_OPTIONS,
        "map_rotation": [str(value) for value in MAP_ROTATION_OPTIONS],
        "map_theme": MAP_THEME_OPTIONS,
        "map_spot_area_style": MAP_SPOT_AREA_STYLE_OPTIONS,
        "map_mowing_path_style": MAP_MOWING_PATH_STYLE_OPTIONS,
        "notification_mode": NOTIFICATION_MODE_OPTIONS,
        "video_retention": VIDEO_RETENTION_OPTIONS,
        "video_transport": VIDEO_TRANSPORT_OPTIONS,
        "xp2p_runner_mode": XP2P_RUNNER_MODE_OPTIONS,
    }

    assert selectors.keys() == expected_options.keys()
    for selector_key, runtime_values in expected_options.items():
        assert selectors[selector_key]["options"].keys() == set(runtime_values)


@pytest.mark.asyncio
async def test_notification_blueprint_generates_valid_automation(hass) -> None:
    validated = await async_validate_config_item(
        hass,
        "mower-condition-notifications",
        _substituted_automation(),
    )

    assert validated is not None
    assert validated.validation_status is ValidationStatus.OK


async def _notification_blueprint_script(
    hass,
    **overrides: Any,
) -> tuple[Script, dict[str, Any]]:
    config = _substituted_automation(overrides)
    validated = await async_validate_config_item(
        hass,
        "mower-condition-notifications-runtime",
        config,
    )
    assert validated is not None
    assert validated.validation_status is ValidationStatus.OK
    return (
        Script(
            hass,
            validated["actions"],
            "Mower notification blueprint test",
            DOMAIN,
            script_mode="parallel",
            max_runs=20,
        ),
        config["variables"],
    )


def _set_blueprint_states(hass) -> None:
    hass.states.async_set(
        "lawn_mower.garden",
        "idle",
        {
            "friendly_name": "Garden mower",
            "error_display": None,
            "status_notice_tier": None,
        },
    )
    hass.states.async_set("binary_sensor.garden_error_active", "off")
    hass.states.async_set("sensor.garden_error", "none")
    hass.states.async_set("sensor.garden_status_notice", "none")
    hass.states.async_set("binary_sensor.garden_online", "on")
    hass.states.async_set("binary_sensor.garden_maintenance_warning", "off")
    hass.states.async_set("binary_sensor.garden_maintenance_due", "off")


def _state_trigger(
    trigger_id: str,
    entity_id: str,
    from_state: str,
    to_state: str,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=trigger_id,
        entity_id=entity_id,
        from_state=State(entity_id, from_state),
        to_state=State(entity_id, to_state),
    )


async def _wait_for_calls(calls: list, count: int) -> None:
    for _ in range(200):
        if len(calls) >= count:
            return
        await asyncio.sleep(0.001)
    raise AssertionError(f"Expected {count} service calls, received {len(calls)}")


@pytest.mark.asyncio
async def test_blueprint_suppresses_notification_for_short_fault(hass) -> None:
    _set_blueprint_states(hass)
    notify_calls = async_mock_service(hass, "test", "notify")
    async_mock_service(hass, "persistent_notification", "dismiss")
    script, variables = await _notification_blueprint_script(
        hass,
        delivery_mode="one_shot",
        onset_delay_seconds=0.05,
        notify_actions=[{"action": "test.notify"}],
    )
    trigger = _state_trigger(
        "fault",
        "binary_sensor.garden_error_active",
        "off",
        "on",
    )
    hass.states.async_set("sensor.garden_error", "Left wheel blocked")
    hass.states.async_set("binary_sensor.garden_error_active", "on")

    task = asyncio.create_task(script.async_run({**variables, "trigger": trigger}))
    await asyncio.sleep(0.01)
    hass.states.async_set("binary_sensor.garden_error_active", "off")
    hass.states.async_set("sensor.garden_error", "none")
    await asyncio.wait_for(task, timeout=1)

    assert notify_calls == []


@pytest.mark.asyncio
async def test_blueprint_repeat_run_ends_before_a_later_recurrence(hass) -> None:
    _set_blueprint_states(hass)
    notify_calls = async_mock_service(hass, "test", "notify")
    async_mock_service(hass, "persistent_notification", "dismiss")
    script, variables = await _notification_blueprint_script(
        hass,
        delivery_mode="repeat",
        onset_delay_seconds=0,
        repeat_interval_minutes=30,
        notify_actions=[{"action": "test.notify"}],
    )
    trigger = _state_trigger(
        "fault",
        "binary_sensor.garden_error_active",
        "off",
        "on",
    )
    hass.states.async_set("sensor.garden_error", "Left wheel blocked")
    hass.states.async_set("binary_sensor.garden_error_active", "on")

    first = asyncio.create_task(script.async_run({**variables, "trigger": trigger}))
    await _wait_for_calls(notify_calls, 1)
    hass.states.async_set("binary_sensor.garden_error_active", "off")
    hass.states.async_set("sensor.garden_error", "none")
    await asyncio.wait_for(first, timeout=1)

    hass.states.async_set("sensor.garden_error", "Left wheel blocked")
    hass.states.async_set("binary_sensor.garden_error_active", "on")
    second = asyncio.create_task(script.async_run({**variables, "trigger": trigger}))
    await _wait_for_calls(notify_calls, 2)
    await asyncio.sleep(0.01)
    assert len(notify_calls) == 2

    hass.states.async_set("binary_sensor.garden_error_active", "off")
    hass.states.async_set("sensor.garden_error", "none")
    await asyncio.wait_for(second, timeout=1)


@pytest.mark.asyncio
async def test_blueprint_updates_persistent_notification_when_fault_changes(
    hass,
) -> None:
    _set_blueprint_states(hass)
    create_calls = async_mock_service(hass, "persistent_notification", "create")
    dismiss_calls = async_mock_service(hass, "persistent_notification", "dismiss")
    script, variables = await _notification_blueprint_script(
        hass,
        delivery_mode="persistent",
        onset_delay_seconds=0,
    )
    fault_trigger = _state_trigger(
        "fault",
        "binary_sensor.garden_error_active",
        "off",
        "on",
    )
    hass.states.async_set("sensor.garden_error", "Left wheel blocked")
    hass.states.async_set("binary_sensor.garden_error_active", "on")

    first = asyncio.create_task(
        script.async_run({**variables, "trigger": fault_trigger})
    )
    await _wait_for_calls(create_calls, 1)

    hass.states.async_set("sensor.garden_error", "Right wheel blocked")
    await asyncio.wait_for(first, timeout=1)
    assert dismiss_calls == []

    detail_trigger = _state_trigger(
        "fault_detail",
        "sensor.garden_error",
        "Left wheel blocked",
        "Right wheel blocked",
    )
    second = asyncio.create_task(
        script.async_run({**variables, "trigger": detail_trigger})
    )
    await _wait_for_calls(create_calls, 2)
    assert "Right wheel blocked" in create_calls[-1].data["message"]

    hass.states.async_set("binary_sensor.garden_error_active", "off")
    hass.states.async_set("sensor.garden_error", "none")
    await asyncio.wait_for(second, timeout=1)
    assert len(dismiss_calls) == 1


@pytest.mark.asyncio
async def test_blueprint_maintenance_warning_notifies_once(hass) -> None:
    _set_blueprint_states(hass)
    notify_calls = async_mock_service(hass, "test", "notify")
    async_mock_service(hass, "persistent_notification", "dismiss")
    script, variables = await _notification_blueprint_script(
        hass,
        delivery_mode="one_shot",
        onset_delay_seconds=0,
        notify_actions=[{"action": "test.notify"}],
    )
    trigger = _state_trigger(
        "maintenance_warning",
        "binary_sensor.garden_maintenance_warning",
        "off",
        "on",
    )
    hass.states.async_set("binary_sensor.garden_maintenance_warning", "on")

    task = asyncio.create_task(script.async_run({**variables, "trigger": trigger}))
    await _wait_for_calls(notify_calls, 1)
    await asyncio.wait_for(task, timeout=1)

    assert len(notify_calls) == 1


@pytest.mark.asyncio
async def test_blueprint_status_restoration_starts_a_fresh_incident(hass) -> None:
    _set_blueprint_states(hass)
    notify_calls = async_mock_service(hass, "test", "notify")
    async_mock_service(hass, "persistent_notification", "dismiss")
    script, variables = await _notification_blueprint_script(
        hass,
        delivery_mode="repeat",
        onset_delay_seconds=0,
        repeat_interval_minutes=30,
        notify_actions=[{"action": "test.notify"}],
    )
    hass.states.async_set(
        "lawn_mower.garden",
        "idle",
        {
            "friendly_name": "Garden mower",
            "error_display": None,
            "status_notice_tier": "attention",
        },
    )
    hass.states.async_set("sensor.garden_status_notice", "Rain detected")
    first_trigger = _state_trigger(
        "notice",
        "sensor.garden_status_notice",
        "none",
        "Rain detected",
    )

    first = asyncio.create_task(
        script.async_run({**variables, "trigger": first_trigger})
    )
    await _wait_for_calls(notify_calls, 1)
    hass.states.async_set("sensor.garden_status_notice", "unavailable")
    await asyncio.wait_for(first, timeout=1)

    hass.states.async_set("sensor.garden_status_notice", "Rain detected")
    restored_trigger = _state_trigger(
        "notice",
        "sensor.garden_status_notice",
        "unavailable",
        "Rain detected",
    )
    restored = asyncio.create_task(
        script.async_run({**variables, "trigger": restored_trigger})
    )
    await _wait_for_calls(notify_calls, 2)

    hass.states.async_set("sensor.garden_status_notice", "none")
    await asyncio.wait_for(restored, timeout=1)
    assert len(notify_calls) == 2


@pytest.mark.asyncio
async def test_blueprint_fault_change_during_onset_has_one_incident_owner(hass) -> None:
    _set_blueprint_states(hass)
    notify_calls = async_mock_service(hass, "test", "notify")
    async_mock_service(hass, "persistent_notification", "dismiss")
    script, variables = await _notification_blueprint_script(
        hass,
        delivery_mode="one_shot",
        onset_delay_seconds=0.05,
        notify_actions=[{"action": "test.notify"}],
    )
    fault_trigger = _state_trigger(
        "fault",
        "binary_sensor.garden_error_active",
        "off",
        "on",
    )
    hass.states.async_set("sensor.garden_error", "Left wheel blocked")
    hass.states.async_set("binary_sensor.garden_error_active", "on")

    original = asyncio.create_task(
        script.async_run({**variables, "trigger": fault_trigger})
    )
    await asyncio.sleep(0.01)
    hass.states.async_set("sensor.garden_error", "Right wheel blocked")
    detail_trigger = _state_trigger(
        "fault_detail",
        "sensor.garden_error",
        "Left wheel blocked",
        "Right wheel blocked",
    )
    detail = asyncio.create_task(
        script.async_run({**variables, "trigger": detail_trigger})
    )

    await _wait_for_calls(notify_calls, 1)
    await asyncio.wait_for(original, timeout=1)
    await asyncio.wait_for(detail, timeout=1)
    assert len(notify_calls) == 1


@pytest.mark.parametrize(
    ("trigger_id", "active_states", "cleared_states", "notice_tier"),
    [
        (
            "reconcile_fault",
            {
                "binary_sensor.garden_error_active": "on",
                "sensor.garden_error": "Left wheel blocked",
            },
            {
                "binary_sensor.garden_error_active": "off",
                "sensor.garden_error": "none",
            },
            None,
        ),
        (
            "reconcile_notice",
            {"sensor.garden_status_notice": "Rain detected"},
            {"sensor.garden_status_notice": "none"},
            "attention",
        ),
        (
            "reconcile_offline",
            {"binary_sensor.garden_online": "off"},
            {"binary_sensor.garden_online": "on"},
            None,
        ),
        (
            "reconcile_maintenance_warning",
            {"binary_sensor.garden_maintenance_warning": "on"},
            {"binary_sensor.garden_maintenance_warning": "off"},
            None,
        ),
        (
            "reconcile_maintenance_due",
            {"binary_sensor.garden_maintenance_due": "on"},
            {"binary_sensor.garden_maintenance_due": "off"},
            None,
        ),
    ],
)
@pytest.mark.asyncio
async def test_blueprint_reconciles_active_conditions_after_reload(
    hass,
    trigger_id: str,
    active_states: dict[str, str],
    cleared_states: dict[str, str],
    notice_tier: str | None,
) -> None:
    _set_blueprint_states(hass)
    if notice_tier is not None:
        hass.states.async_set(
            "lawn_mower.garden",
            "idle",
            {
                "friendly_name": "Garden mower",
                "error_display": None,
                "status_notice_tier": notice_tier,
            },
        )
    for entity_id, state in active_states.items():
        hass.states.async_set(entity_id, state)

    create_calls = async_mock_service(hass, "persistent_notification", "create")
    dismiss_calls = async_mock_service(hass, "persistent_notification", "dismiss")
    script, variables = await _notification_blueprint_script(
        hass,
        delivery_mode="persistent",
        onset_delay_seconds=0,
    )
    trigger = SimpleNamespace(
        id=trigger_id,
        entity_id=None,
        from_state=None,
        to_state=None,
    )

    task = asyncio.create_task(script.async_run({**variables, "trigger": trigger}))
    await _wait_for_calls(create_calls, 1)
    for entity_id, state in cleared_states.items():
        hass.states.async_set(entity_id, state)
    await asyncio.wait_for(task, timeout=1)

    assert len(create_calls) == 1
    assert len(dismiss_calls) == 1


@pytest.mark.asyncio
async def test_blueprint_reconciliation_dismisses_stale_persistent_item(hass) -> None:
    _set_blueprint_states(hass)
    create_calls = async_mock_service(hass, "persistent_notification", "create")
    dismiss_calls = async_mock_service(hass, "persistent_notification", "dismiss")
    script, variables = await _notification_blueprint_script(
        hass,
        delivery_mode="persistent",
        onset_delay_seconds=0,
    )
    trigger = SimpleNamespace(
        id="reconcile_fault",
        entity_id=None,
        from_state=None,
        to_state=None,
    )

    await asyncio.wait_for(
        script.async_run({**variables, "trigger": trigger}),
        timeout=1,
    )

    assert create_calls == []
    assert len(dismiss_calls) == 1
    assert dismiss_calls[0].data["notification_id"].endswith("_fault")


@pytest.mark.asyncio
async def test_blueprint_restoration_reconciles_after_unavailable_startup_state(
    hass,
) -> None:
    _set_blueprint_states(hass)
    hass.states.async_set("binary_sensor.garden_error_active", "unavailable")
    hass.states.async_set("sensor.garden_error", "unavailable")
    create_calls = async_mock_service(hass, "persistent_notification", "create")
    dismiss_calls = async_mock_service(hass, "persistent_notification", "dismiss")
    script, variables = await _notification_blueprint_script(
        hass,
        delivery_mode="persistent",
        onset_delay_seconds=0,
    )
    trigger = SimpleNamespace(
        id="reconcile_fault",
        entity_id=None,
        from_state=None,
        to_state=None,
    )

    await asyncio.wait_for(
        script.async_run({**variables, "trigger": trigger}),
        timeout=1,
    )
    assert create_calls == []
    assert dismiss_calls == []

    hass.states.async_set("sensor.garden_error", "Left wheel blocked")
    hass.states.async_set("binary_sensor.garden_error_active", "on")
    restored = _state_trigger(
        "restore_fault",
        "binary_sensor.garden_error_active",
        "unavailable",
        "on",
    )
    task = asyncio.create_task(
        script.async_run({**variables, "trigger": restored})
    )
    await _wait_for_calls(create_calls, 1)
    hass.states.async_set("binary_sensor.garden_error_active", "off")
    hass.states.async_set("sensor.garden_error", "none")
    await asyncio.wait_for(task, timeout=1)

    assert len(dismiss_calls) == 1


@pytest.mark.asyncio
async def test_blueprint_restoration_to_clear_dismisses_stale_item(hass) -> None:
    _set_blueprint_states(hass)
    create_calls = async_mock_service(hass, "persistent_notification", "create")
    dismiss_calls = async_mock_service(hass, "persistent_notification", "dismiss")
    script, variables = await _notification_blueprint_script(
        hass,
        delivery_mode="persistent",
        onset_delay_seconds=0,
    )
    onset = _state_trigger(
        "fault",
        "binary_sensor.garden_error_active",
        "off",
        "on",
    )
    hass.states.async_set("sensor.garden_error", "Left wheel blocked")
    hass.states.async_set("binary_sensor.garden_error_active", "on")
    owner = asyncio.create_task(script.async_run({**variables, "trigger": onset}))
    await _wait_for_calls(create_calls, 1)

    hass.states.async_set("sensor.garden_error", "unavailable")
    hass.states.async_set("binary_sensor.garden_error_active", "unavailable")
    await asyncio.wait_for(owner, timeout=1)
    assert dismiss_calls == []

    hass.states.async_set("sensor.garden_error", "none")
    hass.states.async_set("binary_sensor.garden_error_active", "off")
    restored = _state_trigger(
        "restore_fault",
        "binary_sensor.garden_error_active",
        "unavailable",
        "off",
    )
    await asyncio.wait_for(
        script.async_run({**variables, "trigger": restored}),
        timeout=1,
    )

    assert len(dismiss_calls) == 1


@pytest.mark.asyncio
async def test_blueprint_fault_detail_unavailable_does_not_clear_active_fault(
    hass,
) -> None:
    _set_blueprint_states(hass)
    create_calls = async_mock_service(hass, "persistent_notification", "create")
    dismiss_calls = async_mock_service(hass, "persistent_notification", "dismiss")
    script, variables = await _notification_blueprint_script(
        hass,
        delivery_mode="persistent",
        onset_delay_seconds=0,
    )
    onset = _state_trigger(
        "fault",
        "binary_sensor.garden_error_active",
        "off",
        "on",
    )
    hass.states.async_set("sensor.garden_error", "Left wheel blocked")
    hass.states.async_set("binary_sensor.garden_error_active", "on")
    owner = asyncio.create_task(script.async_run({**variables, "trigger": onset}))
    await _wait_for_calls(create_calls, 1)

    hass.states.async_set("sensor.garden_error", "unavailable")
    unavailable = _state_trigger(
        "fault_detail",
        "sensor.garden_error",
        "Left wheel blocked",
        "unavailable",
    )
    await asyncio.wait_for(
        script.async_run({**variables, "trigger": unavailable}),
        timeout=1,
    )
    assert dismiss_calls == []
    assert not owner.done()

    hass.states.async_set("sensor.garden_error", "Left wheel blocked")
    restored = _state_trigger(
        "fault_detail",
        "sensor.garden_error",
        "unavailable",
        "Left wheel blocked",
    )
    restored_owner = asyncio.create_task(
        script.async_run({**variables, "trigger": restored})
    )
    await _wait_for_calls(create_calls, 2)
    assert dismiss_calls == []
    assert not owner.done()
    assert not restored_owner.done()

    hass.states.async_set("binary_sensor.garden_error_active", "off")
    hass.states.async_set("sensor.garden_error", "none")
    await asyncio.wait_for(owner, timeout=1)
    await asyncio.wait_for(restored_owner, timeout=1)
    assert len(dismiss_calls) == 2


@pytest.mark.asyncio
async def test_blueprint_custom_delivery_removes_prior_persistent_item(hass) -> None:
    _set_blueprint_states(hass)
    notify_calls = async_mock_service(hass, "test", "notify")
    dismiss_calls = async_mock_service(hass, "persistent_notification", "dismiss")
    script, variables = await _notification_blueprint_script(
        hass,
        delivery_mode="one_shot",
        onset_delay_seconds=0,
        notify_actions=[{"action": "test.notify"}],
    )
    hass.states.async_set("sensor.garden_error", "Left wheel blocked")
    hass.states.async_set("binary_sensor.garden_error_active", "on")
    trigger = SimpleNamespace(
        id="reconcile_fault",
        entity_id=None,
        from_state=None,
        to_state=None,
    )

    await asyncio.wait_for(
        script.async_run({**variables, "trigger": trigger}),
        timeout=1,
    )

    assert len(dismiss_calls) == 1
    assert dismiss_calls[0].data["notification_id"].endswith("_fault")
    assert len(notify_calls) == 1


@pytest.mark.asyncio
async def test_blueprint_recurrence_restarts_confirmation_delay(hass) -> None:
    _set_blueprint_states(hass)
    notify_calls = async_mock_service(hass, "test", "notify")
    async_mock_service(hass, "persistent_notification", "dismiss")
    script, variables = await _notification_blueprint_script(
        hass,
        delivery_mode="one_shot",
        onset_delay_seconds=0.1,
        notify_actions=[{"action": "test.notify"}],
    )
    onset = _state_trigger(
        "offline",
        "binary_sensor.garden_online",
        "on",
        "off",
    )
    hass.states.async_set("binary_sensor.garden_online", "off")

    first = asyncio.create_task(script.async_run({**variables, "trigger": onset}))
    await asyncio.sleep(0.02)
    hass.states.async_set("binary_sensor.garden_online", "on")
    await asyncio.wait_for(first, timeout=1)

    await asyncio.sleep(0.02)
    hass.states.async_set("binary_sensor.garden_online", "off")
    second = asyncio.create_task(script.async_run({**variables, "trigger": onset}))
    await asyncio.sleep(0.07)
    assert notify_calls == []

    await _wait_for_calls(notify_calls, 1)
    await asyncio.wait_for(second, timeout=1)
    assert len(notify_calls) == 1


@pytest.mark.parametrize("language", sorted(EXPECTED_LOCALES))
@pytest.mark.asyncio
async def test_home_assistant_loads_every_localized_ui_category(
    hass,
    enable_custom_integrations,
    language: str,
) -> None:
    del enable_custom_integrations
    translated = json.loads(
        (INTEGRATION_ROOT / "translations" / f"{language}.json").read_text(
            encoding="utf-8"
        )
    )

    for category, category_values in translated.items():
        loaded = await async_get_translations(
            hass,
            language,
            category,
            integrations={DOMAIN},
        )

        assert loaded == _home_assistant_category(category, category_values)
