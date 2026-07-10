"""Live video camera entity for Dreame lawn mower."""

from __future__ import annotations

import asyncio
import logging
import platform
import shlex
from pathlib import Path
from typing import Any, Protocol

from homeassistant.components.camera import (
    DATA_CAMERA_PREFS,
    Camera,
    CameraEntityFeature,
)
from homeassistant.components.stream import create_stream
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_XP2P_LIBRARY_PATH,
    CONF_XP2P_RUNNER_COMMAND,
    CONF_XP2P_RUNNER_MODE,
    DOMAIN,
    XP2P_RUNNER_MODE_ONE_SHOT,
    XP2P_RUNNER_MODE_PROCESS,
)
from .coordinator import DreameLawnMowerCoordinator
from .dreame_lawn_mower_client.models import (
    DreameLawnMowerCameraStreamRuntimeInputs,
    snapshot_advertises_video,
)
from .dreame_lawn_mower_client.stream_health import (
    DreameLawnMowerStreamUrlProbeResult,
    probe_stream_url,
)
from .dreame_lawn_mower_client.video_runtime import (
    DreameLawnMowerNativeXp2pRuntime,
    DreameLawnMowerVideoRuntimeError,
    DreameLawnMowerXp2pExternalRunner,
    DreameLawnMowerXp2pLiveStreamSession,
    DreameLawnMowerXp2pProcessRunner,
    diagnose_native_xp2p_runtime,
)
from .dreame_lawn_mower_client.xp2p_host_runtime import (
    DreameLawnMowerXp2pHostRuntime,
)
from .dreame_lawn_mower_client.xp2p_runtime_bootstrap import (
    ensure_xp2p_host_runtime,
)

_LOGGER = logging.getLogger(__name__)
_STREAM_HEALTH_ATTEMPTS = 3
_STREAM_HEALTH_RETRY_INTERVAL = 0.5
_STREAM_HEALTH_TIMEOUT = 3.0
_STREAM_HEALTH_BYTES = 16
_HA_STREAM_START_TIMEOUT = 75.0


class _DreameVideoRuntime(Protocol):
    """Runtime contract shared by native and external XP2P adapters."""

    def start_live_stream(
        self,
        inputs: DreameLawnMowerCameraStreamRuntimeInputs,
    ) -> DreameLawnMowerXp2pLiveStreamSession:
        """Start live video and return a local stream session."""

    def stop_live_stream(self, session: DreameLawnMowerXp2pLiveStreamSession) -> None:
        """Stop a previously started stream session."""


