"""Shared failure reporting for mower video startup and cleanup."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from . import video_stream_helpers as _video_helpers
from .debug import sanitize_diagnostic_text
from .diagnostic_events import record_diagnostic_event
from .video_camera_configuration import _VideoCameraConfiguration
from .video_camera_types import _FacadeModuleProxy

_LOGGER = logging.getLogger(f"{__package__}.video_camera")
video_helpers = _FacadeModuleProxy("video_helpers", _video_helpers)


class _VideoCameraDiagnostics(_VideoCameraConfiguration):
    """Record sanitized failures with camera context and consistent state."""

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
