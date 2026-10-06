"""Shared cleanup and native-stop ownership for mower video sessions."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.stream.const import ATTR_STREAMS as ATTR_STREAMS
from homeassistant.components.stream.const import DOMAIN as _STREAM_DOMAIN

from .const import VIDEO_TRANSPORT_CLOUD, VIDEO_TRANSPORT_LAN
from .debug import sanitize_diagnostic_text
from .diagnostic_events import record_diagnostic_event
from .dreame_lawn_mower_client.video_runtime import DreameLawnMowerXp2pLiveStreamSession
from .video_camera_diagnostics import _VideoCameraDiagnostics
from .video_camera_types import _DreameVideoRuntime, _facade_binding

STREAM_DOMAIN = _STREAM_DOMAIN
_PENDING_RUNTIME_STOPS: dict[str, set[asyncio.Future[Any]]] = {}


class _VideoCameraCleanup(_VideoCameraDiagnostics):
    """Own bounded session cleanup and replacement-session fencing."""

    async def _async_stop_active_session(
        self,
        *,
        reason: str = "session_stop",
        trigger: str | None = None,
        failed_pump_task: asyncio.Task[Any] | None = None,
    ) -> None:
        """Stop the current runtime session if one is active."""
        self._last_stream_cleanup_reason = reason
        self._last_stream_cleanup_error = None
        self._last_stream_cleanup_error_stage = None
        if trigger is not None:
            record_diagnostic_event(
                self.coordinator,
                code="video_state_gate_cleanup",
                source="video_camera",
                message="Active live-video session stopped after mower state changed.",
                severity="info",
                context={"reason": reason, "trigger": trigger},
            )
        try:
            await self._stream_idle_monitor.async_cancel()
            relay = getattr(self, "_flv_relay", None)
            if relay is not None:
                if failed_pump_task is None:
                    await relay.async_stop_upstream()
                else:
                    await relay.async_stop_upstream(
                        expected_task=failed_pump_task,
                    )
            cleanup_task = asyncio.create_task(
                self._async_finish_active_session_cleanup()
            )
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                # Relay close may cancel its pump or idle callback while this
                # entity owns the only runtime/session references. Finish the
                # bounded resource cleanup before allowing cancellation out.
                await cleanup_task
                raise
        finally:
            self._last_stream_cleanup_at = datetime.now(UTC).isoformat()
            self.async_write_ha_state()

    async def _async_finish_active_session_cleanup(self) -> None:
        """Finish resource cleanup independently of a cancelled relay callback."""
        _HA_STREAM_STOP_TIMEOUT = _facade_binding("_HA_STREAM_STOP_TIMEOUT", 10.0)
        ha_stream = getattr(self, "stream", None)
        self.stream = None
        self._snapshot_owned_stream = None
        runtime = self._runtime
        session = self._session
        self._runtime = None
        self._session = None
        self._unverified_playback_session = None
        self._pending_provisioning_inputs = None
        self._attr_is_streaming = False
        if ha_stream is not None:
            try:
                async with asyncio.timeout(_HA_STREAM_STOP_TIMEOUT):
                    await ha_stream.stop()
            except TimeoutError:
                self._record_stream_cleanup_error(
                    "home_assistant_stream_stop",
                    (
                        "Home Assistant camera stream cleanup timed out after "
                        f"{_HA_STREAM_STOP_TIMEOUT:g}s."
                    ),
                )
            except Exception as err:  # noqa: BLE001 - continue XP2P cleanup.
                self._record_stream_cleanup_error(
                    "home_assistant_stream_stop",
                    err,
                )
            finally:
                self._unregister_ha_stream(ha_stream)
        if runtime is None or session is None:
            return
        await self._async_stop_session(runtime, session)
        camera_toggle_managed = getattr(
            session,
            "camera_toggle_managed",
            getattr(session, "transport", VIDEO_TRANSPORT_CLOUD)
            != VIDEO_TRANSPORT_LAN,
        )
        if camera_toggle_managed:
            await self._async_disable_camera_stream()

    def _unregister_ha_stream(self, ha_stream: Any) -> None:
        """Remove a discarded HA Stream from the integration registry."""
        hass = getattr(self, "hass", None)
        data = getattr(hass, "data", None)
        if not isinstance(data, dict):
            return
        stream_data = data.get(STREAM_DOMAIN)
        if not isinstance(stream_data, dict):
            return
        streams = stream_data.get(ATTR_STREAMS)
        if not isinstance(streams, list):
            return
        try:
            streams.remove(ha_stream)
        except ValueError:
            pass

    async def _async_stop_session(
        self,
        runtime: _DreameVideoRuntime,
        session: DreameLawnMowerXp2pLiveStreamSession,
    ) -> bool:
        """Stop a runtime session without changing entity state bookkeeping."""
        _RUNTIME_SESSION_STOP_TIMEOUT = _facade_binding(
            "_RUNTIME_SESSION_STOP_TIMEOUT", 20.0
        )
        try:
            stop_job = asyncio.ensure_future(
                self.hass.async_add_executor_job(runtime.stop_live_stream, session)
            )
        except Exception as err:  # noqa: BLE001 - cleanup should not break unload.
            self._record_stream_cleanup_error("runtime_session_stop", err)
            return False
        self._register_runtime_stop(stop_job)
        try:
            async with asyncio.timeout(_RUNTIME_SESSION_STOP_TIMEOUT):
                await asyncio.shield(stop_job)
            return True
        except TimeoutError:
            self._record_stream_cleanup_error(
                "runtime_session_stop",
                (
                    "Dreame mower live-video runtime cleanup timed out after "
                    f"{_RUNTIME_SESSION_STOP_TIMEOUT:g}s."
                ),
            )
            return False
        except Exception as err:  # noqa: BLE001 - cleanup should not break unload.
            self._record_stream_cleanup_error("runtime_session_stop", err)
            return False

    def _register_runtime_stop(self, stop_job: asyncio.Future[Any]) -> None:
        """Fence replacement sessions until an executor-backed stop really ends."""
        key = self._runtime_stop_key
        jobs = _PENDING_RUNTIME_STOPS.setdefault(key, set())
        jobs.add(stop_job)

        def _completed(future: asyncio.Future[Any]) -> None:
            pending = _PENDING_RUNTIME_STOPS.get(key)
            if pending is not None:
                pending.discard(future)
                if not pending:
                    _PENDING_RUNTIME_STOPS.pop(key, None)
            if future.cancelled():
                return
            try:
                future.exception()
            except (asyncio.CancelledError, Exception):
                pass

        stop_job.add_done_callback(_completed)

    @property
    def _runtime_stop_key(self) -> str:
        """Return a stable mower identity shared across entity reloads."""
        descriptor = getattr(self, "_descriptor", None)
        return str(
            getattr(descriptor, "did", None)
            or getattr(descriptor, "unique_id", None)
            or self._attr_unique_id
        )

    @property
    def _runtime_cleanup_pending(self) -> bool:
        """Return whether an earlier native stop still owns this mower service."""
        jobs = _PENDING_RUNTIME_STOPS.get(self._runtime_stop_key)
        if not jobs:
            return False
        active = {job for job in jobs if not job.done()}
        if active:
            _PENDING_RUNTIME_STOPS[self._runtime_stop_key] = active
            return True
        _PENDING_RUNTIME_STOPS.pop(self._runtime_stop_key, None)
        return False

    async def _async_stop_session_for_handoff(
        self,
        runtime: _DreameVideoRuntime,
        session: DreameLawnMowerXp2pLiveStreamSession,
    ) -> bool:
        """Finish probe cleanup before cancellation or a replacement session."""
        stop_task = asyncio.create_task(self._async_stop_session(runtime, session))
        try:
            return await asyncio.shield(stop_task)
        except asyncio.CancelledError:
            await stop_task
            raise

    async def _async_disable_camera_stream(self) -> None:
        """Best-effort app-side video cleanup."""
        _CAMERA_STREAM_DISABLE_TIMEOUT = _facade_binding(
            "_CAMERA_STREAM_DISABLE_TIMEOUT", 10.0
        )
        try:
            async with asyncio.timeout(_CAMERA_STREAM_DISABLE_TIMEOUT):
                await self.coordinator.client.async_set_camera_stream_enabled(False)
            self._last_stream_disable_error = None
        except TimeoutError:
            self._last_stream_disable_error = (
                "Dreame app video-mode cleanup timed out after "
                f"{_CAMERA_STREAM_DISABLE_TIMEOUT:g}s."
            )
            self._record_stream_cleanup_error(
                "camera_stream_disable",
                self._last_stream_disable_error,
            )
        except Exception as err:  # noqa: BLE001 - cleanup should not break unload.
            self._last_stream_disable_error = sanitize_diagnostic_text(err)
            self._record_stream_cleanup_error(
                "camera_stream_disable",
                self._last_stream_disable_error,
            )
