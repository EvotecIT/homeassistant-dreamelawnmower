from __future__ import annotations

from types import SimpleNamespace

from custom_components.dreame_lawn_mower.select import (
    DreameLawnMowerVoiceLanguageSelect,
)


def test_voice_language_select_stays_available_for_unknown_index() -> None:
    entity = object.__new__(DreameLawnMowerVoiceLanguageSelect)
    entity.coordinator = SimpleNamespace(
        last_update_success=True,
        data=SimpleNamespace(),
        voice_settings={
            "voice_settings": {
                "available": True,
                "voice_language_index": 999,
            }
        },
    )

    assert entity.available is True
    assert entity.current_option is None
