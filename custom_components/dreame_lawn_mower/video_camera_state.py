"""Capability and state presentation for the mower video camera."""

from __future__ import annotations

from typing import Any

from . import video_stream_helpers as _video_helpers
from .const import DOMAIN, VIDEO_TRANSPORT_AUTO
from .dreame_lawn_mower_client.models import (
    camera_stream_block_reason as _camera_stream_block_reason,
)
from .dreame_lawn_mower_client.models import (
    snapshot_advertises_video as _snapshot_advertises_video,
)
from .dreame_lawn_mower_client.video_runtime import (
    DreameLawnMowerXp2pLiveStreamSession,
)
from .video_camera_types import _facade_binding, _FacadeModuleProxy

video_helpers = _FacadeModuleProxy("video_helpers", _video_helpers)


def camera_stream_block_reason(snapshot: Any) -> str | None:
    """Route state gating through the historical facade binding."""
    return _facade_binding(
        "camera_stream_block_reason",
        _camera_stream_block_reason,
    )(snapshot)


def snapshot_advertises_video(snapshot: Any) -> bool:
    """Route capability detection through the historical facade binding."""
    return _facade_binding(
        "snapshot_advertises_video",
        _snapshot_advertises_video,
    )(snapshot)


class DreameLawnMowerVideoStateMixin:
    """Expose mower video capability, availability, and diagnostics."""

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
            "managed_xp2p_runtime_environment": (
                video_helpers.managed_runtime_environment()
            ),
            "video_runtime_preparation_error": self._runtime_preparation_error,
            "stream_session_active": self._session is not None,
            "video_delivery": {
                "preferred": "webrtc",
                "fallback": "hls",
                "source": "loopback_flv_relay",
                **(
                    self._flv_relay.diagnostics
                    if getattr(self, "_flv_relay", None) is not None
                    else {}
                ),
            },
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
            "last_managed_runtime_diagnostics": getattr(
                self,
                "_last_managed_runtime_diagnostics",
                None,
            ),
            "last_stream_health": self._last_stream_health,
            "last_stream_session": self._session.as_dict(redact=True)
            if self._session is not None
            else None,
        }
