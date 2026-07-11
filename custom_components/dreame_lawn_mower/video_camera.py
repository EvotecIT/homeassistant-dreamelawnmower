"""Live video camera entity for Dreame lawn mower."""

from __future__ import annotations

import asyncio
import logging
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
from .dreame_lawn_mower_client.models import (
    DreameLawnMowerCameraStreamRuntimeInputs,
    camera_stream_block_reason,
    snapshot_advertises_video,
)
from .dreame_lawn_mower_client.stream_health import DreameLawnMowerStreamUrlProbeResult
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
_SNAPSHOT_STREAM_START_TIMEOUT = 15.0


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
        self._session: DreameLawnMowerXp2pLiveStreamSession | None = None
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
        self._last_runtime_inputs_ready: bool | None = None
        self._last_runtime_inputs_source: str | None = None
        self._last_runtime_inputs_missing: tuple[str, ...] = ()
        self._last_stream_health: dict[str, Any] | None = None
        self._last_stream_enable_result: Any | None = None
        self._last_stream_disable_error: str | None = None
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

    async def async_added_to_hass(self) -> None:
        """Schedule managed runtime preparation without blocking entity setup."""
        await super().async_added_to_hass()
        if not self._lan_cache.loaded:
            try:
                await self._lan_cache.async_load()
            except Exception as err:  # noqa: BLE001 - Auto/cloud remain available.
                self._lan_cache_error = str(err)
                _LOGGER.warning("Failed to load Dreame LAN video cache: %s", err)
        if not self._provisioning_cache.loaded:
            try:
                await self._provisioning_cache.async_load()
            except Exception as err:  # noqa: BLE001 - cloud remains available.
                self._provisioning_cache_error = str(err)
                _LOGGER.warning(
                    "Failed to load Dreame video provisioning cache: %s",
                    err,
                )
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
            self._runtime_preparation_error = str(err)
            _LOGGER.warning("Failed to prepare Dreame mower live video: %s", err)
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

    @property
    def available(self) -> bool:
        """Return whether live video can be requested from Home Assistant."""
        if not self._runtime_configured:
            return False
        snapshot = self.coordinator.data
        if camera_stream_block_reason(snapshot) is not None:
            return False
        if (
            self._video_transport != VIDEO_TRANSPORT_CLOUD
            and (
                self._lan_cache.inputs is not None
                or self._provisioning_cache.inputs is not None
            )
        ):
            return True
        if snapshot is None:
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
            "video_transport_policy": self._video_transport,
            "video_block_reason": camera_stream_block_reason(self.coordinator.data),
            "last_video_transport": self._last_video_transport,
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
                if self._session_is_usable(self._session):
                    return self._session.stream_url
                await self._async_stop_active_session()
            return await self._async_start_stream()

    async def async_create_stream(self) -> Any | None:
        """Create HA's stream with enough time for native XP2P startup."""
        if not self._create_stream_lock:
            self._create_stream_lock = asyncio.Lock()
        async with self._create_stream_lock:
            async with self._stream_lock:
                if self.stream is not None:
                    if getattr(self, "_attr_is_on", True) and self._session_is_usable(
                        self._session
                    ):
                        return self.stream
                    await self._async_stop_active_session()

            previous_session = self._session
            session: DreameLawnMowerXp2pLiveStreamSession | None = None
            owns_session = False
            try:
                async with asyncio.timeout(_HA_STREAM_START_TIMEOUT):
                    source = await self.stream_source()
                if not source:
                    return None
                session = self._session
                owns_session = session is not None and session is not previous_session
                dynamic_settings = await self.hass.data[
                    DATA_CAMERA_PREFS
                ].get_dynamic_stream_settings(self.entity_id)

                async with self._stream_lock:
                    if (
                        not getattr(self, "_attr_is_on", True)
                        or self._session is not session
                        or not self._session_is_usable(session)
                    ):
                        if self._session is session:
                            await self._async_stop_active_session()
                        return None
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
                    return ha_stream
            except BaseException:
                if owns_session and session is not None:
                    await self._async_cleanup_failed_stream_setup(session)
                raise

    async def _async_cleanup_failed_stream_setup(
        self,
        session: DreameLawnMowerXp2pLiveStreamSession,
    ) -> None:
        """Stop a session started for an HA stream that was never adopted."""
        async with self._stream_lock:
            if self._session is session and self.stream is None:
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
            return await self._async_camera_image_locked(width, height)

    async def _async_camera_image_locked(
        self,
        width: int | None,
        height: int | None,
    ) -> bytes | None:
        """Decode one JPEG while serializing snapshot-only session ownership."""
        existing_session = self._session
        snapshot_session: DreameLawnMowerXp2pLiveStreamSession | None = None
        try:
            async with asyncio.timeout(_SNAPSHOT_STREAM_START_TIMEOUT):
                source = await self.stream_source()
        except TimeoutError:
            _LOGGER.debug("Timed out starting Dreame mower video for a still image")
            return self._last_image
        if source is None:
            return self._last_image
        try:
            current_session = self._session
            if current_session is not None and current_session is not existing_session:
                snapshot_session = current_session
            image = await self.hass.async_add_executor_job(
                video_helpers.decode_flv_jpeg,
                source,
                width,
                height,
            )
        except Exception as err:  # noqa: BLE001 - snapshots may be transient.
            _LOGGER.debug("Failed to decode Dreame mower still image: %s", err)
            return self._last_image
        finally:
            if snapshot_session is not None:
                await self._async_stop_snapshot_session(snapshot_session)
        if image is not None:
            self._last_image = image
        return image or self._last_image

    async def _async_stop_snapshot_session(
        self,
        snapshot_session: DreameLawnMowerXp2pLiveStreamSession,
    ) -> None:
        """Stop a session created only for a still unless HLS adopted it."""
        while True:
            async with self._stream_lock:
                if self._session is not snapshot_session or self.stream is not None:
                    return
                create_stream_lock = self._create_stream_lock
                if create_stream_lock is None or not create_stream_lock.locked():
                    await self._async_stop_active_session()
                    return
            async with create_stream_lock:
                pass

    async def _async_start_stream(self) -> str | None:
        """Start one serialized XP2P stream session."""
        self._last_stream_health = None
        self._last_lan_error = None
        self._last_cached_xp2p_error = None
        cached_xp2p_inputs = (
            self._provisioning_cache.inputs
            if self._video_transport == VIDEO_TRANSPORT_AUTO
            else None
        )
        if not self._runtime_configured:
            self._set_stream_error(
                "Configure a native XP2P library path or XP2P runner command."
            )
            return None
        if reason := camera_stream_block_reason(self.coordinator.data):
            self._set_stream_error(reason)
            return None

        await self._async_stop_active_session()
        try:
            runtime = await self._async_get_runtime()
            self._runtime_preparation_error = None
        except Exception as err:  # noqa: BLE001 - HA should receive a clean miss.
            self._set_stream_error(str(err))
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
                lan_source = await self._async_try_lan_stream(runtime, lan_inputs)
                if lan_source is not None:
                    return lan_source
                if (
                    self._video_transport == VIDEO_TRANSPORT_AUTO
                    and cached_endpoint is not None
                ):
                    cached_error = self._last_lan_error
                    try:
                        await self._lan_cache.async_clear_endpoint()
                        self._lan_cache_error = None
                    except Exception as err:  # noqa: BLE001 - retry in memory.
                        self._lan_cache_error = str(err)
                        _LOGGER.warning(
                            "Failed to clear stale Dreame LAN endpoint: %s", err
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
                            lan_source = await self._async_try_lan_stream(
                                runtime,
                                cloud_inputs,
                            )
                            if lan_source is not None:
                                return lan_source
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

            if self._video_transport == VIDEO_TRANSPORT_LAN:
                self._set_stream_error(self._last_lan_error or "Same-LAN video failed.")
                return None

        if cached_xp2p_inputs is not None and cached_xp2p_inputs.ready:
            cached_source = await self._async_try_cached_xp2p_stream(
                runtime,
                cached_xp2p_inputs,
            )
            if cached_source is not None:
                return cached_source

        stream_enable_attempted = False
        session: DreameLawnMowerXp2pLiveStreamSession | None = None
        try:
            if cloud_inputs is None:
                if cloud_inputs_error is not None:
                    raise cloud_inputs_error
                cloud_inputs = await self._async_get_runtime_inputs()
            if not cloud_inputs.ready:
                raise DreameLawnMowerVideoRuntimeError(
                    "Dreame cloud did not return required XP2P fields: "
                    + ", ".join(cloud_inputs.missing_required)
                )
            stream_enable_attempted = True
            self._last_stream_enable_result = video_helpers.safe_state_attribute(
                await self.coordinator.client.async_set_camera_stream_enabled(True)
            )
            self._last_stream_disable_error = None
            session = await self._async_start_runtime_session(runtime, cloud_inputs)
            stream_health = await self.hass.async_add_executor_job(
                video_helpers.probe_stream_health_and_route,
                runtime,
                session,
            )
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
            self._set_stream_error(self._with_lan_failure(str(err)))
            return None
        except Exception as err:  # noqa: BLE001 - HA should receive a clean miss.
            if runtime is not None and session is not None:
                await self._async_stop_session(runtime, session)
            if stream_enable_attempted:
                await self._async_disable_camera_stream()
            _LOGGER.warning("Failed to start Dreame mower live video: %s", err)
            self._set_stream_error(self._with_lan_failure(str(err)))
            return None

        self._last_stream_health = stream_health.as_dict()
        if not stream_health.flv_header_present:
            await self._async_stop_session(runtime, session)
            await self._async_disable_camera_stream()
            self._set_stream_error(
                self._with_lan_failure(video_helpers.stream_health_error(stream_health))
            )
            return None

        return self._adopt_stream_session(
            runtime,
            session,
            stream_health,
            transport=VIDEO_TRANSPORT_CLOUD,
        )

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
            self._last_lan_error = str(err)
            return None
        if not stream_health.flv_header_present:
            await self._async_stop_session(runtime, session)
            self._last_lan_error = video_helpers.stream_health_error(stream_health)
            return None
        try:
            await self._lan_cache.async_save_session(session)
            self._lan_cache_error = None
        except Exception as err:  # noqa: BLE001 - a live stream remains usable.
            self._lan_cache_error = str(err)
            _LOGGER.warning("Failed to save Dreame LAN video endpoint: %s", err)
        return self._adopt_stream_session(
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
            stop_session=self._async_stop_session,
            executor=self.hass.async_add_executor_job,
        )
        if result.session is None or result.health is None:
            self._last_cached_xp2p_error = result.error
            return None
        return self._adopt_stream_session(
            runtime,
            result.session,
            result.health,
            transport="cached_xp2p",
        )

    async def _async_get_runtime_inputs(
        self,
    ) -> DreameLawnMowerCameraStreamRuntimeInputs:
        """Fetch cloud inputs and refresh both LAN and private XP2P caches."""
        inputs = await self.coordinator.client.async_get_camera_stream_runtime_inputs()
        self._last_runtime_inputs_ready = inputs.ready
        self._last_runtime_inputs_source = inputs.source
        self._last_runtime_inputs_missing = inputs.missing_required
        if inputs.lan_identity_ready:
            try:
                await self._lan_cache.async_save_identity(inputs)
                self._lan_cache_error = None
            except Exception as err:  # noqa: BLE001 - cloud transport still works.
                self._lan_cache_error = str(err)
                _LOGGER.warning("Failed to save Dreame LAN video identity: %s", err)
        if inputs.ready:
            config = await self.hass.async_add_executor_job(
                self._provisioning_cache.resolve_fresh_device_config,
                inputs,
            )
            try:
                await self._provisioning_cache.async_save(inputs, config)
                self._provisioning_cache_error = None
            except Exception as err:  # noqa: BLE001 - current stream can continue.
                self._provisioning_cache_error = str(err)
                _LOGGER.warning(
                    "Failed to save Dreame video provisioning cache: %s",
                    err,
                )
        return inputs

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
        stream_health: DreameLawnMowerStreamUrlProbeResult,
        *,
        transport: str,
    ) -> str:
        """Adopt one health-checked session as the active HA camera source."""
        self._runtime = runtime
        self._session = session
        self._last_stream_health = stream_health.as_dict()
        self._last_error = None
        self._last_video_transport = transport
        self._attr_is_on = True
        self._attr_is_streaming = True
        self.async_write_ha_state()
        return session.stream_url

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
        prepare_task = self._runtime_prepare_task
        self._runtime_prepare_task = None
        if prepare_task is not None and not prepare_task.done():
            prepare_task.cancel()
            try:
                await prepare_task
            except asyncio.CancelledError:
                pass
        async with self._stream_lock:
            await self._async_stop_active_session()

    async def _async_stop_active_session(self) -> None:
        """Stop the current runtime session if one is active."""
        await self._stream_idle_monitor.async_cancel()
        ha_stream = getattr(self, "stream", None)
        self.stream = None
        runtime = self._runtime
        session = self._session
        self._runtime = None
        self._session = None
        self._attr_is_streaming = False
        if ha_stream is not None:
            try:
                await ha_stream.stop()
            except Exception as err:  # noqa: BLE001 - continue XP2P cleanup.
                _LOGGER.debug("Failed to stop Home Assistant camera stream: %s", err)
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
    def _native_library_path(self) -> str | None:
        return video_helpers.option_text(self._entry, CONF_XP2P_LIBRARY_PATH)

    @property
    def _runner_command(self) -> str | None:
        return video_helpers.option_text(self._entry, CONF_XP2P_RUNNER_COMMAND)

    def _set_stream_error(self, error: str) -> None:
        self._last_error = error
        self._attr_is_streaming = False
        self.async_write_ha_state()
