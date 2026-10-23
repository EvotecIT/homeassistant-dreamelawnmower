"""HA lifecycle for optional deferred starts and owned-task pause recovery."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from homeassistant.const import EVENT_CALL_SERVICE, EVENT_STATE_CHANGED
from homeassistant.core import CoreState, Event, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .dreame_lawn_mower_client import DreameLawnMowerConnectionError
from .dreame_lawn_mower_client.schedule_start import scheduled_start_block_reason
from .dreame_lawn_mower_client.session_checkpoint import session_identity
from .managed_mowing_state import decode_managed_record
from .mowing_permission import permission_block
from .observation_checkpoint import ObservationStore, checkpoint_fits
from .scheduled_mowing import _publish, async_start_scheduled_mowing

_LOGGER = logging.getLogger(__name__)
_DEFER_REASONS = {
    "external_permission_blocked",
    "external_permission_unknown",
    "external_permission_stale",
    "battery_below_threshold_or_invalid",
    "rain_delay_active",
    "connectivity_unknown_or_offline",
    "eligibility_refresh_failed",
}


async def async_remove_managed_mowing(hass: Any, entry: Any) -> None:
    """Remove private automation intent with its configuration entry."""
    await ObservationStore(
        hass,
        1,
        f"{DOMAIN}.{entry.entry_id}.managed_mowing",
        private=True,
        atomic_writes=True,
    ).async_remove()


class ManagedMowing:
    """Keep one bounded intent; persist admission before any physical command.

    Only pending, never-dispatched intents may survive a restart. A restarted
    observation cannot prove that no manual intervention occurred while HA was
    absent, so active or paused runs lose their automatic recovery authority.
    """

    def __init__(self, entity: Any) -> None:
        self.entity = entity
        self.hass = entity.hass
        self.coordinator = entity.coordinator
        self.record: dict[str, Any] | None = None
        self._last_run_id = ""
        self._cancel_reason: str | None = None
        self._request_params: dict[str, Any] | None = None
        self._restored = False
        self._startup_wait = False
        self._closed = False
        self._last_emission: tuple[Any, ...] | None = None
        self._last_result: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._unsub: list[Callable[[], None]] = []
        self._store = ObservationStore(
            self.hass,
            1,
            f"{DOMAIN}.{self.coordinator.entry.entry_id}.managed_mowing",
            private=True,
            atomic_writes=True,
        )

    async def async_start(self) -> None:
        """Load private state and attach owned listeners after entity setup."""
        saved = await self._store.async_load()
        if isinstance(saved, dict):
            last = saved.get("last_run_id")
            self._last_run_id = (
                last if isinstance(last, str) and len(last) <= 128 else ""
            )
            self.record = decode_managed_record(saved.get("record"), dt_util.utcnow())
        if self.record:
            self._restored = True
            self._startup_wait = self.hass.state is not CoreState.running
            if self.record["phase"] != "waiting":
                self._cancel_reason = "restart_cancelled_recovery"
            elif not self.record["params"]["defer_minutes"]:
                self._cancel_reason = "restart_without_deferred_window"
        self._unsub = [
            self.hass.bus.async_listen(EVENT_CALL_SERVICE, self._service_called),
            self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._state_changed),
            self.coordinator.async_add_listener(self._schedule),
            async_track_time_interval(self.hass, self._schedule, timedelta(seconds=30)),
        ]
        if self._startup_wait:
            self._unsub.append(async_at_started(self.hass, self._started))
        if self.record:
            self._schedule()

    async def async_close(self) -> None:
        """Detach listeners and drain only this owner's task before unload."""
        self._closed = True
        for unsub in self._unsub:
            unsub()
        self._unsub.clear()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        async with self._lock:
            # A completed revocation must survive even if its queued tick was
            # cancelled by this unload. Ordinary pending intent remains restorable.
            await self._flush_revocation()

    async def _flush_revocation(self) -> None:
        """Persist revocation with the service/lifecycle lock already held."""
        if self._cancel_reason:
            if self.record:
                await self._finish("cancelled", self._cancel_reason)
            else:
                # HA can eagerly enter the tick and clear the in-memory
                # record before its atomic disk write is cancelled by unload.
                await self._save()

    async def async_cancel(self) -> None:
        """Return from explicit cancellation only after its durable revocation."""
        self.cancel("scheduled_run_cancelled")
        async with self._lock:
            await self._flush_revocation()

    async def async_cancel_owner(self, owner_entity: str) -> None:
        """Revoke a matching owner, including a request still doing preflight."""
        params = self.record["params"] if self.record else (self._request_params or {})
        if params.get("automation_entity") == owner_entity:
            await self.async_cancel()

    @callback
    def _started(self, _: Any) -> None:
        self._startup_wait = False
        self._schedule()

    @property
    def summary(self) -> dict[str, Any] | None:
        """Expose intent without treating a pending run as an active mower task."""
        if not self.record:
            return None
        return {
            key: self.record[key]
            for key in (
                "run_id",
                "phase",
                "map_index",
                "start_deadline",
                "recovery_deadline",
            )
        } | {"permission_entity": self.record["params"]["permission_entity"]}

    async def async_request(self, params: dict[str, Any]) -> dict[str, Any]:
        """Accept one request, or reject overlap; never replace an existing intent."""
        requested_at = dt_util.utcnow()
        if self._closed or self._lock.locked():
            return self._emit(
                "skipped", "managed_request_busy_or_closed", params=params
            )
        async with self._lock:
            if self.record:
                return self._emit("skipped", "managed_run_in_progress", params=params)
            if params["run_id"] and params["run_id"] == self._last_run_id:
                return self._emit("skipped", "duplicate_scheduled_run", params=params)
            if params["resume_when_allowed"] and (
                params["interruption"] != "pause" or params["recovery_minutes"] == 0
            ):
                return self._emit(
                    "skipped", "recovery_requires_pause_and_deadline", params=params
                )
            if params["interruption"] != "none" and not params["permission_entity"]:
                return self._emit(
                    "skipped", "interruption_requires_permission", params=params
                )
            retirement_pending = self._cancel_reason is not None
            self._cancel_reason = None
            self._request_params = params
            if retirement_pending:
                try:
                    # Retire an older failed write before a new preflight can
                    # return early. The old reason is cleared before awaiting,
                    # preserving any new cancellation arriving during this save.
                    await self._save()
                except (OSError, ValueError):
                    self._cancel_reason = (
                        self._cancel_reason or "managed_storage_unavailable"
                    )
                    return self._emit(
                        "failed", "managed_storage_unavailable", params=params
                    )
            try:
                native = (
                    await self.coordinator.client.async_get_schedule_start_evidence()
                )
                snapshot = (
                    await self.coordinator.client.async_refresh_authoritative_snapshot()
                )
            except DreameLawnMowerConnectionError:
                return self._emit(
                    "skipped", "eligibility_refresh_failed", params=params
                )
            map_index = native.get("current_map_index")
            if type(map_index) is not int or map_index < 0:
                return self._emit("skipped", "current_map_unknown", params=params)
            now = requested_at
            baseline_id = session_identity(getattr(snapshot, "mission_task_id", None))
            if params["interruption"] != "none" and baseline_id is None:
                return self._emit(
                    "skipped", "managed_task_identity_required", params=params
                )
            if params["interruption"] == "pause":
                try:
                    settings = await self.coordinator.client.async_get_device_settings()
                except DreameLawnMowerConnectionError:
                    settings = {}
                if settings.get("resume_after_charging") is not False:
                    return self._emit(
                        "skipped",
                        "native_auto_resume_enabled_or_unknown",
                        params=params,
                    )
            if self._cancel_reason or self._closed:
                return self._emit(
                    "cancelled",
                    self._cancel_reason or "managed_run_closed",
                    params=params,
                )
            self.record = {
                "params": params,
                "run_id": params["run_id"] or str(uuid4()),
                "map_index": map_index,
                "task_id": None,
                "baseline_task_id": session_identity(
                    getattr(snapshot, "mission_task_id", None)
                ),
                "phase": "waiting",
                "created_at": now.isoformat(),
                "command_at": None,
                "start_deadline": (
                    now + timedelta(minutes=params["defer_minutes"])
                ).isoformat()
                if params["defer_minutes"]
                else None,
                "recovery_deadline": (
                    now + timedelta(minutes=params["recovery_minutes"])
                ).isoformat()
                if params["resume_when_allowed"]
                else None,
            }
            try:
                await self._save()
                return await self._tick_locked()
            except (OSError, ValueError):
                _LOGGER.exception("Managed mowing cannot persist its intent")
                return await self._fail("managed_storage_unavailable", params=params)
            except Exception:
                _LOGGER.exception("Managed mowing request stopped after a failure")
                return await self._fail("managed_run_failed", params=params)

    @callback
    def cancel(self, reason: str = "manual_intervention") -> None:
        """Revoke dispatch permission synchronously, including during preflight."""
        if self.record or self._lock.locked():
            self._cancel_reason = reason
            self._schedule()

    @callback
    def _schedule(self, *_: Any) -> None:
        if (
            not self._closed
            and self.record
            and (self._task is None or self._task.done())
        ):
            self._task = self.hass.async_create_task(self._async_tick())

    async def _async_tick(self) -> None:
        async with self._lock:
            if self._closed or not self.record:
                return
            try:
                await self._tick_locked()
            except asyncio.CancelledError:
                raise
            except DreameLawnMowerConnectionError:
                self._emit("failed", "managed_observation_unavailable")
            except Exception:
                _LOGGER.exception("Managed mowing stopped after an unexpected failure")
                if self.record:
                    await self._fail("managed_run_failed")

    async def _fail(
        self, reason: str, *, params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Retire failed intent durably; retry revocation on close after disk errors."""
        self._cancel_reason = reason
        result = self._emit(
            "failed", reason, params=params if not self.record else None
        )
        try:
            await self._clear()
        except (OSError, ValueError):
            # _clear retires the run ID and clears memory before writing. Keep
            # both, and keep revocation set so unload retries its persistence.
            _LOGGER.exception("Managed mowing cannot persist its failure")
        return result

    async def _tick_locked(self) -> dict[str, Any]:
        assert self.record is not None
        params = self.record["params"]
        owner = params["automation_entity"]
        if self._cancel_reason:
            return await self._finish("cancelled", self._cancel_reason)
        if self._startup_wait:
            return self._emit("deferred", "waiting_for_home_assistant_start")
        if owner and not self.hass.states.is_state(owner, "on"):
            return await self._finish("cancelled", "automation_disabled_or_unknown")
        phase = self.record["phase"]
        deadline = self.record[
            "start_deadline" if phase == "waiting" else "recovery_deadline"
        ]
        if deadline and dt_util.utcnow() >= datetime.fromisoformat(deadline):
            if phase in {"waiting", "paused"}:
                return await self._finish("expired", "scheduled_window_expired")
        if phase == "waiting":
            return await self._attempt_start()
        return await self._monitor()

    def _dispatch_block(self) -> str | None:
        if self._closed:
            return "managed_run_closed"
        if self._cancel_reason:
            return self._cancel_reason
        if self._startup_wait:
            return "waiting_for_home_assistant_start"
        assert self.record is not None
        params = self.record["params"]
        owner = params["automation_entity"]
        if owner and not self.hass.states.is_state(owner, "on"):
            return "automation_disabled_or_unknown"
        phase = self.record["phase"]
        deadline = (
            self.record["recovery_deadline"]
            if phase == "resuming"
            else (self.record["start_deadline"] if phase == "dispatching" else None)
        )
        if deadline and dt_util.utcnow() >= datetime.fromisoformat(deadline):
            return "scheduled_window_expired"
        return None

    async def _attempt_start(self) -> dict[str, Any]:
        assert self.record is not None
        params = self.record["params"]
        if self._restored:
            snapshot = (
                await self.coordinator.client.async_refresh_authoritative_snapshot()
            )
            baseline = self.record["baseline_task_id"]
            if (
                baseline is None
                or session_identity(getattr(snapshot, "mission_task_id", None))
                != baseline
            ):
                return await self._finish(
                    "cancelled", "restart_task_identity_unverified"
                )
            self._restored = False

        async def before_dispatch() -> None:
            assert self.record is not None
            self.record["phase"] = "dispatching"
            await self._save()

        arguments = {
            key: value
            for key, value in params.items()
            if key
            in {
                "task_type",
                "minimum_battery",
                "map_index",
                "zone_ids",
                "spot_ids",
                "contour_ids",
                "permission_entity",
                "permission_max_age",
            }
        }
        result = await async_start_scheduled_mowing(
            self.entity,
            **arguments,
            _expected_map_index=self.record["map_index"],
            _dispatch_block=self._dispatch_block,
            _record_outcomes=False,
            _before_dispatch=before_dispatch,
            _previous_task_id=self.record["baseline_task_id"],
        )
        if self._cancel_reason:
            return await self._finish("cancelled", self._cancel_reason)
        if result["reason"] == "scheduled_window_expired":
            return await self._finish("expired", "scheduled_window_expired")
        if result["reason"] == "pending_task_changed_or_unknown":
            return await self._finish("cancelled", "pending_task_changed_or_unknown")
        if (
            result["status"] == "skipped"
            and result["reason"] in _DEFER_REASONS
            and params["defer_minutes"]
        ):
            if self.record["phase"] != "waiting":
                self.record["phase"] = "waiting"
                await self._save()
            return self._emit("deferred", result["reason"], detail=result.get("detail"))
        self._emit(result["status"], result["reason"], detail=result.get("detail"))
        if (
            result["status"] in {"started", "submitted"}
            and params["interruption"] != "none"
        ):
            self.record["phase"] = "observing"
            self.record["command_at"] = dt_util.utcnow().isoformat()
            await self._save()
        else:
            await self._clear()
        return result

    async def _monitor(self) -> dict[str, Any]:
        assert self.record is not None
        params = self.record["params"]
        snapshot = await self.coordinator.client.async_refresh_authoritative_snapshot()
        native = await self.coordinator.client.async_get_schedule_start_evidence()
        if self._cancel_reason:
            return await self._finish("cancelled", self._cancel_reason)
        if native.get("current_map_index") != self.record["map_index"]:
            return await self._finish("cancelled", "managed_map_changed_or_unknown")
        identity = session_identity(getattr(snapshot, "mission_task_id", None))
        phase = self.record["phase"]
        if phase == "observing":
            if (
                identity is not None
                and identity != self.record["baseline_task_id"]
                and snapshot.mowing_session_active is True
            ):
                self.record["task_id"] = identity
                self.record["phase"] = "running"
                await self._save()
            elif (
                dt_util.utcnow() - datetime.fromisoformat(self.record["command_at"])
            ).total_seconds() >= 120:
                return await self._finish(
                    "cancelled", "managed_task_identity_unconfirmed"
                )
            else:
                return self._emit("submitted", "awaiting_managed_task_identity")
        elif identity is None or identity != self.record["task_id"]:
            return await self._finish("cancelled", "managed_task_changed_or_unknown")
        if phase == "confirming_resume":
            if snapshot.paused is False and snapshot.mowing_session_active is True:
                self.record["phase"] = "running"
                await self._save()
            elif (
                dt_util.utcnow() - datetime.fromisoformat(self.record["command_at"])
            ).total_seconds() >= 120:
                return await self._finish("failed", "resume_not_confirmed")
            else:
                return self._emit("submitted", "resume_request_submitted")
        blocked = permission_block(
            self.hass, params["permission_entity"], params["permission_max_age"]
        )
        if self.record["phase"] == "paused":
            if snapshot.paused is not True or snapshot.task_resumable is not True:
                return await self._finish(
                    "cancelled", "manual_or_external_intervention"
                )
            settings = await self.coordinator.client.async_get_device_settings()
            if settings.get("resume_after_charging") is not False:
                return await self._interrupt(
                    (
                        "native_auto_resume_enabled_or_unknown",
                        "Native resume setting changed",
                    ),
                    end_task=True,
                )
            if not blocked and params["resume_when_allowed"]:
                return await self._resume(snapshot, native)
            return self._emit(
                "paused", blocked[0] if blocked else "automatic_resume_disabled"
            )
        if snapshot.mowing_session_active is False and snapshot.task_resumable is False:
            return await self._finish("finished", "managed_task_no_longer_active")
        if snapshot.paused is True:
            return await self._finish("cancelled", "manual_or_external_intervention")
        if blocked:
            return await self._interrupt(blocked)
        return self._emit("started", "managed_task_observed")

    async def _interrupt(
        self,
        blocked: tuple[str, str],
        *,
        end_task: bool = False,
    ) -> dict[str, Any]:
        assert self.record is not None
        command = "dock" if end_task else self.record["params"]["interruption"]
        self.record["phase"] = "pausing"
        await self._save()
        if self._cancel_reason:
            return await self._finish("cancelled", self._cancel_reason)
        try:
            outcome = await self.coordinator.client.async_control_managed_task(
                command,
                expected_task_id=self.record["task_id"],
                expected_map_index=self.record["map_index"],
                admission=lambda: self._dispatch_block() is None,
            )
            if outcome == "docked":
                return await self._finish(
                    "cancelled", "permission_ended_task_and_docked"
                )
            snapshot = (
                await self.coordinator.client.async_refresh_authoritative_snapshot()
            )
            if (
                snapshot.paused is not True
                or snapshot.mission_task_id != self.record["task_id"]
            ):
                return await self._finish("failed", "pause_not_confirmed")
        except DreameLawnMowerConnectionError:
            return await self._finish("failed", "interruption_not_confirmed")
        self.record["phase"] = "paused"
        await self._save()
        return self._emit("paused", blocked[0], detail=blocked[1])

    async def _resume(self, snapshot: Any, native: dict[str, Any]) -> dict[str, Any]:
        assert self.record is not None
        params = self.record["params"]
        deadline = self.record["recovery_deadline"]
        if not deadline or dt_util.utcnow() >= datetime.fromisoformat(deadline):
            return await self._finish("expired", "recovery_window_expired")
        weather = await self.coordinator.client.async_get_weather_protection(
            include_raw=False
        )
        if reason := scheduled_start_block_reason(
            snapshot,
            weather,
            native,
            minimum_battery=params["minimum_battery"],
            map_index=self.record["map_index"],
            resume_task_id=self.record["task_id"],
        ):
            return self._emit("paused", reason)
        self.record["phase"] = "resuming"
        await self._save()

        def admission() -> bool:
            return (
                self._dispatch_block() is None
                and permission_block(
                    self.hass,
                    params["permission_entity"],
                    params["permission_max_age"],
                )
                is None
            )

        try:
            await self.coordinator.client.async_control_managed_task(
                "resume",
                expected_task_id=self.record["task_id"],
                expected_map_index=self.record["map_index"],
                admission=admission,
            )
        except DreameLawnMowerConnectionError:
            return await self._finish("failed", "resume_not_confirmed")
        self.record["phase"] = "confirming_resume"
        self.record["command_at"] = dt_util.utcnow().isoformat()
        await self._save()
        return self._emit("submitted", "resume_request_submitted")

    def _emit(
        self,
        status: str,
        reason: str,
        *,
        params: dict[str, Any] | None = None,
        detail: str | None = None,
    ) -> dict[str, Any]:
        request_scoped = params is not None
        record = None if request_scoped else self.record
        params = params or (record["params"] if record else {})
        emission = (record["run_id"] if record else None, status, reason, detail)
        if not request_scoped and emission == self._last_emission:
            return dict(self._last_result)
        targets = params.get(
            {"zone": "zone_ids", "spot": "spot_ids", "edge": "contour_ids"}.get(
                params.get("task_type", "all"), ""
            )
        )
        result = _publish(
            self.entity,
            params.get("task_type", "all"),
            status,
            reason,
            map_index=record["map_index"] if record else params.get("map_index"),
            targets=targets,
            detail=detail,
            event_data={
                "run_id": record["run_id"] if record else params.get("run_id", ""),
                "automation_entity": params.get("automation_entity", ""),
            },
        )
        if not request_scoped:
            self._last_emission, self._last_result = emission, result
        return result

    async def _finish(self, status: str, reason: str) -> dict[str, Any]:
        result = self._emit(status, reason)
        await self._clear()
        return result

    async def _clear(self) -> None:
        if self.record:
            self._last_run_id = self.record["run_id"]
        self.record = None
        await self._save()
        self.coordinator.async_update_listeners()

    async def _save(self) -> None:
        record = {"record": self.record, "last_run_id": self._last_run_id}
        if not checkpoint_fits(record, self._store.key):
            raise ValueError("Managed mowing state exceeds the private storage budget")
        write = self.hass.async_create_task(self._store.async_save(record))
        try:
            await asyncio.shield(write)
        except asyncio.CancelledError:
            # Store's atomic executor write can outlive its cancelled await.
            # Drain it under our lifecycle lock before a final revocation save,
            # so older waiting bytes cannot replace that revocation afterward.
            await write
            raise

    @callback
    def _state_changed(self, event: Event) -> None:
        if self.record and event.data.get("entity_id") in {
            self.record["params"]["permission_entity"],
            self.record["params"]["automation_entity"],
        }:
            owner = self.record["params"]["automation_entity"]
            if (
                event.data.get("entity_id") == owner
                and not self._startup_wait
                and not self.hass.states.is_state(owner, "on")
            ):
                self.cancel("automation_disabled_or_unknown")
                return
            self._schedule()

    @callback
    def _service_called(self, event: Event) -> None:
        if not self.record and not self._lock.locked():
            return
        params = self.record["params"] if self.record else (self._request_params or {})
        domain, service = event.data.get("domain"), event.data.get("service")
        data = event.data.get("service_data", {})
        entity_ids = data.get("entity_id", [])
        if isinstance(entity_ids, str):
            entity_ids = [entity_ids]
        if domain == "automation" and service in {"turn_off", "toggle", "reload"}:
            if service == "reload" or params.get("automation_entity") in entity_ids:
                self.cancel("automation_disabled_or_reloaded")
            return
        if domain not in {"lawn_mower", DOMAIN, "button", "select"}:
            return
        if domain == DOMAIN and service == "start_scheduled_mowing":
            return
        registry = er.async_get(self.hass)
        own = er.async_entries_for_config_entry(
            registry, self.coordinator.entry.entry_id
        )
        own_ids = {entry.entity_id for entry in own} | {self.entity.entity_id}
        devices = data.get("device_id", [])
        if isinstance(devices, str):
            devices = [devices]
        if (
            "all" in entity_ids
            or own_ids.intersection(entity_ids)
            or any(entry.device_id in devices for entry in own)
        ):
            self.cancel()
