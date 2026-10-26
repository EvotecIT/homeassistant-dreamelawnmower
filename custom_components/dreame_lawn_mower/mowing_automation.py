"""UI configuration adapter for the existing guarded mowing engine."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import date, timedelta
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
from .mowing_automation_config import (
    CONF_MOWING_AUTOMATION,
    ROTATION_SETTINGS,
    automation_config,
)
from .mowing_conditions import MowingConditions
from .option_updates import EntryUpdateSnapshot
from .sensor_zone_history import current_zone_overview
from .zone_rotation import due_zone_ids

_LOGGER = logging.getLogger(__name__)
# Settings controls may bind too; only these three gate the schedule trigger.
REQUIRED_BINDINGS = frozenset({"permission", "enabled", "mower"})


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
        if (
            REQUIRED_BINDINGS <= self.entities.keys()
            and not self._started
            and not self._closed
        ):
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
            request: dict[str, Any] = {
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
            if self.config["mowing_target"] == "rotation":
                selection = self._rotation_target()
                if isinstance(selection, str):
                    manager.skip(
                        dict(request, task_type="zone", zone_ids=[]), selection
                    )
                    return
                request.update(selection)
            await manager.async_request(TASK_SCHEMA(request))
        finally:
            self._run_task = None

    def _rotation_target(self, today: date | None = None) -> dict[str, Any] | str:
        """Due whole zones of the selected map, or why none can be chosen.

        The scheduled start and the dashboard preview both call this, so the
        preview cannot announce zones the start would not choose.
        """
        coordinator = self.coordinator
        history = getattr(coordinator, "zone_mowing_sessions", None)
        map_index = getattr(coordinator, "selected_map_index", None)
        if (
            history is None
            or not history.complete
            or not history.attributable
            or type(map_index) is not int
        ):
            return "zone_history_unavailable"
        overview = current_zone_overview(coordinator)
        if not overview:
            return "zone_history_unavailable"
        zone_ids = due_zone_ids(
            overview,
            rest_days=self.config["rotation_rest_days"],
            max_area=self.config["rotation_max_area"],
            today=today or dt_util.now().date(),
        )
        if not zone_ids:
            return "no_zone_due"
        return {"task_type": "zone", "zone_ids": zone_ids, "map_index": map_index}

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

    async def async_set_rotation(self, **changes: Any) -> None:
        """Persist rotation choices in place; the next start reads them.

        Unlike the wizard, these settings never need a reload: the runtime
        applies them at once and records the entry as already applied.
        """
        if set(changes) - ROTATION_SETTINGS:
            raise HomeAssistantError("Only rotation settings change in place")
        config = automation_config({**self.config, **changes})
        if not config:
            raise HomeAssistantError("Invalid mowing automation setting")
        entry = self.coordinator.entry
        options = dict(entry.options)
        options[CONF_MOWING_AUTOMATION] = config
        self.config = config
        self.hass.config_entries.async_update_entry(entry, options=options)
        if isinstance(
            getattr(self.coordinator, "applied_entry_update", None),
            EntryUpdateSnapshot,
        ):
            self.coordinator.applied_entry_update = EntryUpdateSnapshot.capture(entry)
        self.coordinator.async_update_listeners()

    def plan(self) -> dict[str, Any]:
        """What the next Schedule block start would request, if it began now.

        Native mower checks, permission and battery still apply at the start.
        """
        state = self.hass.states.get(self.config["schedule_entity"] or "")
        next_start = None
        if state is not None and state.state == "off":
            next_start = dt_util.parse_datetime(
                str(state.attributes.get("next_event") or "")
            )
        result: dict[str, Any] = {
            "next_start": next_start.isoformat() if next_start else None,
            "task_type": "all",
            "zones": [],
            "area_m2": None,
            "reason": None,
        }
        if self.config["mowing_target"] != "rotation":
            return result
        day = dt_util.as_local(next_start).date() if next_start else None
        target = self._rotation_target(day)
        result["task_type"] = "zone"
        if isinstance(target, str):
            result["reason"] = target
            return result
        overview = {
            item["zone_id"]: item for item in current_zone_overview(self.coordinator)
        }
        zones = [
            {
                "zone_id": zone_id,
                "label": overview.get(zone_id, {}).get("label"),
                "area_m2": overview.get(zone_id, {}).get("area_m2"),
            }
            for zone_id in target["zone_ids"]
        ]
        areas = [zone["area_m2"] for zone in zones]
        result["zones"] = zones
        if all(isinstance(area, int | float) for area in areas):
            result["area_m2"] = round(sum(areas), 1)
        return result

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
            "mowing_target": self.config["mowing_target"],
            "rotation_rest_days": self.config["rotation_rest_days"],
            "rotation_max_area": self.config["rotation_max_area"],
            "target_entity": getattr(self.entities.get("target"), "entity_id", None),
            "rest_days_entity": getattr(
                self.entities.get("rest_days"), "entity_id", None
            ),
            "max_area_entity": getattr(
                self.entities.get("max_area"), "entity_id", None
            ),
            "plan": self.plan(),
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
