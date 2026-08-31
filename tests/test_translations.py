from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from homeassistant.helpers.translation import async_get_translations

from custom_components.dreame_lawn_mower.const import (
    COUNTRY_OPTIONS,
    DOMAIN,
    MAP_MOWING_PATH_STYLE_OPTIONS,
    MAP_ROTATION_OPTIONS,
    MAP_SPOT_AREA_STYLE_OPTIONS,
    MAP_THEME_OPTIONS,
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
        "video_retention": VIDEO_RETENTION_OPTIONS,
        "video_transport": VIDEO_TRANSPORT_OPTIONS,
        "xp2p_runner_mode": XP2P_RUNNER_MODE_OPTIONS,
    }

    assert selectors.keys() == expected_options.keys()
    for selector_key, runtime_values in expected_options.items():
        assert selectors[selector_key]["options"].keys() == set(runtime_values)


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
