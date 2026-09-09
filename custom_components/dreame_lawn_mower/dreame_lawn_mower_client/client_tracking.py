"""Map-scoped runtime publication and host-provided identity lifetime."""

from __future__ import annotations

from datetime import UTC, datetime

from .models import DreameLawnMowerStatusBlob


class _DreameLawnMowerClientTrackingMixin:
    """Own live evidence retirement for both streamed and rendered maps."""

    @property
    def runtime_map_identity_expires_at(self) -> datetime | None:
        """Return the absolute deadline for verified identity, or None if revoked.

        Standalone callers own identity validity and default to no expiration.
        A caching host sets its UTC deadline. None retires existing live evidence
        without discarding map-scoped history; a new deadline restores admission.
        """
        return self._runtime_map_identity_expires_at

    @runtime_map_identity_expires_at.setter
    def runtime_map_identity_expires_at(self, deadline: datetime | None) -> None:
        self._runtime_map_identity_expires_at = deadline
        if deadline is None:
            self._retire_runtime_live_tracking()
        else:
            self._expire_runtime_live_tracking()

    def _retire_runtime_live_tracking(self) -> None:
        """Revoke live publication, preserving history and consumed packets."""
        self._position_tracker.invalidate_current()
        self._latest_runtime_status_blob = None
        self._runtime_live_track_segments = ()
        self._runtime_live_map_index = None
        self._runtime_live_task_id = None

    def _expire_runtime_live_tracking(self) -> bool:
        """Enforce identity lifetime even when no coordinator callback runs."""
        deadline = self._runtime_map_identity_expires_at
        if deadline is None or datetime.now(UTC) >= deadline:
            self._retire_runtime_live_tracking()
            return True
        return False

    def update_runtime_live_tracking(
        self,
        status_blob: DreameLawnMowerStatusBlob | None,
        *,
        active: bool,
        map_index: int | None = None,
    ) -> None:
        """Cache live overlays only against an explicitly verified map index.

        An unknown map retires live context; it never inherits the previous slot.
        Scoped historical position evidence remains available as last-known data.
        """
        if self._expire_runtime_live_tracking():
            map_index = None
        if map_index is None or self._runtime_live_map_index not in (None, map_index):
            self._position_tracker.invalidate_current()
        self._position_tracker.record(status_blob, map_index=map_index)
        self._latest_runtime_status_blob = (
            status_blob if map_index is not None else None
        )
        self._runtime_session_active = active
        if not active or map_index is None:
            self._runtime_live_track_segments = ()
            # Consume unscoped packets so recovery cannot rebind their cached
            # coordinates to a map discovered only after they arrived.
            self._last_runtime_track_blob_hex = (
                getattr(status_blob, "hex", None) or self._last_runtime_track_blob_hex
            )
            self._runtime_live_map_index = None
            self._runtime_live_task_id = None
            return

        task_id = getattr(status_blob, "candidate_runtime_task_id", None)
        context_changed = (
            self._runtime_live_map_index is not None
            and map_index is not None
            and self._runtime_live_map_index != map_index
        ) or (
            self._runtime_live_task_id is not None
            and task_id is not None
            and self._runtime_live_task_id != task_id
        )
        if context_changed:
            self._runtime_live_track_segments = ()
        if map_index is not None:
            self._runtime_live_map_index = map_index
        if task_id is not None:
            self._runtime_live_task_id = task_id

        if status_blob is None:
            return

        blob_hex = getattr(status_blob, "hex", None)
        if blob_hex and blob_hex == self._last_runtime_track_blob_hex:
            return

        segments = getattr(status_blob, "candidate_runtime_track_segments", ()) or ()
        if not segments:
            if blob_hex:
                self._last_runtime_track_blob_hex = blob_hex
            return

        self._runtime_live_track_segments = (
            *self._runtime_live_track_segments,
            *tuple(tuple(tuple(point) for point in segment) for segment in segments),
        )
        if len(self._runtime_live_track_segments) > 64:
            self._runtime_live_track_segments = self._runtime_live_track_segments[-64:]
        if blob_hex:
            self._last_runtime_track_blob_hex = blob_hex
