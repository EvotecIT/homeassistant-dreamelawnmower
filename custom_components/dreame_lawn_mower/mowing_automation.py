"""UI configuration adapter for the existing guarded mowing engine."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant.components import persistent_notification
from homeassistant.core import CoreState, Event, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.trigger import (
    async_initialize_triggers,
    async_validate_trigger_config,
)
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .managed_mowing_state import TASK_SCHEMA
from .mowing_automation_config import CONF_MOWING_AUTOMATION, automation_config
from .mowing_conditions import MowingConditions

_LOGGER = logging.getLogger(__name__)


def automation_runtime(coordinator: Any) -> MowingAutomation | None:
    """Share one lifecycle adapter between its mower and local helper entities."""
    runtime: MowingAutomation | None = getattr(coordinator, "mowing_automation", None)
    if runtime is None:
        config = automation_config(
            coordinator.entry.options.get(CONF_MOWING_AUTOMATION)
        )
        if config:
            runtime = MowingAutomation(coordinator, config)
            coordinator.mowing_automation = runtime
    return runtime


class MowingAutomation:
    """Adapt UI inputs into one owned request per real Schedule block start.

    No startup catch-up, browser timer, separate retry engine or mower settings
    writes are introduced here. ManagedMowing remains the dispatch authority.
    """

    def __init__(self, coordinator: Any, config: dict[str, Any]) -> None:
        self.coordinator = coordinator
        self.hass = coordinator.hass
        self.config = config
        self.enabled: bool = config["enabled"]
        self.conditions = MowingConditions(config)
        self.permitted = False
        self.reason = "Waiting for garden observations"
        self.entities: dict[str, Any] = {}
        self._closed = False
        self._started = False
        self._unsub: list[Callable[[], None]] = []
        self._setup_task: asyncio.Task[None] | None = None
        self._run_task: asyncio.Task[Any] | None = None
        self._trigger_error: str | None = None
        self.last_outcome: dict[str, Any] | None = None

    @callback
    def bind(self, kind: str, entity: Any) -> None:
        """Wait for actual registered entity IDs before attaching triggers."""
        self.entities[kind] = entity
        if len(self.entities) == 3 and not self._started and not self._closed:
            self._started = True
            self._setup_task = self.hass.async_create_task(self._async_start())

    async def _async_start(self) -> None:
        self._unsub.extend(
            [
                self.hass.bus.async_listen("state_changed", self._state_changed),
                self.hass.bus.async_listen(f"{DOMAIN}_scheduled_run", self._outcome),
                async_track_time_interval(
                    self.hass, self._evaluate, timedelta(seconds=15)
                ),
            ]
        )
        self._evaluate()
        if self.hass.state is CoreState.running:
            await self._attach()
        else:
            self._unsub.append(async_at_started(self.hass, self._attach))

    async def _attach(self, _: Any = None) -> None:
        if self._closed or not self.enabled or not self.config["schedule_entity"]:
            return
        try:
            config = await async_validate_trigger_config(
                self.hass,
                [
                    {
                        "platform": "schedule.block_started",
                        "target": {"entity_id": self.config["schedule_entity"]},
                    }
                ],
            )
            remove = await async_initialize_triggers(
                self.hass,
                config,
                self._run,
                DOMAIN,
                "Mowing automation",
                _LOGGER.log,
            )
            if remove is None:
                raise HomeAssistantError("Schedule trigger could not be attached")
            if self._closed:
                remove()
            else:
                self._unsub.append(remove)
        except (HomeAssistantError, ValueError, vol.Invalid) as err:
            self._trigger_error = f"Schedule unavailable: {err}"
            self._evaluate()
            _LOGGER.error("Cannot attach mowing automation schedule: %s", err)

    @callback
    def _state_changed(self, event: Event) -> None:
        if event.data.get("entity_id") in {
            self.config["soil_entity"],
            self.config["irrigation_entity"],
            self.config["permission_entity"],
        }:
            self._evaluate()

    @callback
    def _evaluate(self, _: Any = None) -> None:
        if self._closed:
            return
        previous = (self.permitted, self.reason)
        self.permitted, self.reason = self.conditions.evaluate(
            self.hass, dt_util.utcnow()
        )
        if self._trigger_error:
            self.permitted, self.reason = False, self._trigger_error
        permission = self.entities.get("permission")
        if permission is not None:
            # Heartbeat report; source freshness is checked by MowingConditions.
            permission.async_write_ha_state()
        if previous != (self.permitted, self.reason):
            self.coordinator.async_update_listeners()

    async def _run(self, variables: dict[str, Any], context: Any = None) -> None:
        if self._closed or not self.enabled or self._run_task is not None:
            return
        manager = getattr(self.coordinator, "managed_mowing", None)
        if manager is None:
            return
        self._run_task = asyncio.current_task()
        try:
            self._evaluate()
            occurrence = dt_util.utcnow().strftime("%Y-%m-%dT%H:%M")
            params = TASK_SCHEMA(
                {
                    "task_type": "all",
                    "minimum_battery": self.config["minimum_battery"],
                    "permission_entity": self.entities["permission"].entity_id,
                    "permission_max_age": 60,
                    "defer_minutes": self.config["defer_minutes"],
                    "interruption": self.config["interruption"],
                    "resume_when_allowed": self.config["resume_when_allowed"],
                    "recovery_minutes": self.config["recovery_minutes"],
                    "automation_entity": self.entities["enabled"].entity_id,
                    "run_id": f"ui:{self.coordinator.entry.entry_id}:{occurrence}",
                }
            )
            await manager.async_request(params)
        finally:
            self._run_task = None

    @callback
    def _outcome(self, event: Event) -> None:
        owner = self.entities.get("enabled")
        if owner is None or event.data.get("automation_entity") != owner.entity_id:
            return
        self.last_outcome = {
            key: event.data.get(key)
            for key in ("status", "reason", "detail", "observed_at")
        }
        self.coordinator.async_update_listeners()
        if self.config["notify_outcome"]:
            persistent_notification.async_create(
                self.hass,
                f"{event.data.get('status', '')}: "
                f"{event.data.get('reason', '').replace('_', ' ')}. "
                f"{event.data.get('detail') or ''}",
                title="Mowing automation",
                notification_id=f"{DOMAIN}_{self.coordinator.entry.entry_id}_automation",
            )

    async def async_cancel_owned(self) -> None:
        """Revoke only this wizard's intent before replacing its configuration."""
        self.enabled = False
        owner = self.entities.get("enabled")
        if owner is not None:
            owner.async_write_ha_state()
        manager = getattr(self.coordinator, "managed_mowing", None)
        if owner is not None and manager is not None:
            await manager.async_cancel_owner(owner.entity_id)

    async def async_set_enabled(self, enabled: bool) -> None:
        """Persist an opt-in toggle; disabling revokes intent without movement."""
        if enabled and (
            not self.config["schedule_entity"]
            or not self.config["native_plans_disabled"]
        ):
            raise HomeAssistantError(
                "Confirm disabled native plans in Mowing automation setup first"
            )
        if not enabled:
            await self.async_cancel_owned()
        options = dict(self.coordinator.entry.options)
        config = dict(options[CONF_MOWING_AUTOMATION])
        config["enabled"] = enabled
        options[CONF_MOWING_AUTOMATION] = config
        self.hass.config_entries.async_update_entry(
            self.coordinator.entry, options=options
        )
        self.enabled = enabled
        self.entities["enabled"].async_write_ha_state()

    @property
    def summary(self) -> dict[str, Any]:
        """Advertise UI capabilities and actual entity IDs to thin dashboards."""
        return {
            "version": 1,
            "configured": True,
            "enabled": self.enabled,
            "permitted": self.permitted,
            "reason": self.reason,
            "schedule_entity": self.config["schedule_entity"],
            "permission_entity": getattr(
                self.entities.get("permission"), "entity_id", None
            ),
            "enable_entity": getattr(self.entities.get("enabled"), "entity_id", None),
            "entry_id": self.coordinator.entry.entry_id,
            "last_outcome": self.last_outcome,
        }

    async def async_close(self) -> None:
        """Stop owned callbacks/tasks before the managed engine is drained."""
        if self._closed:
            return
        self._closed = True
        for unsub in self._unsub:
            unsub()
        self._unsub.clear()
        for task in (self._setup_task, self._run_task):
            if (
                task is not None
                and task is not asyncio.current_task()
                and not task.done()
            ):
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
