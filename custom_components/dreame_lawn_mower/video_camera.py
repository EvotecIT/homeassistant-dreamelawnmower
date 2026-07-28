"""Live video camera entity for Dreame lawn mower."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any

from homeassistant.components.camera import (
    DATA_CAMERA_PREFS,
    Camera,
    CameraEntityFeature,
)
from homeassistant.components.stream import create_stream
from homeassistant.components.stream.const import (
    ATTR_STREAMS,
)
from homeassistant.components.stream.const import (
    DOMAIN as STREAM_DOMAIN,
)
from homeassistant.config_entries import ConfigEntry
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
from .debug import (
    sanitize_debug_data as sanitize_debug_data,
)
from .debug import sanitize_diagnostic_text
from .diagnostic_events import record_diagnostic_event
from .dreame_lawn_mower_client.models import (
    DreameLawnMowerCameraStreamRuntimeInputs,
    camera_stream_block_reason,
    snapshot_advertises_video,
)
from .dreame_lawn_mower_client.stream_health import (
    DreameLawnMowerStreamUrlProbeResult as DreameLawnMowerStreamUrlProbeResult,
)
from .dreame_lawn_mower_client.video_provisioning_status import (
    XP2P_PROVISIONING_DEVICE_TRIPLE_MISSING as XP2P_PROVISIONING_DEVICE_TRIPLE_MISSING,
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
from .video_cached_xp2p import async_start_cached_xp2p as async_start_cached_xp2p
from .video_camera_startup import (
    DreameLawnMowerVideoStartupMixin,
)
from .video_camera_startup import (
    _runtime_inputs_not_ready_message as _runtime_inputs_not_ready_message,
)
from .video_camera_state import DreameLawnMowerVideoStateMixin
from .video_camera_types import _DreameVideoRuntime as _DreameVideoRuntime
from .video_flv_relay import DreameLawnMowerFlvRelay
from .video_lan_cache import DreameLawnMowerVideoLanCache
from .video_provisioning_cache import DreameLawnMowerVideoProvisioningCache
from .video_session_lifecycle import DreameLawnMowerHaStreamIdleMonitor

_LOGGER = logging.getLogger(__name__)
_VIDEO_UPSTREAM_START_TIMEOUT = DEFAULT_XP2P_HOST_STARTUP_TIMEOUT
_SNAPSHOT_STREAM_START_TIMEOUT = 15.0
_SNAPSHOT_IMAGE_TIMEOUT = 15.0
_HA_STREAM_STOP_TIMEOUT = 10.0
_RUNTIME_SESSION_STOP_TIMEOUT = 20.0
_CAMERA_STREAM_DISABLE_TIMEOUT = 10.0
_PENDING_RUNTIME_STOPS: dict[str, set[asyncio.Future[Any]]] = {}


class DreameLawnMowerVideoCamera(
    DreameLawnMowerVideoStartupMixin,
    DreameLawnMowerVideoStateMixin,
    CoordinatorEntity[DreameLawnMowerCoordinator],
    Camera,
):
    """Live stream camera backed by a configured XP2P runtime."""

    _attr_has_entity_name = True
    _attr_name = "Live Video"
    _attr_icon = "mdi:video-wireless-outline"
    _attr_entity_registry_enabled_default = True
    _attr_supported_features = CameraEntityFeature.STREAM | CameraEntityFeature.ON_OFF

    # Preserve the historical method surface while focused mixins own the
    # implementations. Reading from ``__dict__`` retains property and
    # staticmethod descriptors as well as normal methods.
    _handle_coordinator_update = DreameLawnMowerVideoStateMixin.__dict__[
        "_handle_coordinator_update"
    ]
    _async_cleanup_for_state_gate = DreameLawnMowerVideoStateMixin.__dict__[
        "_async_cleanup_for_state_gate"
    ]
    available = DreameLawnMowerVideoStateMixin.__dict__["available"]
    device_info = DreameLawnMowerVideoStateMixin.__dict__["device_info"]
    extra_state_attributes = DreameLawnMowerVideoStateMixin.__dict__[
        "extra_state_attributes"
    ]
    _async_start_stream = DreameLawnMowerVideoStartupMixin.__dict__[
        "_async_start_stream"
    ]
    _async_refresh_auto_start_state = DreameLawnMowerVideoStartupMixin.__dict__[
        "_async_refresh_auto_start_state"
    ]
    _video_start_is_blocked = DreameLawnMowerVideoStartupMixin.__dict__[
        "_video_start_is_blocked"
    ]
    _async_try_lan_stream = DreameLawnMowerVideoStartupMixin.__dict__[
        "_async_try_lan_stream"
    ]
    _async_try_cached_xp2p_stream = DreameLawnMowerVideoStartupMixin.__dict__[
        "_async_try_cached_xp2p_stream"
    ]
    _async_get_runtime_inputs = DreameLawnMowerVideoStartupMixin.__dict__[
        "_async_get_runtime_inputs"
    ]
    _async_cache_healthy_provisioning = DreameLawnMowerVideoStartupMixin.__dict__[
        "_async_cache_healthy_provisioning"
    ]
    _async_start_lan_runtime_session = DreameLawnMowerVideoStartupMixin.__dict__[
        "_async_start_lan_runtime_session"
    ]
    _adopt_stream_session = DreameLawnMowerVideoStartupMixin.__dict__[
        "_adopt_stream_session"
    ]
    _async_adopt_stream_session = DreameLawnMowerVideoStartupMixin.__dict__[
        "_async_adopt_stream_session"
    ]
    _async_cleanup_rejected_session = DreameLawnMowerVideoStartupMixin.__dict__[
        "_async_cleanup_rejected_session"
    ]
    _with_lan_failure = DreameLawnMowerVideoStartupMixin.__dict__["_with_lan_failure"]
    _async_start_runtime_session = DreameLawnMowerVideoStartupMixin.__dict__[
        "_async_start_runtime_session"
    ]
    _schedule_late_start_cleanup = DreameLawnMowerVideoStartupMixin.__dict__[
        "_schedule_late_start_cleanup"
    ]
    _async_cleanup_late_start = DreameLawnMowerVideoStartupMixin.__dict__[
        "_async_cleanup_late_start"
    ]

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
        self._snapshot_requests = 0
        self._snapshot_owned_stream: Any | None = None
        self._stream_idle_monitor = DreameLawnMowerHaStreamIdleMonitor(
            coordinator.hass,
            stream_lock=self._stream_lock,
            is_current=lambda stream, owner: (
                self.stream is stream and owner is self._flv_relay
            ),
            stop_active=self._async_stop_active_session,
            has_external_consumers=lambda: (
                self._flv_relay.direct_subscriber_count > 0
                or self._snapshot_requests > 0
            ),
        )
        self._flv_relay = self._create_flv_relay()
        self._video_start_requested_at: float | None = None
        self._video_first_media_at: float | None = None
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
        self._last_managed_runtime_diagnostics: dict[str, Any] | None = None
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
        self._bypass_cached_xp2p = False
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

    async def stream_source(self) -> str | None:
        """Return a dormant local FLV source for HA HLS or WebRTC providers.

        Capability discovery calls this method, so it must not contact or
        enable the mower.  The relay starts XP2P only when a media consumer
        performs the first HTTP GET.
        """
        if not getattr(self, "_attr_is_on", True):
            return None
        try:
            return await self._ensure_flv_relay().async_start()
        except Exception as err:  # noqa: BLE001 - expose a clean source miss.
            self._set_stream_error(
                f"Could not expose the local mower video relay: {err}",
                stage="relay_setup",
            )
            return None

    def _create_flv_relay(self) -> DreameLawnMowerFlvRelay:
        """Create the local fan-out owner without opening its listener."""
        return DreameLawnMowerFlvRelay(
            self.coordinator.hass,
            source_factory=self._async_start_relay_upstream,
            media_ready=self._async_relay_media_ready,
            failed=self._async_relay_failed,
            idle=self._async_relay_idle,
        )

    def _ensure_flv_relay(self) -> DreameLawnMowerFlvRelay:
        """Create the relay lazily for compatibility with restored entities."""
        relay = getattr(self, "_flv_relay", None)
        if relay is None:
            relay = self._create_flv_relay()
            self._flv_relay = relay
        return relay

    async def _async_start_relay_upstream(self) -> str | None:
        """Start the one mower-owned source after a real local consumer arrives."""
        self._video_start_requested_at = monotonic()
        self._video_first_media_at = None
        self.async_write_ha_state()
        try:
            async with asyncio.timeout(_VIDEO_UPSTREAM_START_TIMEOUT):
                if getattr(self, "_bypass_cached_xp2p", False):
                    return await self._async_start_raw_source(
                        skip_cached_xp2p=True
                    )
                return await self._async_start_raw_source()
        except TimeoutError:
            self._set_stream_error(
                "Mower video did not start within "
                f"{_VIDEO_UPSTREAM_START_TIMEOUT:g} seconds.",
                stage="upstream_start_timeout",
            )
            return None

    async def _async_relay_media_ready(
        self,
        relay_diagnostics: dict[str, object],
    ) -> None:
        """Commit a session only after the relay observes decodable FLV media."""
        self._video_first_media_at = monotonic()
        provisioning_inputs: DreameLawnMowerCameraStreamRuntimeInputs | None = None
        async with self._stream_lock:
            self._unverified_playback_session = None
            provisioning_inputs = self._pending_provisioning_inputs
            self._pending_provisioning_inputs = None
            if self._last_stream_health is None:
                self._last_stream_health = {}
            self._last_stream_health.update(relay_diagnostics)
            self._last_stream_health.update(
                {
                    "available": True,
                    "verification_source": "local_flv_relay",
                    "playback_session_verified": True,
                }
            )
        if provisioning_inputs is not None:
            await self._async_cache_healthy_provisioning(provisioning_inputs)
        if self._last_video_transport != "cached_xp2p":
            self._bypass_cached_xp2p = False
        self.async_write_ha_state()

    async def _async_relay_failed(self, error: str) -> None:
        """Retire a failed upstream while leaving the relay URL reusable."""
        cached_playback_failed = (
            self._last_video_transport == "cached_xp2p"
            and self._unverified_playback_session is not None
        )
        if cached_playback_failed:
            self._bypass_cached_xp2p = True
        self._set_stream_error(
            f"The local mower video relay stopped before playback completed: {error}",
            stage="relay_playback",
        )
        async with self._stream_lock:
            if self._session is not None or getattr(self, "stream", None) is not None:
                await self._async_stop_active_session(reason="relay_failure")
        if cached_playback_failed:
            try:
                await self._provisioning_cache.async_clear()
                self._provisioning_cache_error = None
            except Exception as err:  # noqa: BLE001 - bypass remains authoritative.
                self._provisioning_cache_error = sanitize_diagnostic_text(err)
                _LOGGER.warning(
                    "Failed to clear stale Dreame video provisioning cache: %s",
                    self._provisioning_cache_error,
                )

    async def _async_relay_idle(self) -> None:
        """Release mower video after the last local WebRTC/HLS viewer leaves."""
        async with self._stream_lock:
            if self._session is not None or getattr(self, "stream", None) is not None:
                await self._async_stop_active_session(reason="relay_idle")

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
            ha_stream = await self._async_create_stream_locked()
            if (
                ha_stream is not None
                and getattr(self, "_snapshot_owned_stream", None) is ha_stream
            ):
                self._snapshot_owned_stream = None
            return ha_stream

    async def _async_create_stream_locked(self) -> Any | None:
        """Create or reuse HA's HLS stream over the local fan-out relay."""
        async with self._stream_lock:
            if self.stream is not None:
                if getattr(self, "_attr_is_on", True):
                    return self.stream
                await self._async_stop_active_session()
            if reason := camera_stream_block_reason(self.coordinator.data):
                self._set_stream_error(reason, stage="mower_state_gate")
                return None

        try:
            source = await self._ensure_flv_relay().async_start_ha_stream()
        except Exception as err:  # noqa: BLE001 - expose a clean source miss.
            self._set_stream_error(
                f"Could not expose the local mower video relay: {err}",
                stage="relay_setup",
            )
            return None
        dynamic_settings = await self.hass.data[
            DATA_CAMERA_PREFS
        ].get_dynamic_stream_settings(self.entity_id)

        async with self._stream_lock:
            if not getattr(self, "_attr_is_on", True):
                return None
            if reason := camera_stream_block_reason(self.coordinator.data):
                self._set_stream_error(reason, stage="mower_state_gate")
                return None
            if self.stream is not None:
                return self.stream
            ha_stream = create_stream(
                self.hass,
                source,
                options=self.stream_options,
                dynamic_stream_settings=dynamic_settings,
                stream_label=self.entity_id,
            )
            ha_stream.set_update_callback(self.async_write_ha_state)
            self.stream = ha_stream
            self._stream_idle_monitor.schedule(ha_stream, self._flv_relay)
            return ha_stream

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
            self._snapshot_requests = getattr(self, "_snapshot_requests", 0) + 1
            try:
                return await self._async_camera_image_locked(width, height)
            finally:
                self._snapshot_requests = max(0, self._snapshot_requests - 1)

    async def _async_camera_image_locked(
        self,
        width: int | None,
        height: int | None,
    ) -> bytes | None:
        """Return one JPEG from Home Assistant's single FLV consumer."""
        if not getattr(self, "_create_stream_lock", None):
            self._create_stream_lock = asyncio.Lock()
        async with self._create_stream_lock:
            previous_stream = getattr(self, "stream", None)
            try:
                async with asyncio.timeout(_SNAPSHOT_STREAM_START_TIMEOUT):
                    ha_stream = await self._async_create_stream_locked()
            except TimeoutError:
                _LOGGER.debug(
                    "Timed out starting Dreame mower video for a still image"
                )
                return self._last_image
            except Exception as err:  # noqa: BLE001 - snapshots may be transient.
                _LOGGER.debug(
                    "Failed to start Dreame mower video for a still image: %s",
                    sanitize_diagnostic_text(err),
                )
                return self._last_image
            if ha_stream is not None and ha_stream is not previous_stream:
                self._snapshot_owned_stream = ha_stream
        if ha_stream is None:
            return self._last_image
        snapshot_only_stream = ha_stream is not previous_stream
        relay = getattr(self, "_flv_relay", None)
        relay_media_ready = bool(
            relay is not None
            and relay.diagnostics.get("relay_first_media_ready")
        )
        image_timeout = (
            _SNAPSHOT_IMAGE_TIMEOUT
            if relay_media_ready
            else _VIDEO_UPSTREAM_START_TIMEOUT + _SNAPSHOT_IMAGE_TIMEOUT
        )
        try:
            async with asyncio.timeout(image_timeout):
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
        """Stop a one-shot HA decoder without interrupting another relay viewer."""
        async with self._stream_lock:
            snapshot_owned_stream = getattr(
                self,
                "_snapshot_owned_stream",
                None,
            )
            if self.stream is not ha_stream:
                if snapshot_owned_stream is ha_stream:
                    self._snapshot_owned_stream = None
                return
            if snapshot_owned_stream is not ha_stream:
                return
            self._snapshot_owned_stream = None
            await self._stream_idle_monitor.async_cancel()
            self.stream = None
            try:
                async with asyncio.timeout(_HA_STREAM_STOP_TIMEOUT):
                    await ha_stream.stop()
            except TimeoutError:
                self._record_stream_cleanup_error(
                    "snapshot_stream_stop",
                    (
                        "Home Assistant snapshot stream cleanup timed out after "
                        f"{_HA_STREAM_STOP_TIMEOUT:g}s."
                    ),
                )
            except Exception as err:  # noqa: BLE001 - relay lifecycle remains owned.
                self._record_stream_cleanup_error("snapshot_stream_stop", err)
            finally:
                self._unregister_ha_stream(ha_stream)
            # The relay owns the upstream session. Its idle grace stops XP2P
            # only when no WebRTC or HLS consumer remains.

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
        relay = getattr(self, "_flv_relay", None)
        if relay is not None:
            await relay.async_close()
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
            relay = getattr(self, "_flv_relay", None)
            if relay is not None:
                await relay.async_stop_upstream()
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
            try:
                runtime.require_compatible_worker()
            except DreameLawnMowerVideoRuntimeError:
                self._last_managed_runtime_diagnostics = (
                    video_helpers.safe_state_attribute(runtime.last_failure)
                )
                raise
            self._prepared_runtime = runtime
            self._last_native_runtime_diagnostics = None
            self._last_managed_runtime_diagnostics = None
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
        changed = safe_error != getattr(self, "_last_error", None) or stage != getattr(
            self, "_last_error_stage", None
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
