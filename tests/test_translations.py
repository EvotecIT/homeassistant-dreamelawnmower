from __future__ import annotations

import json
from pathlib import Path

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.const import (
    STATE_CODE_TO_STATE,
)

INTEGRATION_ROOT = (
    Path(__file__).parents[1] / "custom_components" / "dreame_lawn_mower"
)
EXPECTED_LOCALES = {"de", "en", "fr", "it", "pl", "ru", "uk"}


def _state_translations(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["entity"]["sensor"]["state_name"]["state"]


def test_state_name_translations_cover_every_mower_state() -> None:
    expected_states = set(STATE_CODE_TO_STATE.values())
    translation_files = list((INTEGRATION_ROOT / "translations").glob("*.json"))

    assert EXPECTED_LOCALES <= {path.stem for path in translation_files}
    for path in translation_files:
        states = _state_translations(path)
        assert set(states) == expected_states, path.name
        assert all(
            isinstance(label, str) and label.strip() for label in states.values()
        )


def test_english_translation_matches_strings_source() -> None:
    source_states = _state_translations(INTEGRATION_ROOT / "strings.json")
    english_states = _state_translations(
        INTEGRATION_ROOT / "translations" / "en.json"
    )

    assert source_states == english_states
