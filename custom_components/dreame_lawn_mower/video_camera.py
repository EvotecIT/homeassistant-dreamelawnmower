"""Live video camera entity for Dreame lawn mower."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urljoin

from homeassistant.components.camera import (
    DATA_CAMERA_PREFS,
    Camera,
    CameraEntityFeature,
)
from homeassistant.components.stream import HLS_PROVIDER, create_stream
from homeassistant.components.stream.const import (
    ATTR_STREAMS,
)
from homeassistant.components.stream.const import (
    DOMAIN as STREAM_DOMAIN,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.network import get_url
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import video_stream_helpers as video_helpers
from .const import (
    CONF_XP2P_LIBRARY_PATH,
    CONF_XP2P_RUNNER_COMMAND,
    CONF_XP2P_RUNNER_MODE,
    DOMAIN,
    VIDEO_TRANSPORT_AUTO,
    VIDEO_TRANSPORT_CLOUD,
    VIDEO_TRANSPORT_LAN,
    XP2P_RUNNER_MODE_ONE_SHOT,
    XP2P_RUNNER_MODE_PROCESS,
)
from .coordinator import DreameLawnMowerCoordinator
from .debug import sanitize_debug_data, sanitize_diagnostic_text
from .diagnostic_events import record_diagnostic_event
from .dreame_lawn_mower_client.models import (
    DreameLawnMowerCameraStreamRuntimeInputs,
    camera_stream_block_reason,
    snapshot_advertises_video,
)
from .dreame_lawn_mower_client.stream_health import DreameLawnMowerStreamUrlProbeResult
from .dreame_lawn_mower_client.video_provisioning_status import (
    XP2P_PROVISIONING_DEVICE_TRIPLE_MISSING,
)
from .dreame_lawn_mower_client.video_runtime import (
    DreameLawnMowerNativeXp2pRuntime,
    DreameLawnMowerVideoRuntimeError,
    DreameLawnMowerXp2pExternalRunner,
    DreameLawnMowerXp2pLiveStreamSession,
    DreameLawnMowerXp2pProcessRunner,
    diagnose_native_xp2p_runtime,
)
from .dreame_lawn_mower_client.xp2p_config import DreameLawnMowerXp2pDeviceConfig
from .dreame_lawn_mower_client.xp2p_host_runtime import (
    DEFAULT_XP2P_HOST_STARTUP_TIMEOUT,
    DreameLawnMowerXp2pHostRuntime,
)
from .dreame_lawn_mower_client.xp2p_runtime_bootstrap import (
    ensure_xp2p_host_runtime,
)
from .video_cached_xp2p import async_start_cached_xp2p
from .video_lan_cache import DreameLawnMowerVideoLanCache
from .video_provisioning_cache import DreameLawnMowerVideoProvisioningCache
from .video_session_lifecycle import DreameLawnMowerHaStreamIdleMonitor

_LOGGER = logging.getLogger(__name__)
_HA_STREAM_START_TIMEOUT = DEFAULT_XP2P_HOST_STARTUP_TIMEOUT + 30.0
_HA_PLAYBACK_VERIFY_TIMEOUT = 15.0
_SNAPSHOT_STREAM_START_TIMEOUT = 15.0
_SNAPSHOT_IMAGE_TIMEOUT = 15.0
_HA_STREAM_STOP_TIMEOUT = 10.0
_RUNTIME_SESSION_STOP_TIMEOUT = 20.0
_CAMERA_STREAM_DISABLE_TIMEOUT = 10.0
_PENDING_RUNTIME_STOPS: dict[str, set[asyncio.Future[Any]]] = {}


def _runtime_inputs_not_ready_message(
    inputs: DreameLawnMowerCameraStreamRuntimeInputs,
) -> str:
    """Return a useful, credential-free explanation for missing XP2P inputs."""
    if (
        inputs.provisioning_issue
        == XP2P_PROVISIONING_DEVICE_TRIPLE_MISSING
    ):
        return (
            "Dreame cloud has not provisioned an XP2P video identity for this "
            "mower on the current account/region. Confirm that live video works "
            "in Dreamehome or MOVAhome; contact Dreame support if it is also "
            "missing there."
        )
    return (
        "Dreame cloud did not return required XP2P fields: "
        + ", ".join(inputs.missing_required)
    )


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
    _attr_supported_features = CameraEntityFeature.STREAM | CameraEntityFeature.ON_OFF

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
        self.content_type = "image/jpeg"
        self._runtime: _DreameVideoRuntime | None = None
        self._prepared_runtime: _DreameVideoRuntime | None = None
        self._runtime_prepare_task: asyncio.Task[None] | None = None
        self._state_gate_cleanup_task: asyncio.Task[None] | None = None
        self._session: DreameLawnMowerXp2pLiveStreamSession | None = None
        self._unverified_playback_session: (
            DreameLawnMowerXp2pLiveStreamSession | None
        ) = None
        self._pending_provisioning_inputs: (
            DreameLawnMowerCameraStreamRuntimeInputs | None
        ) = None
        self._stream_lock = asyncio.Lock()
        self._snapshot_lock = asyncio.Lock()
        self._stream_idle_monitor = DreameLawnMowerHaStreamIdleMonitor(
            coordinator.hass,
            stream_lock=self._stream_lock,
            is_current=lambda stream, session: (
                self.stream is stream and self._session is session
            ),
            stop_active=self._async_stop_active_session,
        )
        self._last_error: str | None = None
        self._last_error_at: str | None = None
        self._last_error_code: str | None = None
        self._last_error_stage: str | None = None
        self._last_runtime_inputs_ready: bool | None = None
        self._last_runtime_inputs_source: str | None = None
        self._last_runtime_inputs_missing: tuple[str, ...] = ()
        self._last_runtime_inputs_provisioning_issue: str | None = None
        self._last_runtime_input_diagnostics: dict[str, Any] | None = None
        self._last_stream_health: dict[str, Any] | None = None
        self._last_stream_enable_result: Any | None = None
        self._last_stream_disable_error: str | None = None
        self._last_stream_cleanup_reason: str | None = None
        self._last_stream_cleanup_error: str | None = None
        self._last_stream_cleanup_error_stage: str | None = None
        self._last_stream_cleanup_at: str | None = None
        self._last_native_runtime_diagnostics: dict[str, Any] | None = None
        self._runtime_preparation_error: str | None = None
        self._last_image: bytes | None = None
        self._lan_cache = getattr(coordinator, "video_lan_cache", None)
        if self._lan_cache is None:
            self._lan_cache = DreameLawnMowerVideoLanCache(
                coordinator.hass,
                entry_id=entry.entry_id,
                did=self._descriptor.did,
            )
        self._lan_cache_error: str | None = None
        self._last_lan_error: str | None = None
        self._provisioning_cache = getattr(
            coordinator,
            "video_provisioning_cache",
            None,
        )
        if self._provisioning_cache is None:
            self._provisioning_cache = DreameLawnMowerVideoProvisioningCache(
                coordinator.hass,
                entry_id=entry.entry_id,
                did=self._descriptor.did,
            )
        self._provisioning_cache_error: str | None = None
        self._last_cached_xp2p_error: str | None = None
        self._last_video_transport: str | None = None
        self._last_video_transport_attempted: str | None = None
        self._video_capability_observed = snapshot_advertises_video(coordinator.data)

    async def async_added_to_hass(self) -> None:
        """Schedule managed runtime preparation without blocking entity setup."""
        await super().async_added_to_hass()
        if not self._lan_cache.loaded:
            try:
                await self._lan_cache.async_load()
            except Exception as err:  # noqa: BLE001 - Auto/cloud remain available.
                self._lan_cache_error = sanitize_diagnostic_text(err)
                _LOGGER.warning(
                    "Failed to load Dreame LAN video cache: %s", self._lan_cache_error
                )
        if not self._provisioning_cache.loaded:
            try:
                await self._provisioning_cache.async_load()
            except Exception as err:  # noqa: BLE001 - cloud remains available.
                self._provisioning_cache_error = sanitize_diagnostic_text(err)
                _LOGGER.warning(
                    "Failed to load Dreame video provisioning cache: %s",
                    self._provisioning_cache_error,
                )
        if self._persisted_video_capability:
            self._video_capability_observed = True
        snapshot = self.coordinator.data
        if not self._runtime_configured or (
            self._lan_cache.inputs is None
            and self._provisioning_cache.inputs is None
            and (snapshot is None or not snapshot_advertises_video(snapshot))
        ):
            return
        self._runtime_prepare_task = self.hass.async_create_task(
            self._async_prepare_runtime()
        )

    async def _async_prepare_runtime(self) -> None:
        """Prepare a configured runtime in the background before first playback."""
        try:
            await self.hass.async_add_executor_job(self._create_runtime)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 - retry remains available on play.
            self._runtime_preparation_error = sanitize_diagnostic_text(err)
            _LOGGER.warning(
                "Failed to prepare Dreame mower live video: %s",
                self._runtime_preparation_error,
            )
        else:
            self._runtime_preparation_error = None

    async def _async_get_runtime(self) -> _DreameVideoRuntime:
        """Reuse in-flight background preparation before starting a stream."""
        prepare_task = self._runtime_prepare_task
        if prepare_task is not None and not prepare_task.done():
            await prepare_task
        self._runtime_prepare_task = None
        if self._prepared_runtime is not None:
            return self._prepared_runtime
        return await self.hass.async_add_executor_job(self._create_runtime)

    def _handle_coordinator_update(self) -> None:
        """Remember video support and retire sessions when state blocks video."""
        snapshot = self.coordinator.data
        if snapshot_advertises_video(snapshot):
            self._video_capability_observed = True

        block_reason = camera_stream_block_reason(snapshot)
        cleanup_task = self._state_gate_cleanup_task
        target_session = self._session
        target_stream = getattr(self, "stream", None)
        if (
            block_reason is not None
            and (target_session is not None or target_stream is not None)
            and (cleanup_task is None or cleanup_task.done())
        ):
            self._state_gate_cleanup_task = self.hass.async_create_task(
                self._async_cleanup_for_state_gate(
                    block_reason,
                    target_session,
                    target_stream,
                )
            )

        super()._handle_coordinator_update()

    async def _async_cleanup_for_state_gate(
        self,
        block_reason: str,
        target_session: DreameLawnMowerXp2pLiveStreamSession | None,
        target_stream: Any | None,
    ) -> None:
        """Stop an active session after the mower enters a blocked state."""
        async with self._stream_lock:
            if (
                self._session is not target_session
                or getattr(self, "stream", None) is not target_stream
            ):
                return
            await self._async_stop_active_session(
                reason="state_gate",
                trigger=block_reason,
            )

    @property
    def available(self) -> bool:
        """Return whether live video can be requested from Home Assistant."""
        if not self._runtime_configured:
            return False
        snapshot = self.coordinator.data
        if camera_stream_block_reason(snapshot) is not None:
            return False
        if snapshot is not None and not getattr(snapshot, "available", True):
            return False
        cached_lan_ready = (
            self._lan_cache.inputs is not None and self._lan_cache.endpoint is not None
        )
        cached_xp2p_ready = (
            self._provisioning_cache.inputs is not None
            and self._provisioning_cache.device_config is not None
        )
        if self._video_transport == VIDEO_TRANSPORT_AUTO and (
            cached_lan_ready or cached_xp2p_ready
        ):
            return True
        if not super().available:
            return False
        if snapshot is None:
            return False
        return self._video_capability_observed

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
            "video_transport_policy": self._video_transport,
            "video_block_reason": camera_stream_block_reason(self.coordinator.data),
            "video_capability_advertised": snapshot_advertises_video(
                self.coordinator.data
            ),
            "video_capability_observed": self._video_capability_observed,
            "last_video_transport": self._last_video_transport,
            "last_video_transport_attempted": self._last_video_transport_attempted,
            "lan_video_identity_cached": self._lan_cache.inputs is not None,
            "lan_video_endpoint_cached": self._lan_cache.endpoint is not None,
            "lan_video_cache_error": self._lan_cache_error,
            "last_lan_video_error": self._last_lan_error,
            "xp2p_provisioning_cached": (
                self._provisioning_cache.inputs is not None
                and self._provisioning_cache.device_config is not None
            ),
            "xp2p_provisioning_cache_error": self._provisioning_cache_error,
            "last_cached_xp2p_error": self._last_cached_xp2p_error,
            "xp2p_library_configured": bool(self._native_library_path),
            "xp2p_runner_configured": bool(self._runner_command),
            "managed_xp2p_runtime_supported": video_helpers.managed_runtime_supported(),
            "video_runtime_preparation_error": self._runtime_preparation_error,
            "stream_session_active": self._session is not None,
            "last_stream_error": self._last_error,
            "last_stream_error_at": getattr(self, "_last_error_at", None),
            "last_stream_error_code": getattr(self, "_last_error_code", None),
            "last_stream_error_stage": getattr(self, "_last_error_stage", None),
            "last_runtime_inputs_ready": self._last_runtime_inputs_ready,
            "last_runtime_inputs_source": self._last_runtime_inputs_source,
            "last_runtime_inputs_missing": self._last_runtime_inputs_missing,
            "last_runtime_inputs_provisioning_issue": (
                self._last_runtime_inputs_provisioning_issue
            ),
            "last_runtime_input_diagnostics": getattr(
                self,
                "_last_runtime_input_diagnostics",
                None,
            ),
            "last_stream_enable_result": self._last_stream_enable_result,
            "last_stream_disable_error": self._last_stream_disable_error,
            "last_stream_cleanup_reason": self._last_stream_cleanup_reason,
            "last_stream_cleanup_error": self._last_stream_cleanup_error,
            "last_stream_cleanup_error_stage": self._last_stream_cleanup_error_stage,
            "last_stream_cleanup_at": self._last_stream_cleanup_at,
            "stream_cleanup_pending": (
                self._state_gate_cleanup_task is not None
                and not self._state_gate_cleanup_task.done()
            ),
            "runtime_cleanup_pending": self._runtime_cleanup_pending,
            "last_native_runtime_diagnostics": self._last_native_runtime_diagnostics,
            "last_stream_health": self._last_stream_health,
            "last_stream_session": self._session.as_dict(redact=True)
            if self._session is not None
            else None,
        }

    async def stream_source(self) -> str | None:
        """Return HA's verified HLS proxy instead of the single-consumer FLV URL."""
        if not getattr(self, "_create_stream_lock", None):
            self._create_stream_lock = asyncio.Lock()
        async with self._create_stream_lock:
            previous_stream = getattr(self, "stream", None)
            ha_stream = await self._async_create_stream_locked()
            if ha_stream is None:
                return None
            owns_stream = ha_stream is not previous_stream
            try:
                ha_stream.add_provider(HLS_PROVIDER)
                endpoint = ha_stream.endpoint_url(HLS_PROVIDER)
                return urljoin(f"{get_url(self.hass)}/", endpoint)
            except Exception as err:  # noqa: BLE001 - expose a clean source miss.
                self._set_stream_error(
                    f"Home Assistant could not expose the verified video stream: {err}",
                    stage="ha_proxy",
                )
                if owns_stream:
                    await self._async_stop_owned_stream(ha_stream)
                return None

    async def _async_start_raw_source(
        self,
        *,
        skip_cached_xp2p: bool = False,
    ) -> str | None:
        """Start live video and return its private single-consumer FLV URL."""
        async with self._stream_lock:
            if not getattr(self, "_attr_is_on", True):
                self._set_stream_error("Dreame mower live video is turned off.")
                return None
            if self._session is not None:
                if self._session_is_usable(self._session):
                    return self._session.stream_url
                await self._async_stop_active_session()
            if skip_cached_xp2p:
                return await self._async_start_stream(skip_cached_xp2p=True)
            return await self._async_start_stream()

    async def async_create_stream(self) -> Any | None:
        """Create HA's stream with enough time for native XP2P startup."""
        if not getattr(self, "_create_stream_lock", None):
            self._create_stream_lock = asyncio.Lock()
        async with self._create_stream_lock:
            return await self._async_create_stream_locked()

    async def _async_create_stream_locked(self) -> Any | None:
        """Create or reuse one HA stream while the creation lock is held."""
        async with self._stream_lock:
            if self.stream is not None:
                if getattr(self, "_attr_is_on", True) and self._session_is_usable(
                    self._session
                ):
                    return self.stream
                await self._async_stop_active_session()

        ha_stream, attempted_transport = await self._async_create_stream_attempt_locked(
            skip_cached_xp2p=False
        )
        if ha_stream is not None:
            return ha_stream
        if (
            attempted_transport == "cached_xp2p"
            and self._video_transport == VIDEO_TRANSPORT_AUTO
        ):
            ha_stream, _ = await self._async_create_stream_attempt_locked(
                skip_cached_xp2p=True
            )
        return ha_stream

    async def _async_create_stream_attempt_locked(
        self,
        *,
        skip_cached_xp2p: bool,
    ) -> tuple[Any | None, str | None]:
        """Create and verify one HA stream attempt while creation is serialized."""
        previous_session = self._session
        session: DreameLawnMowerXp2pLiveStreamSession | None = None
        owns_session = False
        try:
            async with asyncio.timeout(_HA_STREAM_START_TIMEOUT):
                source = (
                    await self._async_start_raw_source(skip_cached_xp2p=True)
                    if skip_cached_xp2p
                    else await self._async_start_raw_source()
                )
            if not source:
                return None, None
            session = self._session
            attempted_transport = self._last_video_transport
            owns_session = session is not None and session is not previous_session
            dynamic_settings = await self.hass.data[
                DATA_CAMERA_PREFS
            ].get_dynamic_stream_settings(self.entity_id)

            async with self._stream_lock:
                if (
                    not getattr(self, "_attr_is_on", True)
                    or self._session is not session
                    or not self._session_is_usable(session)
                    or camera_stream_block_reason(self.coordinator.data) is not None
                ):
                    if self._session is session:
                        await self._async_stop_active_session(
                            reason="state_gate",
                            trigger=camera_stream_block_reason(self.coordinator.data),
                        )
                    return None, attempted_transport
                ha_stream = create_stream(
                    self.hass,
                    source,
                    options=self.stream_options,
                    dynamic_stream_settings=dynamic_settings,
                    stream_label=self.entity_id,
                )
                ha_stream.set_update_callback(self.async_write_ha_state)
                self.stream = ha_stream
                self._stream_idle_monitor.schedule(ha_stream, session)

            if getattr(self, "_unverified_playback_session", None) is session:
                if not await self._async_verify_playback_stream(ha_stream, session):
                    return None, attempted_transport
            return ha_stream, attempted_transport
        except BaseException:
            if owns_session and session is not None:
                await self._async_cleanup_failed_stream_setup(session)
            raise

    async def _async_verify_playback_stream(
        self,
        ha_stream: Any,
        session: DreameLawnMowerXp2pLiveStreamSession,
    ) -> bool:
        """Verify the adopted session through HA's sole stream consumer."""
        try:
            async with asyncio.timeout(_HA_PLAYBACK_VERIFY_TIMEOUT):
                image = await ha_stream.async_get_image(wait_for_next_keyframe=True)
            if image is None:
                raise DreameLawnMowerVideoRuntimeError(
                    "Home Assistant did not decode a frame from the playback session."
                )
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 - expose a clean camera miss.
            await self._async_cleanup_failed_stream_setup(session)
            self._set_stream_error(
                f"Qualified XP2P video could not verify its playback session: {err}",
                stage="playback_verification",
            )
            return False

        provisioning_inputs: DreameLawnMowerCameraStreamRuntimeInputs | None = None
        async with self._stream_lock:
            if self.stream is not ha_stream or self._session is not session:
                return False
            self._unverified_playback_session = None
            provisioning_inputs = self._pending_provisioning_inputs
            self._pending_provisioning_inputs = None
            if getattr(self, "_last_stream_health", None) is not None:
                self._last_stream_health["available"] = True
                self._last_stream_health["flv_header_present"] = True
                self._last_stream_health["playback_session_verified"] = True
            self._last_image = image
        if provisioning_inputs is not None:
            await self._async_cache_healthy_provisioning(provisioning_inputs)
        return True

    async def _async_cleanup_failed_stream_setup(
        self,
        session: DreameLawnMowerXp2pLiveStreamSession,
    ) -> None:
        """Stop a session whose HA stream setup or verification failed."""
        async with self._stream_lock:
            if self._session is session:
                await self._async_stop_active_session()

    @staticmethod
    def _session_is_usable(
        session: DreameLawnMowerXp2pLiveStreamSession | None,
    ) -> bool:
        """Return whether a session still has a live owned worker when applicable."""
        if session is None:
            return False
        process = getattr(session, "runner_process", None)
        return process is None or process.poll() is None

    async def async_camera_image(
        self,
        width: int | None = None,
        height: int | None = None,
    ) -> bytes | None:
        """Return a real JPEG frame decoded from the managed local FLV source."""
        if not getattr(self, "_attr_is_on", True):
            return None
        async with self._snapshot_lock:
            if not getattr(self, "_create_stream_lock", None):
                self._create_stream_lock = asyncio.Lock()
            async with self._create_stream_lock:
                return await self._async_camera_image_locked(width, height)

    async def _async_camera_image_locked(
        self,
        width: int | None,
        height: int | None,
    ) -> bytes | None:
        """Return one JPEG from Home Assistant's single FLV consumer."""
        previous_stream = getattr(self, "stream", None)
        try:
            async with asyncio.timeout(_SNAPSHOT_STREAM_START_TIMEOUT):
                ha_stream = await self._async_create_stream_locked()
        except TimeoutError:
            _LOGGER.debug("Timed out starting Dreame mower video for a still image")
            return self._last_image
        except Exception as err:  # noqa: BLE001 - snapshots may be transient.
            _LOGGER.debug(
                "Failed to start Dreame mower video for a still image: %s",
                sanitize_diagnostic_text(err),
            )
            return self._last_image
        if ha_stream is None:
            return self._last_image
        snapshot_only_stream = ha_stream is not previous_stream
        try:
            async with asyncio.timeout(_SNAPSHOT_IMAGE_TIMEOUT):
                image = await ha_stream.async_get_image(
                    width=width,
                    height=height,
                    wait_for_next_keyframe=True,
                )
        except TimeoutError:
            _LOGGER.debug("Timed out reading Dreame mower still image")
            return self._last_image
        except Exception as err:  # noqa: BLE001 - snapshots may be transient.
            _LOGGER.debug(
                "Failed to read Dreame mower still image: %s",
                sanitize_diagnostic_text(err),
            )
            return self._last_image
        finally:
            if snapshot_only_stream:
                await self._async_stop_owned_stream(ha_stream)
        if image is not None:
            self._last_image = image
        return image or self._last_image

    async def _async_stop_owned_stream(self, ha_stream: Any) -> None:
        """Stop an HA stream created only for the current one-shot operation."""
        async with self._stream_lock:
            if self.stream is not ha_stream:
                return
            # The creation lock remains held across the image read and cleanup,
            # so no live viewer can adopt this stream before it is stopped.
            await self._async_stop_active_session()

    async def _async_start_stream(
        self,
        *,
        skip_cached_xp2p: bool = False,
    ) -> str | None:
        """Start one serialized XP2P stream session."""
        self._last_stream_health = None
        self._last_lan_error = None
        self._last_cached_xp2p_error = None
        self._last_video_transport_attempted = None
        cached_xp2p_inputs = (
            self._provisioning_cache.inputs
            if self._video_transport == VIDEO_TRANSPORT_AUTO
            else None
        )
        if not self._runtime_configured:
            self._set_stream_error(
                "Configure a native XP2P library path or XP2P runner command.",
                stage="runtime_configuration",
            )
            return None
        if self._runtime_cleanup_pending:
            self._set_stream_error(
                "Previous live-video runtime cleanup is still in progress. "
                "Retry shortly.",
                stage="runtime_cleanup",
            )
            return None
        if self._video_start_is_blocked():
            return None
        if (
            self._video_transport == VIDEO_TRANSPORT_AUTO
            and not await self._async_refresh_auto_start_state()
        ):
            return None

        await self._async_stop_active_session()
        try:
            runtime = await self._async_get_runtime()
            self._runtime_preparation_error = None
        except Exception as err:  # noqa: BLE001 - HA should receive a clean miss.
            self._set_stream_error(str(err), stage="runtime_preparation")
            return None

        if (
            not skip_cached_xp2p
            and cached_xp2p_inputs is not None
            and cached_xp2p_inputs.ready
        ):
            self._last_video_transport_attempted = "cached_xp2p"
            try:
                cached_source = await self._async_try_cached_xp2p_stream(
                    runtime,
                    cached_xp2p_inputs,
                )
            except DreameLawnMowerVideoRuntimeError as err:
                self._set_stream_error(
                    self._with_lan_failure(str(err)),
                    stage="cached_xp2p_start",
                )
                return None
            if cached_source is not None:
                return cached_source
            if self._video_start_is_blocked():
                return None

        cloud_inputs: DreameLawnMowerCameraStreamRuntimeInputs | None = None
        cloud_inputs_error: Exception | None = None
        if self._video_transport != VIDEO_TRANSPORT_CLOUD:
            lan_inputs = self._lan_cache.inputs
            cached_endpoint = self._lan_cache.endpoint
            if (
                self._video_transport == VIDEO_TRANSPORT_AUTO
                and cached_xp2p_inputs is None
                and (lan_inputs is None or cached_endpoint is None)
            ):
                try:
                    cloud_inputs = await self._async_get_runtime_inputs()
                except Exception as err:  # noqa: BLE001 - LAN cache may be absent.
                    cloud_inputs_error = err
                if cloud_inputs is not None and cloud_inputs.lan_identity_ready:
                    # Tencent's LAN probe may require the short-lived access token.
                    # It is used only for discovery and is never persisted.
                    lan_inputs = cloud_inputs
                else:
                    lan_inputs = self._lan_cache.inputs

            if lan_inputs is not None:
                self._last_video_transport_attempted = VIDEO_TRANSPORT_LAN
                try:
                    lan_source = await self._async_try_lan_stream(runtime, lan_inputs)
                except DreameLawnMowerVideoRuntimeError as err:
                    self._set_stream_error(str(err), stage="lan_start")
                    return None
                if lan_source is not None:
                    return lan_source
                if self._video_start_is_blocked():
                    return None
                if (
                    self._video_transport == VIDEO_TRANSPORT_AUTO
                    and cached_endpoint is not None
                ):
                    cached_error = self._last_lan_error
                    try:
                        await self._lan_cache.async_clear_endpoint()
                        self._lan_cache_error = None
                    except Exception as err:  # noqa: BLE001 - retry in memory.
                        self._lan_cache_error = sanitize_diagnostic_text(err)
                        _LOGGER.warning(
                            "Failed to clear stale Dreame LAN endpoint: %s",
                            self._lan_cache_error,
                        )
                    try:
                        cloud_inputs = await self._async_get_runtime_inputs()
                    except Exception as err:  # noqa: BLE001 - cloud fallback reports.
                        cloud_inputs_error = err
                        cached_failure = cached_error or "unknown error"
                        self._last_lan_error = (
                            f"Cached endpoint failed: {cached_failure} "
                            f"Fresh LAN discovery inputs failed: {err}"
                        )
                    else:
                        if cloud_inputs.lan_identity_ready:
                            self._last_video_transport_attempted = VIDEO_TRANSPORT_LAN
                            try:
                                lan_source = await self._async_try_lan_stream(
                                    runtime,
                                    cloud_inputs,
                                )
                            except DreameLawnMowerVideoRuntimeError as err:
                                self._set_stream_error(str(err), stage="lan_start")
                                return None
                            if lan_source is not None:
                                return lan_source
                            if self._video_start_is_blocked():
                                return None
                            self._last_lan_error = (
                                "Cached endpoint failed: "
                                f"{cached_error or 'unknown error'} "
                                "Fresh LAN discovery failed: "
                                f"{self._last_lan_error or 'unknown error'}"
                            )
                        else:
                            self._last_lan_error = (
                                "Cached endpoint failed: "
                                f"{cached_error or 'unknown error'} "
                                "Fresh cloud inputs did not include LAN identity."
                            )
            else:
                self._last_lan_error = (
                    "No cached LAN video identity is available. Use Auto or Cloud "
                    "once while the video cloud is reachable to provision it."
                )

        if self._video_start_is_blocked():
            return None

        stream_enable_attempted = False
        session: DreameLawnMowerXp2pLiveStreamSession | None = None
        self._last_video_transport_attempted = VIDEO_TRANSPORT_CLOUD
        try:
            if cloud_inputs is None:
                if cloud_inputs_error is not None:
                    raise cloud_inputs_error
                cloud_inputs = await self._async_get_runtime_inputs()
            if not cloud_inputs.ready:
                raise DreameLawnMowerVideoRuntimeError(
                    _runtime_inputs_not_ready_message(cloud_inputs)
                )
            if self._video_start_is_blocked():
                return None
            stream_enable_attempted = True
            self._last_stream_enable_result = sanitize_debug_data(
                video_helpers.safe_state_attribute(
                    await self.coordinator.client.async_set_camera_stream_enabled(True)
                )
            )
            self._last_stream_disable_error = None
            if self._video_start_is_blocked():
                await self._async_disable_camera_stream()
                return None
            session = await self._async_start_runtime_session(runtime, cloud_inputs)
        except asyncio.CancelledError:
            if runtime is not None and session is not None:
                await self._async_stop_session(runtime, session)
            if stream_enable_attempted:
                await self._async_disable_camera_stream()
            raise
        except DreameLawnMowerVideoRuntimeError as err:
            if runtime is not None and session is not None:
                await self._async_stop_session(runtime, session)
            if stream_enable_attempted:
                await self._async_disable_camera_stream()
            self._set_stream_error(
                self._with_lan_failure(str(err)),
                stage="cloud_start",
            )
            return None
        except Exception as err:  # noqa: BLE001 - HA should receive a clean miss.
            if runtime is not None and session is not None:
                await self._async_stop_session(runtime, session)
            if stream_enable_attempted:
                await self._async_disable_camera_stream()
            safe_error = sanitize_diagnostic_text(err)
            self._set_stream_error(
                self._with_lan_failure(safe_error),
                stage="cloud_start",
            )
            return None

        return await self._async_adopt_stream_session(
            runtime,
            session,
            None,
            transport=VIDEO_TRANSPORT_CLOUD,
            provisioning_inputs=cloud_inputs,
        )

    async def _async_refresh_auto_start_state(self) -> bool:
        """Require a fresh safe mower snapshot before a cached Auto start."""
        try:
            await self.coordinator.async_refresh()
        except Exception as err:  # noqa: BLE001 - fail closed before video startup.
            _LOGGER.debug(
                "Failed to refresh mower state before Auto video: %s",
                sanitize_diagnostic_text(err),
            )
            self._set_stream_error(
                "Could not refresh mower state before starting Auto video.",
                stage="state_refresh",
            )
            return False
        if not self.coordinator.last_update_success:
            self._set_stream_error(
                "Could not refresh mower state before starting Auto video.",
                stage="state_refresh",
            )
            return False
        snapshot = self.coordinator.data
        if snapshot is None or not getattr(snapshot, "available", True):
            self._set_stream_error(
                "The mower is unavailable after refreshing video safety state.",
                stage="state_refresh",
            )
            return False
        if reason := camera_stream_block_reason(snapshot):
            self._set_stream_error(reason, stage="mower_state_gate")
            return False
        return True

    def _video_start_is_blocked(self) -> bool:
        """Fail a pending start when the latest mower state blocks video."""
        if reason := camera_stream_block_reason(self.coordinator.data):
            self._set_stream_error(reason, stage="mower_state_gate")
            return True
        return False

    async def _async_try_lan_stream(
        self,
        runtime: _DreameVideoRuntime,
        inputs: DreameLawnMowerCameraStreamRuntimeInputs,
    ) -> str | None:
        """Try direct-LAN startup without invoking a cloud video action."""
        session: DreameLawnMowerXp2pLiveStreamSession | None = None
        try:
            session = await self._async_start_lan_runtime_session(runtime, inputs)
            stream_health = await self.hass.async_add_executor_job(
                video_helpers.probe_stream_health_and_route,
                runtime,
                session,
            )
        except asyncio.CancelledError:
            if session is not None:
                await self._async_stop_session(runtime, session)
            raise
        except Exception as err:  # noqa: BLE001 - Auto may fall back to cloud.
            if session is not None:
                await self._async_stop_session(runtime, session)
            self._last_lan_error = sanitize_diagnostic_text(err)
            return None
        if not stream_health.flv_header_present:
            await self._async_stop_session(runtime, session)
            self._last_lan_error = video_helpers.stream_health_error(stream_health)
            return None
        try:
            await self._lan_cache.async_save_session(session)
            self._lan_cache_error = None
        except Exception as err:  # noqa: BLE001 - a live stream remains usable.
            self._lan_cache_error = sanitize_diagnostic_text(err)
            _LOGGER.warning(
                "Failed to save Dreame LAN video endpoint: %s",
                self._lan_cache_error,
            )
        if not await self._async_stop_session_for_handoff(runtime, session):
            self._last_lan_error = (
                "Qualified same-LAN probe session could not stop before "
                "playback handoff."
            )
            raise DreameLawnMowerVideoRuntimeError(self._last_lan_error)
        session = None
        try:
            session = await self._async_start_lan_runtime_session(runtime, inputs)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 - Auto may fall back to cloud.
            self._last_lan_error = (
                "Qualified same-LAN video could not open a playback session: "
                f"{sanitize_diagnostic_text(err)}"
            )
            return None
        return await self._async_adopt_stream_session(
            runtime,
            session,
            stream_health,
            transport=VIDEO_TRANSPORT_LAN,
        )

    async def _async_try_cached_xp2p_stream(
        self,
        runtime: _DreameVideoRuntime,
        inputs: DreameLawnMowerCameraStreamRuntimeInputs,
    ) -> str | None:
        """Try cached normal XP2P without Dreame video-cloud calls."""
        result = await async_start_cached_xp2p(
            runtime,
            inputs,
            start_session=self._async_start_runtime_session,
        )
        if result.session is None:
            self._last_cached_xp2p_error = (
                sanitize_diagnostic_text(result.error)
                if result.error is not None
                else None
            )
            return None
        return await self._async_adopt_stream_session(
            runtime,
            result.session,
            None,
            transport="cached_xp2p",
        )

    async def _async_get_runtime_inputs(
        self,
    ) -> DreameLawnMowerCameraStreamRuntimeInputs:
        """Fetch cloud inputs and stage configuration for health-checked use."""
        try:
            inputs = (
                await self.coordinator.client.async_get_camera_stream_runtime_inputs()
            )
        except Exception:
            self._last_runtime_input_diagnostics = sanitize_debug_data(
                getattr(
                    self.coordinator.client,
                    "last_camera_stream_diagnostics",
                    {},
                )
            )
            raise
        self._last_runtime_input_diagnostics = sanitize_debug_data(
            getattr(inputs, "diagnostics", {})
        )
        self._last_runtime_inputs_ready = inputs.ready
        self._last_runtime_inputs_source = inputs.source
        self._last_runtime_inputs_missing = inputs.missing_required
        self._last_runtime_inputs_provisioning_issue = inputs.provisioning_issue
        if inputs.lan_identity_ready:
            try:
                await self._lan_cache.async_save_identity(inputs)
                self._lan_cache_error = None
            except Exception as err:  # noqa: BLE001 - cloud transport still works.
                self._lan_cache_error = sanitize_diagnostic_text(err)
                _LOGGER.warning(
                    "Failed to save Dreame LAN video identity: %s",
                    self._lan_cache_error,
                )
        if inputs.ready:
            await self.hass.async_add_executor_job(
                self._provisioning_cache.stage_fresh_device_config,
                inputs,
            )
        return inputs

    async def _async_cache_healthy_provisioning(
        self,
        inputs: DreameLawnMowerCameraStreamRuntimeInputs,
    ) -> None:
        """Persist fresh XP2P inputs only after FLV health verification."""
        config = self._provisioning_cache.resolve_device_config(inputs)
        if config is None:
            self._provisioning_cache_error = (
                "Healthy XP2P stream did not retain its resolved device configuration."
            )
            return
        try:
            await self._provisioning_cache.async_save(inputs, config)
            self._provisioning_cache_error = None
        except Exception as err:  # noqa: BLE001 - current stream can continue.
            self._provisioning_cache_error = sanitize_diagnostic_text(err)
            _LOGGER.warning(
                "Failed to save Dreame video provisioning cache: %s",
                self._provisioning_cache_error,
            )

    async def _async_start_lan_runtime_session(
        self,
        runtime: _DreameVideoRuntime,
        inputs: DreameLawnMowerCameraStreamRuntimeInputs,
    ) -> DreameLawnMowerXp2pLiveStreamSession:
        """Start LAN mode and clean up a late native result after cancellation."""
        start_lan = getattr(runtime, "start_lan_stream", None)
        if not callable(start_lan):
            raise DreameLawnMowerVideoRuntimeError(
                "The configured advanced XP2P runtime does not support same-LAN video."
            )
        cached_endpoint = self._lan_cache.endpoint

        def _start() -> DreameLawnMowerXp2pLiveStreamSession:
            if cached_endpoint is None:
                return start_lan(inputs)
            try:
                return start_lan(inputs, endpoint=cached_endpoint)
            except DreameLawnMowerVideoRuntimeError:
                return start_lan(
                    inputs,
                    preferred_address=cached_endpoint.address,
                )

        start_job = self.hass.async_add_executor_job(_start)
        try:
            session = await asyncio.shield(start_job)
            session.provisioning_source = inputs.source
            session.camera_toggle_managed = False
            return session
        except asyncio.CancelledError:
            self._schedule_late_start_cleanup(runtime, start_job)
            raise

    def _adopt_stream_session(
        self,
        runtime: _DreameVideoRuntime,
        session: DreameLawnMowerXp2pLiveStreamSession,
        stream_health: DreameLawnMowerStreamUrlProbeResult | None,
        *,
        transport: str,
        provisioning_inputs: DreameLawnMowerCameraStreamRuntimeInputs | None = None,
    ) -> str:
        """Reserve one untouched session for Home Assistant's sole consumer."""
        self._runtime = runtime
        self._session = session
        self._unverified_playback_session = session
        self._pending_provisioning_inputs = provisioning_inputs
        self._last_stream_health = {
            **(stream_health.as_dict() if stream_health is not None else {}),
            "verification_source": "home_assistant",
            "playback_session_verified": False,
        }
        self._last_error = None
        self._last_error_at = None
        self._last_error_code = None
        self._last_error_stage = None
        self._last_video_transport = transport
        self._attr_is_on = True
        self._attr_is_streaming = True
        self.async_write_ha_state()
        return session.stream_url

    async def _async_adopt_stream_session(
        self,
        runtime: _DreameVideoRuntime,
        session: DreameLawnMowerXp2pLiveStreamSession,
        stream_health: DreameLawnMowerStreamUrlProbeResult | None,
        *,
        transport: str,
        provisioning_inputs: DreameLawnMowerCameraStreamRuntimeInputs | None = None,
    ) -> str | None:
        """Adopt a session only while the latest mower state still permits video."""
        if reason := camera_stream_block_reason(self.coordinator.data):
            record_diagnostic_event(
                self.coordinator,
                code="video_start_state_changed",
                source="video_camera",
                message="Live-video startup was retired after mower state changed.",
                severity="info",
                context={"trigger": reason, "transport": transport},
            )
            self._set_stream_error(reason, stage="mower_state_gate")
            cleanup_task = asyncio.create_task(
                self._async_cleanup_rejected_session(runtime, session)
            )
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                await cleanup_task
                raise
            return None
        return self._adopt_stream_session(
            runtime,
            session,
            stream_health,
            transport=transport,
            provisioning_inputs=provisioning_inputs,
        )

    async def _async_cleanup_rejected_session(
        self,
        runtime: _DreameVideoRuntime,
        session: DreameLawnMowerXp2pLiveStreamSession,
    ) -> None:
        """Finish cleanup for a state-gated session before cancellation escapes."""
        self._last_stream_cleanup_reason = "state_gate"
        self._last_stream_cleanup_error = None
        self._last_stream_cleanup_error_stage = None
        try:
            await self._async_stop_session(runtime, session)
            camera_toggle_managed = getattr(
                session,
                "camera_toggle_managed",
                getattr(session, "transport", VIDEO_TRANSPORT_CLOUD)
                != VIDEO_TRANSPORT_LAN,
            )
            if camera_toggle_managed:
                await self._async_disable_camera_stream()
        finally:
            self._last_stream_cleanup_at = datetime.now(UTC).isoformat()

    def _with_lan_failure(self, cloud_error: str) -> str:
        """Preserve Auto-mode failures without leaking runtime inputs."""
        return video_helpers.format_video_start_failures(
            cloud_error,
            lan_error=self._last_lan_error,
            cached_xp2p_error=self._last_cached_xp2p_error,
        )

    async def _async_start_runtime_session(
        self,
        runtime: _DreameVideoRuntime,
        inputs: DreameLawnMowerCameraStreamRuntimeInputs,
        *,
        camera_toggle_managed: bool = True,
    ) -> DreameLawnMowerXp2pLiveStreamSession:
        """Finish or clean up native startup if the HA request is cancelled."""
        start_job = self.hass.async_add_executor_job(
            runtime.start_live_stream,
            inputs,
        )
        try:
            session = await asyncio.shield(start_job)
            session.provisioning_source = inputs.source
            session.camera_toggle_managed = camera_toggle_managed
            return session
        except asyncio.CancelledError:
            self._schedule_late_start_cleanup(runtime, start_job)
            raise

    def _schedule_late_start_cleanup(
        self,
        runtime: _DreameVideoRuntime,
        start_job: asyncio.Future[DreameLawnMowerXp2pLiveStreamSession],
    ) -> None:
        """Clean up an uncancellable executor result without delaying HA timeout."""

        def _completed(
            future: asyncio.Future[DreameLawnMowerXp2pLiveStreamSession],
        ) -> None:
            if future.cancelled():
                return
            try:
                session = future.result()
            except Exception:  # noqa: BLE001 - native startup already failed.
                return
            self.hass.async_create_task(
                self._async_cleanup_late_start(runtime, session)
            )

        start_job.add_done_callback(_completed)

    async def _async_cleanup_late_start(
        self,
        runtime: _DreameVideoRuntime,
        session: DreameLawnMowerXp2pLiveStreamSession,
    ) -> None:
        """Stop a late result unless it would tear down a newer active service."""
        async with self._stream_lock:
            active_session = self._session
            active_service_id = getattr(active_session, "service_id", None)
            late_service_id = getattr(session, "service_id", None)
            active_process = getattr(active_session, "runner_process", None)
            late_process = getattr(session, "runner_process", None)
            late_owns_distinct_process = (
                late_process is not None and late_process is not active_process
            )
            if active_session is session or (
                active_session is not None
                and self._runtime is runtime
                and active_service_id is not None
                and active_service_id == late_service_id
                and not late_owns_distinct_process
            ):
                return
            await self._async_stop_session(runtime, session)

    async def async_turn_off(self) -> None:
        """Stop the current live video session."""
        async with self._stream_lock:
            await self._async_stop_active_session(reason="turn_off")
            self._attr_is_on = False
            self.async_write_ha_state()

    async def async_turn_on(self) -> None:
        """Allow Home Assistant to request a new live video session."""
        async with self._stream_lock:
            self._attr_is_on = True
            self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Stop XP2P video when Home Assistant unloads the camera."""
        prepare_task = self._runtime_prepare_task
        self._runtime_prepare_task = None
        if prepare_task is not None and not prepare_task.done():
            prepare_task.cancel()
            try:
                await prepare_task
            except asyncio.CancelledError:
                pass
        cleanup_task = self._state_gate_cleanup_task
        if cleanup_task is not None and cleanup_task is not asyncio.current_task():
            try:
                await cleanup_task
            except asyncio.CancelledError:
                pass
            except Exception as err:  # noqa: BLE001 - unload must still finish.
                self._record_stream_cleanup_error("state_gate", err)
        self._state_gate_cleanup_task = None
        async with self._stream_lock:
            if self._session is not None or getattr(self, "stream", None) is not None:
                await self._async_stop_active_session(reason="entity_unload")
            else:
                await self._stream_idle_monitor.async_cancel()
        await super().async_will_remove_from_hass()

    async def _async_stop_active_session(
        self,
        *,
        reason: str = "session_stop",
        trigger: str | None = None,
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
            ha_stream = getattr(self, "stream", None)
            self.stream = None
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
        finally:
            self._last_stream_cleanup_at = datetime.now(UTC).isoformat()
            self.async_write_ha_state()

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

    def _record_stream_cleanup_error(self, stage: str, error: object) -> None:
        """Retain one safe cleanup failure and add it to shared diagnostics."""
        safe_error = sanitize_diagnostic_text(error)
        self._last_stream_cleanup_error = safe_error
        self._last_stream_cleanup_error_stage = stage
        record_diagnostic_event(
            self.coordinator,
            code=f"video_{stage}_failed",
            source="video_camera",
            message=safe_error,
            context={
                "cleanup_reason": self._last_stream_cleanup_reason,
                "transport": self._video_transport,
            },
        )
        _LOGGER.warning(
            "Dreame mower live-video cleanup failed [%s]: %s",
            stage,
            safe_error,
        )

    def _create_runtime(self) -> _DreameVideoRuntime:
        """Create the configured runtime adapter."""
        if self._prepared_runtime is not None:
            return self._prepared_runtime
        if runner_command := self._runner_command:
            self._last_native_runtime_diagnostics = None
            command = video_helpers.split_runner_command(runner_command)
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
            self._last_native_runtime_diagnostics = video_helpers.safe_state_attribute(
                diagnostics.as_dict()
            )
            if not diagnostics.ready:
                raise DreameLawnMowerVideoRuntimeError(
                    diagnostics.error or "Configured XP2P native library is not ready."
                )
            runtime = DreameLawnMowerNativeXp2pRuntime(
                path,
                config_fetcher=self._resolve_xp2p_config,
            )
            self._prepared_runtime = runtime
            return runtime

        if video_helpers.managed_runtime_supported():
            runtime_root = Path(
                self.hass.config.path(
                    ".storage",
                    DOMAIN,
                    "xp2p-runtime",
                )
            )
            runtime = DreameLawnMowerXp2pHostRuntime(
                ensure_xp2p_host_runtime(runtime_root),
                config_fetcher=self._resolve_xp2p_config,
            )
            self._prepared_runtime = runtime
            self._last_native_runtime_diagnostics = None
            return runtime

        raise DreameLawnMowerVideoRuntimeError(
            "Managed XP2P video requires a Linux aarch64 or x86_64 host. "
            "Configure an advanced native XP2P library or runner override on "
            "this platform."
        )

    def _resolve_xp2p_config(
        self,
        inputs: DreameLawnMowerCameraStreamRuntimeInputs,
    ) -> DreameLawnMowerXp2pDeviceConfig:
        """Resolve once, reusing persisted config for cached startup."""
        return self._provisioning_cache.resolve_for_transport(
            inputs,
            auto=self._video_transport == VIDEO_TRANSPORT_AUTO,
        )

    @property
    def _runtime_configured(self) -> bool:
        return bool(
            self._runner_command
            or self._native_library_path
            or video_helpers.managed_runtime_supported()
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
    def _video_transport(self) -> str:
        return video_helpers.video_transport(self._entry)

    @property
    def _persisted_video_capability(self) -> bool:
        """Return whether a prior healthy session proves video support."""
        return bool(
            (
                self._lan_cache.inputs is not None
                and self._lan_cache.endpoint is not None
            )
            or (
                self._provisioning_cache.inputs is not None
                and self._provisioning_cache.device_config is not None
            )
        )

    @property
    def _native_library_path(self) -> str | None:
        return video_helpers.option_text(self._entry, CONF_XP2P_LIBRARY_PATH)

    @property
    def _runner_command(self) -> str | None:
        return video_helpers.option_text(self._entry, CONF_XP2P_RUNNER_COMMAND)

    def _set_stream_error(
        self,
        error: str,
        *,
        stage: str = "stream_start",
    ) -> None:
        safe_error = sanitize_diagnostic_text(error)
        code = f"video_{stage}_failed"
        changed = (
            safe_error != getattr(self, "_last_error", None)
            or stage != getattr(self, "_last_error_stage", None)
        )
        self._last_error = safe_error
        self._last_error_at = datetime.now(UTC).isoformat()
        self._last_error_code = code
        self._last_error_stage = stage
        self._attr_is_streaming = False
        snapshot = getattr(self.coordinator, "data", None)
        record_diagnostic_event(
            self.coordinator,
            code=code,
            source="video_camera",
            message=safe_error,
            context={
                "model": getattr(getattr(self, "_descriptor", None), "model", None),
                "firmware_version": getattr(snapshot, "firmware_version", None),
                "transport": self._video_transport,
                "runtime_mode": self._runtime_mode,
                "managed_runtime_supported": video_helpers.managed_runtime_supported(),
            },
        )
        if changed:
            _LOGGER.warning(
                "Dreame mower live video failed [%s]: %s. Reproduce once, then "
                "download integration diagnostics before reloading Home Assistant.",
                code,
                safe_error,
            )
        self.async_write_ha_state()
