"""Map-scoped runtime publication and host-provided identity lifetime."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from threading import RLock
from typing import TYPE_CHECKING

from .client_transport import _DreameLawnMowerClientTransport
from .models import DreameLawnMowerStatusBlob

if TYPE_CHECKING:
    from .models import DreameLawnMowerSnapshot
    from .position_tracking import MowerPosition, MowerPositionTracker


class _DreameLawnMowerClientTrackingMixin(_DreameLawnMowerClientTransport):
    """Own live evidence retirement for both streamed and rendered maps."""

    _position_tracker: MowerPositionTracker
    _latest_runtime_status_blob: DreameLawnMowerStatusBlob | None
    _runtime_map_identity_expires_at: datetime | None
    _runtime_live_track_segments: tuple[tuple[tuple[int, int], ...], ...]
    _runtime_live_map_index: int | None
    _runtime_live_task_id: int | None
    _runtime_session_active: bool | None
    _last_runtime_track_blob_hex: str | None
    _runtime_live_geometry_identity: str | None
    _runtime_geometry_lock: RLock
    _runtime_geometry_revision: int
    _observed_map_geometry: tuple[int, str] | None
    _latest_snapshot: DreameLawnMowerSnapshot | None

    @property
    def _runtime_geometry_generation(self) -> int:
        """Capture frame ownership before starting a map read."""
        with self._runtime_geometry_lock:
            return self._runtime_geometry_revision

    def _observe_runtime_geometry(
        self, *, map_index: int, geometry: str,
        contains: Callable[[int, int], bool], expected_generation: int | None = None,
    ) -> MowerPosition | None:
        """Commit one current observation atomically, rejecting overtaken reads."""
        with self._runtime_geometry_lock:
            if (
                expected_generation is not None
                and expected_generation != self._runtime_geometry_revision
            ):
                return None
            frame = (map_index, geometry)
            if frame != self._observed_map_geometry:
                self._observed_map_geometry = frame
                self._runtime_geometry_revision += 1
            self._expire_runtime_live_tracking()
            self._bind_runtime_live_geometry(map_index, geometry)
            return self._position_tracker.resolve(
                map_index=map_index, geometry=geometry, contains=contains,
                snapshot=self._latest_snapshot,
            )

    def _bind_runtime_live_geometry(self, map_index: int, geometry: str) -> None:
        """Discard trails when a verified slot's coordinate frame is replaced."""
        if self._runtime_live_map_index != map_index:
            return
        if self._runtime_live_geometry_identity not in (None, geometry):
            self._runtime_live_track_segments = ()
        self._runtime_live_geometry_identity = geometry

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
        with self._runtime_geometry_lock:
            self._runtime_map_identity_expires_at = deadline
            if deadline is None:
                self._retire_runtime_live_tracking()
            else:
                self._expire_runtime_live_tracking()

    def _retire_runtime_live_tracking(self) -> None:
        """Revoke live publication, preserving history and consumed packets."""
        with self._runtime_geometry_lock:
            self._retire_runtime_live_tracking_locked()

    def _retire_runtime_live_tracking_locked(self) -> None:
        # Keep the observed frame and generation: retirement cannot reopen
        # admission for a read that an authoritative observation overtook.
        self._position_tracker.invalidate_current()
        self._latest_runtime_status_blob = None
        self._runtime_live_track_segments = ()
        self._runtime_live_map_index = None
        self._runtime_live_task_id = None
        self._runtime_live_geometry_identity = None

    def _expire_runtime_live_tracking(self) -> bool:
        """Enforce identity lifetime even when no coordinator callback runs."""
        with self._runtime_geometry_lock:
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
        """Publish telemetry against the same lock as geometry observations."""
        with self._runtime_geometry_lock:
            self._update_runtime_live_tracking(
                status_blob, active=active, map_index=map_index,
            )

    def _update_runtime_live_tracking(
        self, status_blob: DreameLawnMowerStatusBlob | None, *, active: bool,
        map_index: int | None,
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
            self._runtime_live_geometry_identity = None
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
            self._runtime_live_geometry_identity = None
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
