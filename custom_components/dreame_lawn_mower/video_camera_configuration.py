"""Shared runtime selection and persisted capability for mower video."""

from __future__ import annotations

from .const import (
    CONF_XP2P_LIBRARY_PATH,
    CONF_XP2P_RUNNER_COMMAND,
    CONF_XP2P_RUNNER_MODE,
    XP2P_RUNNER_MODE_ONE_SHOT,
    XP2P_RUNNER_MODE_PROCESS,
)
from .video_camera_types import _VideoCameraState


class _VideoCameraConfiguration(_VideoCameraState):
    """Resolve shared configuration from the camera's entry and route caches."""

    @property
    def _runtime_configured(self) -> bool:
        from . import video_camera

        return bool(
            self._runner_command
            or self._native_library_path
            or video_camera.video_helpers.managed_runtime_supported()
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
        from . import video_camera

        return video_camera.video_helpers.video_transport(self._entry)

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
        from . import video_camera

        return video_camera.video_helpers.option_text(
            self._entry, CONF_XP2P_LIBRARY_PATH
        )

    @property
    def _runner_command(self) -> str | None:
        from . import video_camera

        return video_camera.video_helpers.option_text(
            self._entry, CONF_XP2P_RUNNER_COMMAND
        )
