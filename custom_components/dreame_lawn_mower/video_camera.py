"""Live video camera entity for Dreame lawn mower."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from time import monotonic
from typing import Any

from homeassistant.components.camera import (
    Camera,
    CameraEntityFeature,
)
from homeassistant.components.camera.const import DATA_CAMERA_PREFS
from homeassistant.components.stream import Stream, create_stream
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import video_stream_helpers as _video_helpers
from .const import (
    CONF_VIDEO_RETENTION,
    DEFAULT_VIDEO_RETENTION,
    VIDEO_RETENTION_OPTIONS,
    VIDEO_TRANSPORT_CLOUD,
    VIDEO_TRANSPORT_LAN,
)
from .coordinator import DreameLawnMowerCoordinator
from .debug import (
    sanitize_debug_data as sanitize_debug_data,
)
from .debug import sanitize_diagnostic_text
from .dreame_lawn_mower_client.feature_capabilities import (
    CAPABILITY_SUPPORTED,
    FEATURE_LIVE_VIDEO,
)
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
    DreameLawnMowerNativeXp2pRuntime as DreameLawnMowerNativeXp2pRuntime,
)
from .dreame_lawn_mower_client.video_runtime import (
    DreameLawnMowerVideoRuntimeError as DreameLawnMowerVideoRuntimeError,
)
from .dreame_lawn_mower_client.video_runtime import (
    DreameLawnMowerXp2pExternalRunner as DreameLawnMowerXp2pExternalRunner,
)
from .dreame_lawn_mower_client.video_runtime import (
    DreameLawnMowerXp2pLiveStreamSession,
)
from .dreame_lawn_mower_client.video_runtime import (
    DreameLawnMowerXp2pProcessRunner as DreameLawnMowerXp2pProcessRunner,
)
from .dreame_lawn_mower_client.video_runtime import (
    diagnose_native_xp2p_runtime as diagnose_native_xp2p_runtime,
)
from .dreame_lawn_mower_client.xp2p_host_runtime import (
    DEFAULT_XP2P_HOST_STARTUP_TIMEOUT,
)
from .dreame_lawn_mower_client.xp2p_host_runtime import (
    DreameLawnMowerXp2pHostRuntime as DreameLawnMowerXp2pHostRuntime,
)
from .dreame_lawn_mower_client.xp2p_runtime_bootstrap import (
    ensure_xp2p_host_runtime as ensure_xp2p_host_runtime,
)
from .video_cached_xp2p import async_start_cached_xp2p as async_start_cached_xp2p
from .video_camera_cleanup import _PENDING_RUNTIME_STOPS as _PENDING_RUNTIME_STOPS
from .video_camera_cleanup import ATTR_STREAMS as ATTR_STREAMS
from .video_camera_cleanup import STREAM_DOMAIN as STREAM_DOMAIN
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
from .video_session_lifecycle import (
    DreameLawnMowerHaStreamIdleMonitor,
    mower_video_relay_idle_grace,
)
from .video_snapshot import VideoSnapshotRequest

_LOGGER = logging.getLogger(__name__)
video_helpers = _video_helpers
_VIDEO_UPSTREAM_START_TIMEOUT = DEFAULT_XP2P_HOST_STARTUP_TIMEOUT
_SNAPSHOT_STREAM_START_TIMEOUT = 15.0
_SNAPSHOT_IMAGE_TIMEOUT = 15.0
_HA_STREAM_STOP_TIMEOUT = 10.0
_RUNTIME_SESSION_STOP_TIMEOUT = 20.0
_CAMERA_STREAM_DISABLE_TIMEOUT = 10.0
_VIDEO_RETRY_BASE_DELAY = 1.0
_VIDEO_RETRY_MAX_DELAY = 30.0
_VIDEO_RETRY_STABLE_RESET = 60.0


class DreameLawnMowerVideoCamera(
    DreameLawnMowerVideoStartupMixin,
    DreameLawnMowerVideoStateMixin,
):
    """Live stream camera backed by a configured XP2P runtime."""

    _attr_has_entity_name = True
    _attr_name = "Live Video"
    _attr_icon = "mdi:video-wireless-outline"
    _attr_entity_registry_enabled_default = True
    _attr_supported_features = CameraEntityFeature.STREAM | CameraEntityFeature.ON_OFF
    # Runtime diagnostics can change for every media packet. They remain visible
    # in downloaded diagnostics but must never be persisted by the recorder.
    _unrecorded_attributes = frozenset({MATCH_ALL})

    # Preserve direct reflection while retaining concrete method signatures.
    # Properties use their raw descriptors so class access cannot evaluate them.
    _handle_coordinator_update = (
        DreameLawnMowerVideoStateMixin._handle_coordinator_update
    )
    _async_cleanup_for_state_gate = (
        DreameLawnMowerVideoStateMixin._async_cleanup_for_state_gate
    )
    available = DreameLawnMowerVideoStateMixin.__dict__["available"]
    device_info = DreameLawnMowerVideoStateMixin.__dict__["device_info"]
    extra_state_attributes = DreameLawnMowerVideoStateMixin.__dict__[
        "extra_state_attributes"
    ]
    video_runtime_diagnostics = DreameLawnMowerVideoStateMixin.video_runtime_diagnostics
    _async_start_stream = DreameLawnMowerVideoStartupMixin._async_start_stream
    _async_refresh_video_start_state = (
        DreameLawnMowerVideoStartupMixin._async_refresh_video_start_state
    )
    _video_start_is_blocked = DreameLawnMowerVideoStartupMixin._video_start_is_blocked
    _async_try_lan_stream = DreameLawnMowerVideoStartupMixin._async_try_lan_stream
    _async_try_cached_xp2p_stream = (
        DreameLawnMowerVideoStartupMixin._async_try_cached_xp2p_stream
    )
    _async_get_runtime_inputs = (
        DreameLawnMowerVideoStartupMixin._async_get_runtime_inputs
    )
    _async_cache_healthy_provisioning = (
        DreameLawnMowerVideoStartupMixin._async_cache_healthy_provisioning
    )
    _async_start_lan_runtime_session = (
        DreameLawnMowerVideoStartupMixin._async_start_lan_runtime_session
    )
    _adopt_stream_session = DreameLawnMowerVideoStartupMixin._adopt_stream_session
    _async_adopt_stream_session = (
        DreameLawnMowerVideoStartupMixin._async_adopt_stream_session
    )
    _async_cleanup_rejected_session = (
        DreameLawnMowerVideoStartupMixin._async_cleanup_rejected_session
    )
    _with_lan_failure = DreameLawnMowerVideoStartupMixin._with_lan_failure
    _async_start_runtime_session = (
        DreameLawnMowerVideoStartupMixin._async_start_runtime_session
    )
    _schedule_late_start_cleanup = (
        DreameLawnMowerVideoStartupMixin._schedule_late_start_cleanup
    )
    _async_cleanup_late_start = (
        DreameLawnMowerVideoStartupMixin._async_cleanup_late_start
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
        self._snapshot_owned_stream = None
        self._snapshot_request = VideoSnapshotRequest()
        self._video_retention_mode = entry.options.get(
            CONF_VIDEO_RETENTION,
            DEFAULT_VIDEO_RETENTION,
        )
        if self._video_retention_mode not in VIDEO_RETENTION_OPTIONS:
            self._video_retention_mode = DEFAULT_VIDEO_RETENTION
        self._video_live_view_seen = False
        self._stream_idle_monitor = DreameLawnMowerHaStreamIdleMonitor(
            coordinator.hass,
            stream_lock=self._stream_lock,
            is_current=lambda stream, owner: (
                self.stream is stream and owner is self._flv_relay
            ),
            stop_active=self._async_stop_idle_session,
            has_external_consumers=lambda: (
                self._flv_relay.direct_subscriber_count > 0
                or self._snapshot_requests > 0
            ),
            should_stay_warm=self._video_session_should_stay_warm,
        )
        self._flv_relay = self._create_flv_relay()
        self._video_start_requested_at: float | None = None
        self._video_first_media_at: float | None = None
        self._video_recovery_failure_count = 0
        self._video_recovery_success_count = 0
        self._video_recovery_consecutive_failures = 0
        self._video_recovery_pending = False
        self._video_retry_not_before = 0.0
        self._last_stream_recovered_at: str | None = None
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
        lan_cache = getattr(coordinator, "video_lan_cache", None)
        if lan_cache is None:
            lan_cache = DreameLawnMowerVideoLanCache(
                coordinator.hass,
                entry_id=entry.entry_id,
                did=self._descriptor.did,
            )
        self._lan_cache = lan_cache
        self._lan_cache_error: str | None = None
        self._last_lan_error: str | None = None
        provisioning_cache = getattr(
            coordinator,
            "video_provisioning_cache",
            None,
        )
        if provisioning_cache is None:
            provisioning_cache = DreameLawnMowerVideoProvisioningCache(
                coordinator.hass,
                entry_id=entry.entry_id,
                did=self._descriptor.did,
            )
        self._provisioning_cache = provisioning_cache
        self._provisioning_cache_error: str | None = None
        self._last_cached_xp2p_error: str | None = None
        self._bypass_cached_xp2p = False
        self._bypass_lan = False
        self._last_video_transport: str | None = None
        self._last_video_transport_attempted: str | None = None
        self._video_capability_advertised = snapshot_advertises_video(
            coordinator.data
        )
        self._video_capability_observed = False

    async def async_added_to_hass(self) -> None:
        """Schedule managed runtime preparation without blocking entity setup."""
        await super().async_added_to_hass()
        self.coordinator.video_diagnostics_provider = self.video_runtime_diagnostics
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
            and (
                snapshot is None
                or self._resolved_video_capability().state != CAPABILITY_SUPPORTED
            )
        ):
            return
        self._runtime_prepare_task = self.hass.async_create_task(
            self._async_prepare_runtime()
        )

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
            should_stay_warm=self._video_session_should_stay_warm,
            subscriber_started=self._video_relay_subscriber_started,
            idle_grace=mower_video_relay_idle_grace(self._video_retention_mode),
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
        retry_delay = max(0.0, self._video_retry_not_before - monotonic())
        if retry_delay:
            await asyncio.sleep(retry_delay)
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
        self._video_capability_observed = True
        record_observed = getattr(
            self.coordinator,
            "record_feature_capability_observed",
            None,
        )
        if record_observed is not None:
            record_observed(FEATURE_LIVE_VIDEO)
        self._video_first_media_at = monotonic()
        if self._video_recovery_pending:
            self._video_recovery_success_count += 1
            self._last_stream_recovered_at = datetime.now(UTC).isoformat()
        self._video_recovery_pending = False
        self._video_retry_not_before = 0.0
        provisioning_inputs: DreameLawnMowerCameraStreamRuntimeInputs | None = None
        async with self._stream_lock:
            self._unverified_playback_session = None
            timing = getattr(self, "_video_startup_timing", None)
            if timing is not None:
                timing.verified()
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
                    "recovery_pending": False,
                    "retry_after_seconds": 0.0,
                }
            )
        if provisioning_inputs is not None:
            await self._async_cache_healthy_provisioning(provisioning_inputs)
        if self._last_video_transport != "cached_xp2p":
            self._bypass_cached_xp2p = False
        if self._last_video_transport == VIDEO_TRANSPORT_LAN:
            self._bypass_lan = False
        self.async_write_ha_state()

    async def _async_relay_failed(self, error: str) -> None:
        """Retire a failed upstream while leaving the relay URL reusable."""
        now = monotonic()
        if block_reason := camera_stream_block_reason(self.coordinator.data):
            # A mower safety gate is not a transport outage. Leave the stable
            # relay endpoint available, but do not accumulate Wi-Fi recovery
            # backoff or ask consumers to remount until coordinator state says
            # the mower can safely open video again.
            self._video_recovery_pending = False
            self._video_retry_not_before = 0.0
            self._set_stream_error(block_reason, stage="mower_state_gate")
            target_session = self._session
            target_stream = getattr(self, "stream", None)
            if target_session is not None or target_stream is not None:
                async with self._stream_lock:
                    if (
                        self._session is target_session
                        and getattr(self, "stream", None) is target_stream
                    ):
                        await self._async_stop_active_session(
                            reason="state_gate",
                            trigger=block_reason,
                            failed_pump_task=asyncio.current_task(),
                        )
            self.async_write_ha_state()
            return
        stable_for = (
            now - self._video_first_media_at
            if self._video_first_media_at is not None
            else 0.0
        )
        if stable_for >= _VIDEO_RETRY_STABLE_RESET:
            self._video_recovery_consecutive_failures = 1
        else:
            self._video_recovery_consecutive_failures += 1
        retry_delay = min(
            _VIDEO_RETRY_MAX_DELAY,
            _VIDEO_RETRY_BASE_DELAY
            * (
                2
                ** min(
                    self._video_recovery_consecutive_failures - 1,
                    5,
                )
            ),
        )
        self._video_recovery_failure_count += 1
        self._video_recovery_pending = True
        self._video_retry_not_before = now + retry_delay
        if self._last_stream_health is None:
            self._last_stream_health = {}
        self._last_stream_health.update(
            {
                "available": False,
                "playback_session_verified": False,
                "recovery_pending": True,
                "retry_after_seconds": retry_delay,
            }
        )
        unverified_playback_failed = (
            getattr(self, "_unverified_playback_session", None) is not None
        )
        cached_playback_failed = (
            self._last_video_transport == "cached_xp2p"
            and unverified_playback_failed
        )
        lan_playback_failed = (
            self._last_video_transport == VIDEO_TRANSPORT_LAN
            and unverified_playback_failed
        )
        if cached_playback_failed:
            self._bypass_cached_xp2p = True
        if lan_playback_failed:
            self._bypass_lan = True
        failure_context = (
            "The mower video connection was interrupted"
            if self._video_first_media_at is not None
            else "The local mower video relay stopped before playback completed"
        )
        self._set_stream_error(f"{failure_context}: {error}", stage="relay_playback")
        async with self._stream_lock:
            if getattr(self, "stream", None) is not None:
                # Home Assistant Stream already owns discontinuity handling and
                # increasing reconnect delays. Preserve that stable local
                # stream and retire only the broken mower runtime session so
                # its worker can reopen the relay URL after spotty Wi-Fi.
                await self._async_retire_failed_runtime_session()
            elif self._session is not None:
                await self._async_stop_active_session(
                    reason="relay_failure",
                    failed_pump_task=asyncio.current_task(),
                )
            if cached_playback_failed:
                # Cached XP2P deliberately avoids the cloud camera toggle.  If
                # that warm/cached route reaches no media, explicitly clear the
                # mower's stale video mode before the reconnect bypasses cache
                # and performs a fresh cloud enable.
                await self._async_disable_camera_stream()
            await self._async_clear_failed_playback_caches(
                cached_xp2p=cached_playback_failed,
                lan=lan_playback_failed,
            )

    async def _async_retire_failed_runtime_session(self) -> None:
        """Retire one failed mower session while HA Stream stays reconnectable."""
        self._last_stream_cleanup_reason = "relay_failure"
        self._last_stream_cleanup_error = None
        self._last_stream_cleanup_error_stage = None
        runtime = self._runtime
        session = self._session
        self._runtime = None
        self._session = None
        self._unverified_playback_session = None
        self._pending_provisioning_inputs = None
        self._attr_is_streaming = False
        try:
            if runtime is None or session is None:
                return
            cleanup_task = asyncio.create_task(
                self._async_finish_failed_runtime_cleanup(runtime, session)
            )
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                await cleanup_task
                raise
        finally:
            self._last_stream_cleanup_at = datetime.now(UTC).isoformat()
            self.async_write_ha_state()

    async def _async_finish_failed_runtime_cleanup(
        self,
        runtime: _DreameVideoRuntime,
        session: DreameLawnMowerXp2pLiveStreamSession,
    ) -> None:
        """Finish bounded native cleanup without stopping the HA decoder."""
        await self._async_stop_session(runtime, session)
        camera_toggle_managed = getattr(
            session,
            "camera_toggle_managed",
            getattr(session, "transport", VIDEO_TRANSPORT_CLOUD)
            != VIDEO_TRANSPORT_LAN,
        )
        if camera_toggle_managed:
            await self._async_disable_camera_stream()

    async def _async_clear_failed_playback_caches(
        self,
        *,
        cached_xp2p: bool,
        lan: bool,
    ) -> None:
        """Clear failed routes while replacement startup remains serialized."""
        if cached_xp2p:
            try:
                await self._provisioning_cache.async_clear()
                self._provisioning_cache_error = None
            except Exception as err:  # noqa: BLE001 - bypass remains authoritative.
                self._provisioning_cache_error = sanitize_diagnostic_text(err)
                _LOGGER.warning(
                    "Failed to clear stale Dreame video provisioning cache: %s",
                    self._provisioning_cache_error,
                )
        if lan:
            try:
                await self._lan_cache.async_clear_endpoint()
                self._lan_cache_error = None
            except Exception as err:  # noqa: BLE001 - bypass remains authoritative.
                self._lan_cache_error = sanitize_diagnostic_text(err)
                _LOGGER.warning(
                    "Failed to clear stale Dreame LAN video endpoint: %s",
                    self._lan_cache_error,
                )

    async def _async_relay_idle(self) -> None:
        """Release mower video after the last local WebRTC/HLS viewer leaves."""
        async with self._stream_lock:
            await self._async_stop_idle_session()

    async def _async_stop_idle_session(self) -> None:
        """Retire one idle session and invalidate an unverified cached route."""
        cached_start_unverified = (
            self._video_first_media_at is None
            and (
                self._last_video_transport_attempted == "cached_xp2p"
                or (
                    self._last_video_transport == "cached_xp2p"
                    and self._unverified_playback_session is not None
                )
            )
        )
        if self._session is not None or getattr(self, "stream", None) is not None:
            await self._async_stop_active_session(reason="stream_idle")
        if cached_start_unverified:
            # Both relay and HA Stream idle monitors can cancel the pump without
            # invoking the relay-failure callback. Invalidate the same zero-frame
            # cached route before either monitor allows a replacement start.
            self._bypass_cached_xp2p = True
            await self._async_disable_camera_stream()
            await self._async_clear_failed_playback_caches(
                cached_xp2p=True,
                lan=False,
            )

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

    async def async_create_stream(self) -> Stream | None:
        """Create HA's stream with enough time for native XP2P startup."""
        create_stream_lock = getattr(self, "_create_stream_lock", None)
        if create_stream_lock is None:
            create_stream_lock = self._create_stream_lock = asyncio.Lock()
        async with create_stream_lock:
            ha_stream = await self._async_create_stream_locked()
            if ha_stream is not None:
                self._mark_video_live_view()
            if (
                ha_stream is not None
                and getattr(self, "_snapshot_owned_stream", None) is ha_stream
            ):
                self._snapshot_owned_stream = None
            return ha_stream

    async def _async_create_stream_locked(self) -> Stream | None:
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
        request = getattr(self, "_snapshot_request", None)
        if request is None:
            self._snapshot_request = request = VideoSnapshotRequest()
        return await request.async_get(
            self.hass,
            lambda: self._async_capture_snapshot(width, height),
        )

    async def _async_capture_snapshot(
        self, width: int | None, height: int | None
    ) -> bytes | None:
        """Keep retention ownership through the complete background image wait."""
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
        create_stream_lock = getattr(self, "_create_stream_lock", None)
        if create_stream_lock is None:
            create_stream_lock = self._create_stream_lock = asyncio.Lock()
        async with create_stream_lock:
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

    async def _async_stop_owned_stream(self, ha_stream: Stream) -> None:
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
        self._attr_is_on = False
        request = getattr(self, "_snapshot_request", None)
        if request is not None:
            await request.async_cancel()
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
        self._attr_is_on = False
        provider = getattr(self.coordinator, "video_diagnostics_provider", None)
        if provider == self.video_runtime_diagnostics:
            self.coordinator.video_diagnostics_provider = None
        prepare_task = self._runtime_prepare_task
        self._runtime_prepare_task = None
        if prepare_task is not None and not prepare_task.done():
            prepare_task.cancel()
            try:
                await prepare_task
            except asyncio.CancelledError:
                pass
        relay = getattr(self, "_flv_relay", None)
        if relay is not None:
            # A cold source factory owns _stream_lock while XP2P starts. Close
            # the relay before any state-gate task that may be waiting for the
            # same lock, so pump cancellation unwinds that startup promptly.
            await relay.async_close()
        request = getattr(self, "_snapshot_request", None)
        if request is not None:
            await request.async_cancel()
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
