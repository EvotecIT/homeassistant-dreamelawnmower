"""Transport selection and startup orchestration for mower video."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from . import video_stream_helpers as _video_helpers
from .const import (
    VIDEO_TRANSPORT_AUTO,
    VIDEO_TRANSPORT_CLOUD,
    VIDEO_TRANSPORT_LAN,
)
from .debug import (
    sanitize_debug_data as _sanitize_debug_data,
)
from .debug import sanitize_diagnostic_text as _sanitize_diagnostic_text
from .diagnostic_events import record_diagnostic_event as _record_diagnostic_event
from .dreame_lawn_mower_client.models import (
    DreameLawnMowerCameraStreamRuntimeInputs,
)
from .dreame_lawn_mower_client.models import (
    camera_stream_block_reason as _camera_stream_block_reason,
)
from .dreame_lawn_mower_client.stream_health import (
    DreameLawnMowerStreamUrlProbeResult,
)
from .dreame_lawn_mower_client.video_provisioning_status import (
    XP2P_PROVISIONING_DEVICE_TRIPLE_MISSING,
)
from .dreame_lawn_mower_client.video_runtime import (
    DreameLawnMowerVideoRuntimeError,
    DreameLawnMowerXp2pLiveStreamSession,
)
from .video_cached_xp2p import async_start_cached_xp2p as _async_start_cached_xp2p
from .video_camera_types import (
    _DreameVideoRuntime,
    _facade_binding,
    _FacadeModuleProxy,
)

_LOGGER = logging.getLogger(__name__)
video_helpers = _FacadeModuleProxy("video_helpers", _video_helpers)


def sanitize_debug_data(value: Any) -> Any:
    """Route sanitization through the historical facade binding."""
    return _facade_binding("sanitize_debug_data", _sanitize_debug_data)(value)


def sanitize_diagnostic_text(value: Any) -> str:
    """Route diagnostic text through the historical facade binding."""
    return _facade_binding(
        "sanitize_diagnostic_text",
        _sanitize_diagnostic_text,
    )(value)


def record_diagnostic_event(*args: Any, **kwargs: Any) -> Any:
    """Route diagnostic events through the historical facade binding."""
    return _facade_binding(
        "record_diagnostic_event",
        _record_diagnostic_event,
    )(*args, **kwargs)


def camera_stream_block_reason(snapshot: Any) -> str | None:
    """Route state gating through the historical facade binding."""
    return _facade_binding(
        "camera_stream_block_reason",
        _camera_stream_block_reason,
    )(snapshot)


async def async_start_cached_xp2p(*args: Any, **kwargs: Any) -> Any:
    """Route cached XP2P startup through the historical facade binding."""
    return await _facade_binding(
        "async_start_cached_xp2p",
        _async_start_cached_xp2p,
    )(*args, **kwargs)


def _runtime_inputs_not_ready_message(
    inputs: DreameLawnMowerCameraStreamRuntimeInputs,
) -> str:
    """Return a useful, credential-free explanation for missing XP2P inputs."""
    if inputs.provisioning_issue == XP2P_PROVISIONING_DEVICE_TRIPLE_MISSING:
        return (
            "Dreame cloud has not provisioned an XP2P video identity for this "
            "mower on the current account/region. Confirm that live video works "
            "in Dreamehome or MOVAhome; contact Dreame support if it is also "
            "missing there."
        )
    return "Dreame cloud did not return required XP2P fields: " + ", ".join(
        inputs.missing_required
    )


class DreameLawnMowerVideoStartupMixin:
    """Start and adopt LAN, cached XP2P, or cloud video sessions."""

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
            self._video_transport
            in {
                VIDEO_TRANSPORT_AUTO,
                VIDEO_TRANSPORT_CLOUD,
                VIDEO_TRANSPORT_LAN,
            }
            and not await self._async_refresh_video_start_state()
        ):
            return None

        # A relay pump calls this while its first local viewer is already
        # subscribed. Preserve that relay/HA stream until an older runtime
        # session actually exists to retire.
        if self._session is not None:
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
        bypass_lan = (
            self._video_transport == VIDEO_TRANSPORT_AUTO
            and getattr(self, "_bypass_lan", False)
        )
        if self._video_transport != VIDEO_TRANSPORT_CLOUD and not bypass_lan:
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
        elif bypass_lan:
            self._last_lan_error = (
                "Same-LAN playback is bypassed after a decoder-level failure."
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
                    _facade_binding(
                        "_runtime_inputs_not_ready_message",
                        _runtime_inputs_not_ready_message,
                    )(cloud_inputs)
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

    async def _async_refresh_video_start_state(self) -> bool:
        """Require a fresh authoritative snapshot before any video startup."""
        try:
            snapshot = await self.coordinator.async_refresh_video_safety_state()
        except Exception as err:  # noqa: BLE001 - fail closed before video startup.
            _LOGGER.debug(
                "Failed to refresh mower state before video startup: %s",
                sanitize_diagnostic_text(err),
            )
            self._set_stream_error(
                "Could not refresh mower state before starting video.",
                stage="state_refresh",
            )
            return False
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
            start_session=self._async_start_cached_runtime_session,
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

    async def _async_start_cached_runtime_session(
        self,
        runtime: _DreameVideoRuntime,
        inputs: DreameLawnMowerCameraStreamRuntimeInputs,
        *,
        camera_toggle_managed: bool = False,
    ) -> DreameLawnMowerXp2pLiveStreamSession:
        """Use a bounded managed-host attempt for cached provisioning."""
        start_cached = getattr(runtime, "start_cached_live_stream", None)
        if not callable(start_cached):
            return await self._async_start_runtime_session(
                runtime,
                inputs,
                camera_toggle_managed=camera_toggle_managed,
            )
        return await self._async_start_runtime_session(
            runtime,
            inputs,
            camera_toggle_managed=camera_toggle_managed,
            start_callable=start_cached,
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
        start_callable: Callable[
            [DreameLawnMowerCameraStreamRuntimeInputs],
            DreameLawnMowerXp2pLiveStreamSession,
        ]
        | None = None,
    ) -> DreameLawnMowerXp2pLiveStreamSession:
        """Finish or clean up native startup if the HA request is cancelled."""
        start_job = self.hass.async_add_executor_job(
            start_callable or runtime.start_live_stream,
            inputs,
        )
        try:
            session = await asyncio.shield(start_job)
            self._last_managed_runtime_diagnostics = None
            session.provisioning_source = inputs.source
            session.camera_toggle_managed = camera_toggle_managed
            return session
        except asyncio.CancelledError:
            self._schedule_late_start_cleanup(runtime, start_job)
            raise
        except DreameLawnMowerVideoRuntimeError:
            self._last_managed_runtime_diagnostics = (
                video_helpers.safe_state_attribute(
                    getattr(runtime, "last_failure", None)
                )
            )
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
