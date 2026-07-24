"""Shared constants for reusable client domain mixins and helpers."""

from __future__ import annotations

CLOUD_PRESENCE_REFRESH_INTERVAL = 60.0

REMOTE_CONTROL_MAX_ROTATION = 1000
REMOTE_CONTROL_MAX_VELOCITY = 1000

VOICE_LANGUAGE_CODES = (
    "en",
    "cn",
    "de",
    "fr",
    "it",
    "es",
    "pt",
    "no",
    "sv",
    "da",
    "fi",
    "nl",
    "tr",
    "pl",
    "ru",
    "lt",
)
VOICE_LANGUAGE_LABELS = (
    "English",
    "Chinese",
    "German",
    "French",
    "Italian",
    "Spanish",
    "Portuguese",
    "Norwegian",
    "Swedish",
    "Danish",
    "Finnish",
    "Dutch",
    "Turkish",
    "Polish",
    "Russian",
    "Lithuanian",
)
VOICE_LANGUAGE_INDEX_TO_LABEL = {
    index: label for index, label in enumerate(VOICE_LANGUAGE_LABELS)
}
VOICE_LANGUAGE_INDEX_TO_CODE = {
    index: code for index, code in enumerate(VOICE_LANGUAGE_CODES)
}
VOICE_LANGUAGE_LABEL_TO_INDEX = {
    label: index for index, label in enumerate(VOICE_LANGUAGE_LABELS)
}
VOICE_PROMPT_FIELDS = (
    "general_prompt_voice_enabled",
    "working_voice_enabled",
    "special_status_voice_enabled",
    "fault_voice_enabled",
)
