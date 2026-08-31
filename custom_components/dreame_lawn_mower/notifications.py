"""Optional Home Assistant notifications for mower conditions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components import persistent_notification
from homeassistant.helpers.translation import async_get_translations

from .const import (
    ACTIVITY_ERROR,
    CONF_NOTIFICATION_MODE,
    DEFAULT_NOTIFICATION_MODE,
    DOMAIN,
    NOTIFICATION_MODE_FAULTS_AND_WARNINGS,
    NOTIFICATION_MODE_OPTIONS,
)
from .coordinator import DreameLawnMowerCoordinator

_WARNING_TIERS = frozenset({"alert", "attention", "unknown"})
_DEFAULT_TEXT = {
    "unknown_fault": "Unknown fault",
    "fault_title": "{name}: mower fault",
    "fault_message": "The mower reports **{display}**.",
    "error_code": "Error code: `{code}`",
    "warning_title": "{name}: mower warning",
    "warning_message": "The mower reports **{display}**.",
    "status_code": "Status code: `{code}`",
}


@dataclass(frozen=True, slots=True)
class _NotificationContent:
    """Content whose equality controls notification updates."""

    title: str
    message: str


class DreameLawnMowerNotificationManager:
    """Mirror selected mower conditions to persistent notifications."""

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        self._coordinator = coordinator
        self._remove_listener: Callable[[], None] | None = None
        self._content: dict[str, _NotificationContent] = {}
        self._text = dict(_DEFAULT_TEXT)
        entry_id = coordinator.entry.entry_id
        self._notification_ids = {
            "fault": f"{DOMAIN}_{entry_id}_fault",
            "warning": f"{DOMAIN}_{entry_id}_warning",
        }

    async def async_start(self) -> None:
        """Load localized text and start observing coordinator updates."""
        if self._remove_listener is not None:
            return
        if self._mode() != DEFAULT_NOTIFICATION_MODE:
            translations = await async_get_translations(
                self._coordinator.hass,
                self._coordinator.hass.config.language,
                "common",
                integrations={DOMAIN},
            )
            prefix = f"component.{DOMAIN}.common.notification."
            self._text.update(
                {
                    key: translations.get(f"{prefix}{key}", fallback)
                    for key, fallback in _DEFAULT_TEXT.items()
                }
            )
        self._remove_listener = self._coordinator.async_add_listener(self._sync)
        for kind in self._notification_ids:
            self._dismiss(kind, force=True)
        self._sync()

    def stop(self) -> None:
        """Stop observing updates and remove integration-owned notifications."""
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None
        for kind in self._notification_ids:
            self._dismiss(kind, force=True)

    def _mode(self) -> str:
        mode = self._coordinator.entry.options.get(
            CONF_NOTIFICATION_MODE,
            DEFAULT_NOTIFICATION_MODE,
        )
        if mode not in NOTIFICATION_MODE_OPTIONS:
            return DEFAULT_NOTIFICATION_MODE
        return mode

    def _sync(self) -> None:
        mode = self._mode()
        snapshot = self._coordinator.data

        if mode == DEFAULT_NOTIFICATION_MODE or snapshot is None:
            self._dismiss("fault")
            self._dismiss("warning")
            return

        if snapshot.activity == ACTIVITY_ERROR:
            display = (
                snapshot.error_display
                or snapshot.error_name
                or self._text["unknown_fault"]
            )
            code = (
                "\n\n" + self._text["error_code"].format(code=snapshot.error_code)
                if snapshot.error_code is not None
                else ""
            )
            self._publish(
                "fault",
                title=self._text["fault_title"].format(
                    name=snapshot.descriptor.title
                ),
                message=self._text["fault_message"].format(display=display) + code,
            )
        else:
            self._dismiss("fault")

        if (
            mode == NOTIFICATION_MODE_FAULTS_AND_WARNINGS
            and snapshot.status_notice_display
            and (snapshot.status_notice_tier or "unknown").casefold()
            in _WARNING_TIERS
        ):
            code = (
                "\n\n"
                + self._text["status_code"].format(
                    code=snapshot.status_notice_code
                )
                if snapshot.status_notice_code is not None
                else ""
            )
            self._publish(
                "warning",
                title=self._text["warning_title"].format(
                    name=snapshot.descriptor.title
                ),
                message=self._text["warning_message"].format(
                    display=snapshot.status_notice_display
                )
                + code,
            )
        else:
            self._dismiss("warning")

    def _publish(self, kind: str, *, title: str, message: str) -> None:
        content = _NotificationContent(title=title, message=message)
        if self._content.get(kind) == content:
            return
        persistent_notification.async_create(
            self._coordinator.hass,
            message,
            title=title,
            notification_id=self._notification_ids[kind],
        )
        self._content[kind] = content

    def _dismiss(self, kind: str, *, force: bool = False) -> None:
        if not force and kind not in self._content:
            return
        persistent_notification.async_dismiss(
            self._coordinator.hass,
            self._notification_ids[kind],
        )
        self._content.pop(kind, None)