class DreameLawnMowerVideoCamera(
    CoordinatorEntity[DreameLawnMowerCoordinator],
    Camera,
):
    """Live stream camera backed by a configured XP2P runtime."""

    _attr_has_entity_name = True
    _attr_name = "Live Video"
    _attr_icon = "mdi:video-wireless-outline"
    _attr_entity_registry_enabled_default = True
    _attr_supported_features = (
        CameraEntityFeature.STREAM | CameraEntityFeature.ON_OFF
    )

    def __init__(
        self,
        coordinator: DreameLawnMowerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        Camera.__init__(self)
        CoordinatorEntity.__init__(self, coordinator)
        self._entry = entry
        self._descriptor = coordinator.client.descriptor
        self._attr_unique_id = f"{self._descriptor.unique_id}_live_video"
        self._attr_brand = "Dreametech"
        self._attr_model = self._descriptor.display_model
        self._attr_is_on = True
        self.content_type = "video/x-flv"
        self._runtime: _DreameVideoRuntime | None = None
        self._prepared_runtime: _DreameVideoRuntime | None = None
        self._session: DreameLawnMowerXp2pLiveStreamSession | None = None
        self._stream_lock = asyncio.Lock()
        self._last_error: str | None = None
        self._last_runtime_inputs_ready: bool | None = None
        self._last_runtime_inputs_source: str | None = None
        self._last_runtime_inputs_missing: tuple[str, ...] = ()
        self._last_stream_health: dict[str, Any] | None = None
        self._last_stream_enable_result: Any | None = None
        self._last_stream_disable_error: str | None = None
        self._last_native_runtime_diagnostics: dict[str, Any] | None = None
        self._runtime_preparation_error: str | None = None

    async def async_added_to_hass(self) -> None:
        """Prepare the managed runtime before HA applies its stream timeout."""
        await super().async_added_to_hass()
        snapshot = self.coordinator.data
        if (
            not self._runtime_configured
            or snapshot is None
            or not snapshot_advertises_video(snapshot)
        ):
            return
        try:
            await self.hass.async_add_executor_job(self._create_runtime)
        except Exception as err:  # noqa: BLE001 - retry remains available on play.
            self._runtime_preparation_error = str(err)
            _LOGGER.warning("Failed to prepare Dreame mower live video: %s", err)
        else:
            self._runtime_preparation_error = None

    @property
    def available(self) -> bool:
        """Return whether live video can be requested from Home Assistant."""
        if not self._runtime_configured:
            return False
        snapshot = self.coordinator.data
        if snapshot is None:
            return False
        if bool(
            getattr(snapshot, "docked", False)
            or getattr(snapshot, "raw_docked", False)
        ):
            return False
        return snapshot_advertises_video(snapshot)

    @property
    def device_info(self) -> dict[str, Any]:
        """Return dynamic device metadata for the registry."""
        snapshot = self.coordinator.data
        descriptor = snapshot.descriptor if snapshot is not None else self._descriptor
        return {
            "identifiers": {(DOMAIN, descriptor.unique_id)},
            "manufacturer": "Dreametech",
            "model": descriptor.display_model,
            "name": descriptor.name,
            "sw_version": getattr(snapshot, "firmware_version", None),
            "hw_version": getattr(snapshot, "hardware_version", None),
            "serial_number": getattr(snapshot, "serial_number", None),
        }

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose stream readiness without leaking runtime credentials."""
        return {
            "video_runtime_configured": self._runtime_configured,
            "video_runtime_mode": self._runtime_mode,
            "xp2p_library_configured": bool(self._native_library_path),
            "xp2p_runner_configured": bool(self._runner_command),
            "managed_xp2p_runtime_supported": _managed_runtime_supported(),
            "video_runtime_preparation_error": self._runtime_preparation_error,
            "stream_session_active": self._session is not None,
            "last_stream_error": self._last_error,
            "last_runtime_inputs_ready": self._last_runtime_inputs_ready,
            "last_runtime_inputs_source": self._last_runtime_inputs_source,
            "last_runtime_inputs_missing": self._last_runtime_inputs_missing,
            "last_stream_enable_result": self._last_stream_enable_result,
            "last_stream_disable_error": self._last_stream_disable_error,
            "last_native_runtime_diagnostics": self._last_native_runtime_diagnostics,
            "last_stream_health": self._last_stream_health,
            "last_stream_session": self._session.as_dict(redact=True)
            if self._session is not None
            else None,
        }

    async def stream_source(self) -> str | None:
        """Start live video and return the local FLV source URL for HA stream."""
        async with self._stream_lock:
            if not getattr(self, "_attr_is_on", True):
                self._set_stream_error("Dreame mower live video is turned off.")
                return None
            if self._session is not None:
                process = getattr(self._session, "runner_process", None)
                if process is None or process.poll() is None:
                    return self._session.stream_url
                await self._async_stop_active_session()
            return await self._async_start_stream()

    async def async_create_stream(self) -> Any | None:
        """Create HA's stream with enough time for native XP2P startup."""
        if not self._create_stream_lock:
            self._create_stream_lock = asyncio.Lock()
        async with self._create_stream_lock:
            if self.stream is not None:
                return self.stream
            async with asyncio.timeout(_HA_STREAM_START_TIMEOUT):
                source = await self.stream_source()
            if not source:
                return None
            self.stream = create_stream(
                self.hass,
                source,
                options=self.stream_options,
                dynamic_stream_settings=await self.hass.data[
                    DATA_CAMERA_PREFS
                ].get_dynamic_stream_settings(self.entity_id),
                stream_label=self.entity_id,
            )
            self.stream.set_update_callback(self.async_write_ha_state)
            return self.stream

    async def _async_start_stream(self) -> str | None:
        """Start one serialized XP2P stream session."""
        self._last_stream_health = None
        stream_enabled = False
        runtime: _DreameVideoRuntime | None = None
        session: DreameLawnMowerXp2pLiveStreamSession | None = None
        if not self._runtime_configured:
            self._set_stream_error(
                "Configure a native XP2P library path or XP2P runner command."
            )
            return None

        try:
            inputs = (
                await self.coordinator.client.async_get_camera_stream_runtime_inputs()
            )
            self._last_runtime_inputs_ready = inputs.ready
            self._last_runtime_inputs_source = inputs.source
            self._last_runtime_inputs_missing = inputs.missing_required
            if not inputs.ready:
                self._set_stream_error(
                    "Dreame cloud did not return required XP2P fields: "
                    + ", ".join(inputs.missing_required)
                )
                return None

            await self._async_stop_active_session()
            runtime = await self.hass.async_add_executor_job(self._create_runtime)
            self._runtime_preparation_error = None
            self._last_stream_enable_result = (
                _safe_state_attribute(
                    await self.coordinator.client.async_set_camera_stream_enabled(True)
                )
            )
            stream_enabled = True
            self._last_stream_disable_error = None
            session = await self._async_start_runtime_session(runtime, inputs)
            stream_health = await self.hass.async_add_executor_job(
                _probe_stream_health,
                session.stream_url,
            )
        except asyncio.CancelledError:
            if runtime is not None and session is not None:
                await self._async_stop_session(runtime, session)
            if stream_enabled:
                await self._async_disable_camera_stream()
            raise
        except DreameLawnMowerVideoRuntimeError as err:
            if runtime is not None and session is not None:
                await self._async_stop_session(runtime, session)
            if stream_enabled:
                await self._async_disable_camera_stream()
            self._set_stream_error(str(err))
            return None
        except Exception as err:  # noqa: BLE001 - HA should receive a clean miss.
            if runtime is not None and session is not None:
                await self._async_stop_session(runtime, session)
            if stream_enabled:
                await self._async_disable_camera_stream()
            _LOGGER.warning("Failed to start Dreame mower live video: %s", err)
            self._set_stream_error(str(err))
            return None

        self._last_stream_health = stream_health.as_dict()
        if not stream_health.flv_header_present:
            await self._async_stop_session(runtime, session)
            await self._async_disable_camera_stream()
            self._set_stream_error(_stream_health_error(stream_health))
            return None

        self._runtime = runtime
        self._session = session
        self._last_error = None
        self._attr_is_on = True
        self._attr_is_streaming = True
        self.async_write_ha_state()
        return session.stream_url

    async def _async_start_runtime_session(
        self,
        runtime: _DreameVideoRuntime,
        inputs: DreameLawnMowerCameraStreamRuntimeInputs,
    ) -> DreameLawnMowerXp2pLiveStreamSession:
        """Finish or clean up native startup if the HA request is cancelled."""
        start_job = self.hass.async_add_executor_job(
            runtime.start_live_stream,
            inputs,
        )
        try:
            return await asyncio.shield(start_job)
        except asyncio.CancelledError:
            try:
                orphaned_session = await start_job
            except Exception:  # noqa: BLE001 - startup already failed cleanly.
                pass
            else:
                await self._async_stop_session(runtime, orphaned_session)
            raise

    async def async_turn_off(self) -> None:
        """Stop the current live video session."""
        async with self._stream_lock:
            await self._async_stop_active_session()
            self._attr_is_on = False
            self.async_write_ha_state()

    async def async_turn_on(self) -> None:
        """Allow Home Assistant to request a new live video session."""
        async with self._stream_lock:
            self._attr_is_on = True
            self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Stop XP2P video when Home Assistant unloads the camera."""
        async with self._stream_lock:
            await self._async_stop_active_session()

    async def _async_stop_active_session(self) -> None:
        """Stop the current runtime session if one is active."""
        runtime = self._runtime
        session = self._session
        self._runtime = None
        self._session = None
        self._attr_is_streaming = False
        if runtime is None or session is None:
            return
        await self._async_stop_session(runtime, session)
        await self._async_disable_camera_stream()
        self.async_write_ha_state()

    async def _async_stop_session(
        self,
        runtime: _DreameVideoRuntime,
        session: DreameLawnMowerXp2pLiveStreamSession,
    ) -> None:
        """Stop a runtime session without changing entity state bookkeeping."""
        try:
            await self.hass.async_add_executor_job(runtime.stop_live_stream, session)
        except Exception as err:  # noqa: BLE001 - cleanup should not break unload.
            _LOGGER.debug("Failed to stop Dreame mower live video: %s", err)

    async def _async_disable_camera_stream(self) -> None:
        """Best-effort app-side video cleanup."""
        try:
            await self.coordinator.client.async_set_camera_stream_enabled(False)
            self._last_stream_disable_error = None
        except Exception as err:  # noqa: BLE001 - cleanup should not break unload.
            self._last_stream_disable_error = str(err)
            _LOGGER.debug("Failed to disable Dreame mower app video mode: %s", err)

    def _create_runtime(self) -> _DreameVideoRuntime:
        """Create the configured runtime adapter."""
        if self._prepared_runtime is not None:
            return self._prepared_runtime
        if runner_command := self._runner_command:
            self._last_native_runtime_diagnostics = None
            command = _split_runner_command(runner_command)
            if self._runtime_mode == XP2P_RUNNER_MODE_ONE_SHOT:
                runtime: _DreameVideoRuntime = DreameLawnMowerXp2pExternalRunner(
                    command
                )
            else:
                runtime = DreameLawnMowerXp2pProcessRunner(command)
            self._prepared_runtime = runtime
            return runtime

        if library_path := self._native_library_path:
            path = Path(library_path)
            diagnostics = diagnose_native_xp2p_runtime(path)
            self._last_native_runtime_diagnostics = _safe_state_attribute(
                diagnostics.as_dict()
            )
            if not diagnostics.ready:
                raise DreameLawnMowerVideoRuntimeError(
                    diagnostics.error
                    or "Configured XP2P native library is not ready."
                )
            runtime = DreameLawnMowerNativeXp2pRuntime(path)
            self._prepared_runtime = runtime
            return runtime

        if _managed_runtime_supported():
            runtime_root = Path(
                self.hass.config.path(
                    ".storage",
                    DOMAIN,
                    "xp2p-runtime",
                )
            )
            runtime = DreameLawnMowerXp2pHostRuntime(
                ensure_xp2p_host_runtime(runtime_root)
            )
            self._prepared_runtime = runtime
            self._last_native_runtime_diagnostics = None
            return runtime

        raise DreameLawnMowerVideoRuntimeError(
            "Managed XP2P video requires a Linux aarch64 or x86_64 host. "
            "Configure an advanced native XP2P library or runner override on "
            "this platform."
        )

    @property
    def _runtime_configured(self) -> bool:
        return bool(
            self._runner_command
            or self._native_library_path
            or _managed_runtime_supported()
        )

    @property
    def _runtime_mode(self) -> str:
        if not self._runner_command and not self._native_library_path:
            return "managed"
        value = self._entry.options.get(CONF_XP2P_RUNNER_MODE)
        if value == XP2P_RUNNER_MODE_ONE_SHOT:
            return XP2P_RUNNER_MODE_ONE_SHOT
        return XP2P_RUNNER_MODE_PROCESS

    @property
    def _native_library_path(self) -> str | None:
        return _option_text(self._entry, CONF_XP2P_LIBRARY_PATH)

    @property
    def _runner_command(self) -> str | None:
        return _option_text(self._entry, CONF_XP2P_RUNNER_COMMAND)

    def _set_stream_error(self, error: str) -> None:
        self._last_error = error
        self._attr_is_streaming = False
        self.async_write_ha_state()


def _option_text(entry: ConfigEntry, key: str) -> str | None:
    """Return a trimmed non-empty string option."""
    value = entry.options.get(key)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _safe_state_attribute(value: Any, *, max_depth: int = 4) -> Any:
    """Return a bounded JSON-safe value for Home Assistant attributes."""
    if max_depth < 0:
        return repr(value)
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {
            str(key): _safe_state_attribute(item, max_depth=max_depth - 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_safe_state_attribute(item, max_depth=max_depth - 1) for item in value]
    return repr(value)


def _split_runner_command(command: str) -> tuple[str, ...]:
    """Split a configured runner command into executable and arguments."""
    try:
        parts = tuple(shlex.split(command))
    except ValueError as err:
        raise DreameLawnMowerVideoRuntimeError(
            f"Invalid XP2P runner command: {err}"
        ) from err
    if not parts:
        raise DreameLawnMowerVideoRuntimeError(
            "XP2P runner command cannot be empty."
        )
    return parts


def _managed_runtime_supported() -> bool:
    """Return whether the self-managed runtime supports this HA host."""
    if platform.system().casefold() != "linux":
        return False
    machine = platform.machine().casefold()
    return machine in {"amd64", "x64", "x86_64", "aarch64", "arm64"}


def _probe_stream_health(stream_url: str) -> DreameLawnMowerStreamUrlProbeResult:
    """Check the local stream before Home Assistant advertises it."""
    return probe_stream_url(
        stream_url,
        timeout=_STREAM_HEALTH_TIMEOUT,
        read_bytes=_STREAM_HEALTH_BYTES,
        attempts=_STREAM_HEALTH_ATTEMPTS,
        retry_interval=_STREAM_HEALTH_RETRY_INTERVAL,
    )


def _stream_health_error(health: DreameLawnMowerStreamUrlProbeResult) -> str:
    """Render a redacted reason for a local stream URL that did not serve FLV."""
    details = [f"error_category={health.error_category or 'unknown'}"]
    if health.status_code is not None:
        details.append(f"status_code={health.status_code}")
    if health.bytes_read:
        details.append(f"bytes_read={health.bytes_read}")
    if health.error:
        details.append(f"error={health.error}")
    return (
        "XP2P runtime returned a local stream URL, but it did not emit an FLV "
        f"header ({', '.join(details)})."
    )
