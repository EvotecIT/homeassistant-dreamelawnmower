"""Shared runtime contract for mower video camera modules."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any, Protocol

from homeassistant.components.camera import Camera
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import DreameLawnMowerCoordinator
from .dreame_lawn_mower_client.models import (
    DreameLawnMowerCameraStreamRuntimeInputs,
)
from .dreame_lawn_mower_client.video_runtime import (
    DreameLawnMowerXp2pLiveStreamSession,
)

if TYPE_CHECKING:
    import asyncio

    from homeassistant.components.stream import Stream

    from .dreame_lawn_mower_client.models import DreameLawnMowerDescriptor
    from .runtime_data import DreameLawnMowerConfigEntry
    from .video_flv_relay import DreameLawnMowerFlvRelay
    from .video_lan_cache import DreameLawnMowerVideoLanCache
    from .video_provisioning_cache import DreameLawnMowerVideoProvisioningCache
    from .video_session_lifecycle import DreameLawnMowerHaStreamIdleMonitor
    from .video_snapshot import VideoSnapshotRequest


_FACADE_MODULE = f"{__package__}.video_camera"


def _facade_binding(name: str, fallback: Any) -> Any:
    """Return a possibly monkeypatched binding from the historical facade."""
    facade = sys.modules.get(_FACADE_MODULE)
    return getattr(facade, name, fallback) if facade is not None else fallback


class _FacadeModuleProxy:
    """Resolve module attributes through a replaceable facade binding."""

    def __init__(self, name: str, fallback: Any) -> None:
        self._name = name
        self._fallback = fallback

    def __getattr__(self, name: str) -> Any:
        target = _facade_binding(self._name, self._fallback)
        return getattr(target, name)


class _DreameVideoRuntime(Protocol):
    """Runtime contract shared by native and external XP2P adapters."""

    def start_live_stream(
        self,
        inputs: DreameLawnMowerCameraStreamRuntimeInputs,
    ) -> DreameLawnMowerXp2pLiveStreamSession:
        """Start live video and return a local stream session."""

    def stop_live_stream(self, session: DreameLawnMowerXp2pLiveStreamSession) -> None:
        """Stop a previously started stream session."""


class _VideoCameraState(CoordinatorEntity[DreameLawnMowerCoordinator], Camera):
    """State initialized by the camera and shared by its lifecycle mixins.

    Declarations supply no defaults and do not create sessions or tasks.
    """

    _entry: DreameLawnMowerConfigEntry
    _descriptor: DreameLawnMowerDescriptor
    _stream_lock: asyncio.Lock
    _snapshot_owned_stream: Stream | None
    _snapshot_lock: asyncio.Lock
    _snapshot_requests: int
    _snapshot_request: VideoSnapshotRequest
    _video_retention_mode: str
    _video_live_view_seen: bool
    _stream_idle_monitor: DreameLawnMowerHaStreamIdleMonitor
    _flv_relay: DreameLawnMowerFlvRelay
    _video_recovery_failure_count: int
    _video_recovery_success_count: int
    _video_recovery_consecutive_failures: int
    _video_recovery_pending: bool
    _video_retry_not_before: float
    _lan_cache: DreameLawnMowerVideoLanCache
    _provisioning_cache: DreameLawnMowerVideoProvisioningCache
    _bypass_cached_xp2p: bool
    _bypass_lan: bool
    _video_capability_advertised: bool
    _video_capability_observed: bool
    _runtime: _DreameVideoRuntime | None
    _prepared_runtime: _DreameVideoRuntime | None
    _runtime_prepare_task: asyncio.Task[None] | None
    _state_gate_cleanup_task: asyncio.Task[None] | None
    _session: DreameLawnMowerXp2pLiveStreamSession | None
    _unverified_playback_session: DreameLawnMowerXp2pLiveStreamSession | None
    _pending_provisioning_inputs: DreameLawnMowerCameraStreamRuntimeInputs | None
    _video_start_requested_at: float | None
    _video_first_media_at: float | None
    _last_stream_recovered_at: str | None
    _last_error: str | None
    _last_error_at: str | None
    _last_error_code: str | None
    _last_error_stage: str | None
    _last_runtime_inputs_ready: bool | None
    _last_runtime_inputs_source: str | None
    _last_runtime_inputs_missing: tuple[str, ...]
    _last_runtime_inputs_provisioning_issue: str | None
    _last_runtime_input_diagnostics: dict[str, Any] | None
    _last_stream_health: dict[str, Any] | None
    _last_stream_enable_result: Any | None
    _last_stream_disable_error: str | None
    _last_stream_cleanup_reason: str | None
    _last_stream_cleanup_error: str | None
    _last_stream_cleanup_error_stage: str | None
    _last_stream_cleanup_at: str | None
    _last_native_runtime_diagnostics: dict[str, Any] | None
    _last_managed_runtime_diagnostics: dict[str, Any] | None
    _runtime_preparation_error: str | None
    _last_image: bytes | None
    _lan_cache_error: str | None
    _last_lan_error: str | None
    _provisioning_cache_error: str | None
    _last_cached_xp2p_error: str | None
    _last_video_transport: str | None
    _last_video_transport_attempted: str | None
