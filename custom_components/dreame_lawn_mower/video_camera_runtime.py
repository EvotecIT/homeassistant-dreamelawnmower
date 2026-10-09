"""Runtime preparation and adapter selection for mower video."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from threading import Event

from . import video_stream_helpers as _video_helpers
from .const import DOMAIN, VIDEO_TRANSPORT_AUTO, XP2P_RUNNER_MODE_ONE_SHOT
from .dreame_lawn_mower_client.client_refresh import _run_state_worker
from .dreame_lawn_mower_client.models import DreameLawnMowerCameraStreamRuntimeInputs
from .dreame_lawn_mower_client.video_runtime import DreameLawnMowerVideoRuntimeError
from .dreame_lawn_mower_client.xp2p_config import DreameLawnMowerXp2pDeviceConfig
from .dreame_lawn_mower_client.xp2p_host_runtime import DreameLawnMowerXp2pHostAssets
from .video_camera_cleanup import _VideoCameraCleanup
from .video_camera_types import _DreameVideoRuntime, _FacadeModuleProxy

_LOGGER = logging.getLogger(f"{__package__}.video_camera")
video_helpers = _FacadeModuleProxy("video_helpers", _video_helpers)


class _VideoCameraRuntime(_VideoCameraCleanup):
    """Reuse prepared adapters and select the configured runtime implementation."""

    async def _async_prepare_runtime(self) -> None:
        """Prepare a configured runtime in the background before first playback."""
        from . import video_camera

        try:
            await self._async_create_runtime()
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 - retry remains available on play.
            self._runtime_preparation_error = video_camera.sanitize_diagnostic_text(err)
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
        return await self._async_create_runtime()

    async def _async_create_runtime(self) -> _DreameVideoRuntime:
        """Download managed assets asynchronously before owned worker startup."""
        from . import video_camera

        if self._prepared_runtime is not None:
            return self._prepared_runtime
        if (
            self._runner_command
            or self._native_library_path
            or not video_helpers.managed_runtime_supported()
        ):
            return await self.hass.async_add_executor_job(self._create_runtime)
        assets = await video_camera.async_ensure_xp2p_host_runtime(
            Path(self.hass.config.path(".storage", DOMAIN, "xp2p-runtime")),
            video_camera.async_get_clientsession(self.hass),
        )
        return await _run_state_worker(lambda: self._create_runtime(assets), Event())

    def _create_runtime(
        self,
        managed_assets: DreameLawnMowerXp2pHostAssets | None = None,
    ) -> _DreameVideoRuntime:
        """Create the configured runtime adapter."""
        # Resolve the established facade bindings after module initialization.
        from . import video_camera as camera

        if self._prepared_runtime is not None:
            return self._prepared_runtime
        if runner_command := self._runner_command:
            self._last_native_runtime_diagnostics = None
            command = video_helpers.split_runner_command(runner_command)
            if self._runtime_mode == XP2P_RUNNER_MODE_ONE_SHOT:
                runtime: _DreameVideoRuntime = camera.DreameLawnMowerXp2pExternalRunner(
                    command
                )
            else:
                runtime = camera.DreameLawnMowerXp2pProcessRunner(command)
            self._prepared_runtime = runtime
            return runtime

        if library_path := self._native_library_path:
            path = Path(library_path)
            diagnostics = camera.diagnose_native_xp2p_runtime(path)
            self._last_native_runtime_diagnostics = video_helpers.safe_state_attribute(
                diagnostics.as_dict()
            )
            if not diagnostics.ready:
                raise DreameLawnMowerVideoRuntimeError(
                    diagnostics.error or "Configured XP2P native library is not ready."
                )
            runtime = camera.DreameLawnMowerNativeXp2pRuntime(
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
            runtime = camera.DreameLawnMowerXp2pHostRuntime(
                (
                    managed_assets
                    if managed_assets is not None
                    else camera.ensure_xp2p_host_runtime(runtime_root)
                ),
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
