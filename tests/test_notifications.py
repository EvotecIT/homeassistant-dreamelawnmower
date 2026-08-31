"""Contract tests for optional mower notifications."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.dreame_lawn_mower.const import (
    CONF_NOTIFICATION_MODE,
    NOTIFICATION_MODE_FAULTS,
    NOTIFICATION_MODE_FAULTS_AND_WARNINGS,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerDescriptor,
    DreameLawnMowerSnapshot,
)
from custom_components.dreame_lawn_mower.notifications import (
    DreameLawnMowerNotificationManager,
)


def _snapshot(**overrides: object) -> DreameLawnMowerSnapshot:
    values: dict[str, object] = {
        "descriptor": DreameLawnMowerDescriptor(
            did="mower-1",
            name="Garden Mower",
            model="dreame.mower.g2408",
            display_model="A2",
            account_type="dreame",
            country="eu",
        ),
        "available": True,
        "state": "idle",
        "state_name": "idle",
        "activity": "idle",
    }
    values.update(overrides)
    return DreameLawnMowerSnapshot(**values)


class _Coordinator:
    def __init__(self, *, mode: str | None = None) -> None:
        options = {} if mode is None else {CONF_NOTIFICATION_MODE: mode}
        self.entry = SimpleNamespace(entry_id="entry-1", options=options)
        self.hass = SimpleNamespace(config=SimpleNamespace(language="en"))
        self.data = _snapshot()
        self.listener = None
        self.remove_listener = Mock()

    def async_add_listener(self, listener):
        self.listener = listener
        return self.remove_listener


@pytest.fixture
def notification_calls(monkeypatch):
    created = Mock()
    dismissed = Mock()
    translations = {
        "component.dreame_lawn_mower.common.notification.unknown_fault": (
            "Unknown fault"
        ),
        "component.dreame_lawn_mower.common.notification.fault_title": (
            "{name}: mower fault"
        ),
        "component.dreame_lawn_mower.common.notification.fault_message": (
            "The mower reports **{display}**."
        ),
        "component.dreame_lawn_mower.common.notification.error_code": (
            "Error code: `{code}`"
        ),
        "component.dreame_lawn_mower.common.notification.warning_title": (
            "{name}: mower warning"
        ),
        "component.dreame_lawn_mower.common.notification.warning_message": (
            "The mower reports **{display}**."
        ),
        "component.dreame_lawn_mower.common.notification.status_code": (
            "Status code: `{code}`"
        ),
    }
    translated = AsyncMock(return_value=translations)
    monkeypatch.setattr(
        "custom_components.dreame_lawn_mower.notifications."
        "persistent_notification.async_create",
        created,
    )
    monkeypatch.setattr(
        "custom_components.dreame_lawn_mower.notifications."
        "persistent_notification.async_dismiss",
        dismissed,
    )
    monkeypatch.setattr(
        "custom_components.dreame_lawn_mower.notifications."
        "async_get_translations",
        translated,
    )
    return created, dismissed, translated


def test_default_mode_is_silent_and_clears_stale_notifications(
    notification_calls,
) -> None:
    created, dismissed, translated = notification_calls
    coordinator = _Coordinator()
    coordinator.data = _snapshot(
        activity="error",
        error_code=23,
        error_display="Left wheel blocked",
    )

    asyncio.run(DreameLawnMowerNotificationManager(coordinator).async_start())

    created.assert_not_called()
    translated.assert_not_awaited()
    assert [call.args[1] for call in dismissed.call_args_list] == [
        "dreame_lawn_mower_entry-1_fault",
        "dreame_lawn_mower_entry-1_warning",
    ]


def test_fault_notification_updates_only_when_content_changes_and_clears(
    notification_calls,
) -> None:
    created, dismissed, _ = notification_calls
    coordinator = _Coordinator(mode=NOTIFICATION_MODE_FAULTS)
    manager = DreameLawnMowerNotificationManager(coordinator)
    asyncio.run(manager.async_start())
    dismissed.reset_mock()

    coordinator.data = _snapshot(
        activity="error",
        error_code=23,
        error_display="Left wheel blocked",
    )
    coordinator.listener()
    coordinator.listener()

    created.assert_called_once()
    assert created.call_args.kwargs == {
        "title": "Garden Mower (A2): mower fault",
        "notification_id": "dreame_lawn_mower_entry-1_fault",
    }
    assert "Left wheel blocked" in created.call_args.args[1]
    assert "`23`" in created.call_args.args[1]

    coordinator.data = _snapshot(
        activity="error",
        error_code=24,
        error_display="Right wheel blocked",
    )
    coordinator.listener()
    assert created.call_count == 2

    coordinator.data = _snapshot()
    coordinator.listener()
    dismissed.assert_called_once_with(
        coordinator.hass,
        "dreame_lawn_mower_entry-1_fault",
    )


@pytest.mark.parametrize("tier", ["alert", "attention", None])
def test_warning_mode_publishes_actionable_status_notices(
    notification_calls,
    tier: str | None,
) -> None:
    created, _, _ = notification_calls
    coordinator = _Coordinator(mode=NOTIFICATION_MODE_FAULTS_AND_WARNINGS)
    coordinator.data = _snapshot(
        status_notice_code=44,
        status_notice_display="Rain delay",
        status_notice_tier=tier,
    )

    asyncio.run(DreameLawnMowerNotificationManager(coordinator).async_start())

    created.assert_called_once()
    assert created.call_args.kwargs["notification_id"].endswith("_warning")
    assert "Rain delay" in created.call_args.args[1]


@pytest.mark.parametrize("tier", ["info", None])
def test_non_actionable_or_empty_status_notices_stay_silent(
    notification_calls,
    tier: str | None,
) -> None:
    created, _, _ = notification_calls
    coordinator = _Coordinator(mode=NOTIFICATION_MODE_FAULTS_AND_WARNINGS)
    coordinator.data = _snapshot(
        status_notice_display=None if tier is None else "Mowing resumed",
        status_notice_tier=tier,
    )

    asyncio.run(DreameLawnMowerNotificationManager(coordinator).async_start())

    created.assert_not_called()


def test_stop_unsubscribes_and_dismisses_owned_notifications(
    notification_calls,
) -> None:
    _, dismissed, _ = notification_calls
    coordinator = _Coordinator(mode=NOTIFICATION_MODE_FAULTS)
    manager = DreameLawnMowerNotificationManager(coordinator)
    asyncio.run(manager.async_start())
    dismissed.reset_mock()

    manager.stop()

    coordinator.remove_listener.assert_called_once_with()
    assert [call.args[1] for call in dismissed.call_args_list] == [
        "dreame_lawn_mower_entry-1_fault",
        "dreame_lawn_mower_entry-1_warning",
    ]


def test_runtime_notification_text_uses_home_assistant_language(
    notification_calls,
) -> None:
    created, _, translated = notification_calls
    translated.return_value.update(
        {
            "component.dreame_lawn_mower.common.notification.fault_title": (
                "{name}: błąd kosiarki"
            ),
            "component.dreame_lawn_mower.common.notification.fault_message": (
                "Kosiarka zgłasza **{display}**."
            ),
            "component.dreame_lawn_mower.common.notification.error_code": (
                "Kod błędu: `{code}`"
            ),
        }
    )
    coordinator = _Coordinator(mode=NOTIFICATION_MODE_FAULTS)
    coordinator.hass.config.language = "pl"
    coordinator.data = _snapshot(
        activity="error",
        error_code=23,
        error_display="Left wheel blocked",
    )

    asyncio.run(DreameLawnMowerNotificationManager(coordinator).async_start())

    translated.assert_awaited_once_with(
        coordinator.hass,
        "pl",
        "common",
        integrations={"dreame_lawn_mower"},
    )
    assert created.call_args.kwargs["title"] == "Garden Mower (A2): błąd kosiarki"
    assert created.call_args.args[1].startswith("Kosiarka zgłasza")
    assert "Kod błędu" in created.call_args.args[1]
