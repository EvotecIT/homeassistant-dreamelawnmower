"""Live video camera entity for Dreame lawn mower."""

from __future__ import annotations

import logging
import shlex
from pathlib import Path
from typing import Any, Protocol

from homeassistant.components.camera import Camera, CameraEntityFeature
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
)
from .dreame_lawn_mower_client.video_runtime import (
    DreameLawnMowerNativeXp2pRuntime,
    DreameLawnMowerVideoRuntimeError,
    DreameLawnMowerXp2pExternalRunner,
    DreameLawnMowerXp2pLiveStreamSession,
    DreameLawnMowerXp2pProcessRunner,
)

_LOGGER = logging.getLogger(__name__)


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
    _attr_entity_registry_enabled_default = False
    _attr_supported_features = CameraEntityFeature.STREAM

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
        self.content_type = "video/x-flv"
        self._runtime: _DreameVideoRuntime | None = None
        self._session: DreameLawnMowerXp2pLiveStreamSession | None = None
        self._last_error: str | None = None
        self._last_runtime_inputs_ready: bool | None = None
        self._last_runtime_inputs_source: str | None = None
        self._last_runtime_inputs_missing: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        """Return whether live video can be requested from Home Assistant."""
        if not self._runtime_configured:
            return False
        snapshot = self.coordinator.data
        if snapshot is None:
            return False
        return "video" in snapshot.capabilities

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
            "stream_session_active": self._session is not None,
            "last_stream_error": self._last_error,
            "last_runtime_inputs_ready": self._last_runtime_inputs_ready,
            "last_runtime_inputs_source": self._last_runtime_inputs_source,
            "last_runtime_inputs_missing": self._last_runtime_inputs_missing,
            "last_stream_session": self._session.as_dict(redact=True)
            if self._session is not None
            else None,
        }

    async def stream_source(self) -> str | None:
        """Start live video and return the local FLV source URL for HA stream."""
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
            runtime = self._create_runtime()
            session = await self.hass.async_add_executor_job(
                runtime.start_live_stream,
                inputs,
            )
        except DreameLawnMowerVideoRuntimeError as err:
            self._set_stream_error(str(err))
            return None
        except Exception as err:  # noqa: BLE001 - HA should receive a clean miss.
            _LOGGER.warning("Failed to start Dreame mower live video: %s", err)
            self._set_stream_error(str(err))
            return None

        self._runtime = runtime
        self._session = session
        self._last_error = None
        self._attr_is_streaming = True
        self.async_write_ha_state()
        return session.stream_url

    async def async_turn_off(self) -> None:
        """Stop the current live video session."""
        await self._async_stop_active_session()

    async def async_will_remove_from_hass(self) -> None:
        """Stop XP2P video when Home Assistant unloads the camera."""
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
        try:
            await self.hass.async_add_executor_job(runtime.stop_live_stream, session)
        except Exception as err:  # noqa: BLE001 - cleanup should not break unload.
            _LOGGER.debug("Failed to stop Dreame mower live video: %s", err)
        finally:
            self.async_write_ha_state()

    def _create_runtime(self) -> _DreameVideoRuntime:
        """Create the configured runtime adapter."""
        if runner_command := self._runner_command:
            command = _split_runner_command(runner_command)
            if self._runtime_mode == XP2P_RUNNER_MODE_ONE_SHOT:
                return DreameLawnMowerXp2pExternalRunner(command)
            return DreameLawnMowerXp2pProcessRunner(command)

        if library_path := self._native_library_path:
            return DreameLawnMowerNativeXp2pRuntime(Path(library_path))

        raise DreameLawnMowerVideoRuntimeError(
            "Configure a native XP2P library path or XP2P runner command."
        )

    @property
    def _runtime_configured(self) -> bool:
        return bool(self._runner_command or self._native_library_path)

    @property
    def _runtime_mode(self) -> str:
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
