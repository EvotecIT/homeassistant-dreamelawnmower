"""Map-scoped runtime publication and host-provided identity lifetime."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock
from typing import TYPE_CHECKING

from .client_transport import _DreameLawnMowerClientTransport
from .models import DreameLawnMowerStatusBlob
from .mowing_map import MAX_TRAIL_POINTS
from .position_tracking import (
    POSITION_MAX_AGE_SECONDS,
    position_at_charger,
    position_timestamp,
)

# A same-mission trail survives a recharge, which can last hours. The mission
# generation, task id and coordinate frame must all still match to recover it.
TRAIL_MISSION_MAX_AGE_SECONDS = 24 * 60 * 60


def _joined_runtime_trail(
    segments: tuple[tuple[tuple[int, int], ...], ...],
) -> list[tuple[tuple[int, int], ...]]:
    """Merge packet windows that repeat or extend the previous segment.

    Packets resend a sliding window of the path's corner points: the same
    window arrives again until the mower turns, then a window that starts with
    the tail of the previous one. Kept as-is, a single lane becomes dozens of
    identical segments and a short session exceeds the map card's segment
    budget. Windows are joined only when they share at least two exact points
    (one shared endpoint is not proof of the same window), so explicit breaks
    stay disconnected.
    """
    joined: list[tuple[tuple[int, int], ...]] = []
    for segment in segments:
        if joined:
            last = joined[-1]
            if len(segment) <= len(last) and last[-len(segment):] == segment:
                continue
            overlap = next(
                (
                    size
                    for size in range(min(len(segment), len(last)), 1, -1)
                    if last[-size:] == segment[:size]
                ),
                0,
            )
            if overlap:
                joined[-1] = (*last, *segment[overlap:])
                continue
        joined.append(segment)
    return joined


def bounded_runtime_trail(
    segments: tuple[tuple[tuple[int, int], ...], ...],
) -> tuple[tuple[tuple[int, int], ...], ...]:
    """Join repeated packet windows, then keep the newest points in budget.

    Packets carry short segments, so a segment count dropped most of a long
    mowing session. The map overlay applies the same point budget. A joined
    segment can be long: the oldest one that straddles the budget keeps its
    newest points instead of disappearing whole.
    """
    joined = _joined_runtime_trail(segments)
    total = 0
    for start in range(len(joined) - 1, -1, -1):
        remaining = MAX_TRAIL_POINTS - total
        if len(joined[start]) > remaining:
            tail = joined[start][-remaining:] if remaining >= 2 else ()
            return tuple(([tail] if tail else []) + joined[start + 1:])
        total += len(joined[start])
    return tuple(joined)


if TYPE_CHECKING:
    from .models import DreameLawnMowerSnapshot
    from .position_tracking import MowerPosition, MowerPositionTracker
    from .zone_mowing_history import ZoneMowingHistory


@dataclass(frozen=True, slots=True)
class _RuntimeTrailCheckpoint:
    """Bounded, unpublished history awaiting the same verified session/frame."""

    map_index: int
    task_id: int
    geometry: str
    segments: tuple[tuple[tuple[int, int], ...], ...]
    suspended_at: float
    # The host's mission generation when known. Only a labelled checkpoint may
    # wait through a recharge; an unlabelled one keeps the short recovery gap.
    mission_generation: int | None = None


@dataclass(frozen=True, slots=True)
class _PositionFrame:
    """The last map frame a position was judged against, kept for readers.

    It lets consumers that never request a map read judge new telemetry with
    the same tracker, containment test and station as the map camera.
    """

    map_index: int
    geometry: str
    contains: Callable[[int, int], bool]
    charger: tuple[int, int] | None
    station_revision: str | None = None


class _DreameLawnMowerClientTrackingMixin(_DreameLawnMowerClientTransport):
    """Own live evidence retirement for both streamed and rendered maps."""

    _charger_history: ZoneMowingHistory | None
    _map_lineage: dict[str, str]
    _position_tracker: MowerPositionTracker
    _latest_runtime_status_blob: DreameLawnMowerStatusBlob | None
    _runtime_map_identity_expires_at: datetime | None
    _runtime_live_track_segments: tuple[tuple[tuple[int, int], ...], ...]
    _runtime_live_map_index: int | None
    _runtime_live_task_id: int | None
    _runtime_mission_generation: int | None = None
    _runtime_trail_checkpoint: _RuntimeTrailCheckpoint | None = None
    _runtime_live_packet_admitted: bool = False
    _position_frame: _PositionFrame | None = None
    _runtime_session_active: bool | None
    _last_runtime_track_blob_hex: str | None
    _runtime_live_geometry_identity: str | None
    _runtime_geometry_lock: RLock
    _runtime_geometry_revision: int
    _runtime_geometry_map_index: int | None
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
        charger: tuple[int, int] | None = None,
        station_revision: str | None = None,
    ) -> MowerPosition | None:
        """Commit one current observation atomically, rejecting overtaken reads."""
        with self._runtime_geometry_lock:
            frame = (map_index, geometry)
            # Judge the captured scope before expiry retires its live fields.
            if not self._runtime_geometry_admitted(
                map_index, geometry, expected_generation,
            ):
                return None
            expired = self._expire_runtime_live_tracking()
            if expired:
                position = self._position_tracker.resolve(
                    map_index=map_index, geometry=geometry, contains=contains,
                    snapshot=self._latest_snapshot, allow_geometry_change=False,
                )
                # A verified current station is static evidence. Remember its
                # observed frame without renewing a live lease, admitting a
                # packet, rebinding tracker history or restoring a trail.
                if (
                    station_revision is not None and charger is not None
                    and self._observed_map_geometry in (None, frame)
                ) or self._position_tracker.geometry == geometry:
                    self._position_frame = _PositionFrame(
                        map_index, geometry, contains, charger, station_revision,
                    )
                return position
            if frame != self._observed_map_geometry:
                self._observed_map_geometry = frame
                self._runtime_geometry_revision += 1
            self._bind_runtime_live_geometry(map_index, geometry)
            self._position_frame = _PositionFrame(
                map_index, geometry, contains, charger, station_revision,
            )
            return self._position_tracker.resolve(
                map_index=map_index, geometry=geometry, contains=contains,
                snapshot=self._latest_snapshot,
            )

    def _runtime_geometry_admitted(
        self, map_index: int, geometry: str, expected_generation: int | None,
    ) -> bool:
        """Fence geometry-derived state under the runtime geometry lock."""
        return self._runtime_geometry_map_index in (None, map_index) and (
            expected_generation is None
            or expected_generation == self._runtime_geometry_revision
            or (map_index, geometry) == self._observed_map_geometry
        )

    def current_map_position(self) -> MowerPosition | None:
        """Judge the latest telemetry against the last observed map frame.

        Map reads observe the frame; telemetry callbacks feed the tracker. This
        reader joins both without a cloud read or an image request, using the
        same tracker, containment test and station as the map camera, so a
        position consumer cannot depend on someone viewing the map.
        """
        with self._runtime_geometry_lock:
            self._expire_runtime_live_tracking()
            frame = self._position_frame
            if (
                frame is None
                # A newer committed frame supersedes the remembered one. Without
                # a live session none is committed; the tracker's own geometry
                # check in resolve() then decides.
                or self._observed_map_geometry
                not in (None, (frame.map_index, frame.geometry))
                # The station is only valid in the frame the evidence is bound to.
                or self._position_tracker.geometry not in (None, frame.geometry)
                or (
                    self._position_tracker.geometry is None
                    and frame.station_revision is None
                )
                or self._runtime_geometry_map_index not in (None, frame.map_index)
                or self._runtime_live_geometry_identity not in (None, frame.geometry)
            ):
                return None
            position = self._position_tracker.resolve(
                map_index=frame.map_index, geometry=frame.geometry,
                contains=frame.contains, snapshot=self._latest_snapshot,
                allow_geometry_change=False,
            )
            return self._place_docked_on_station(
                position, map_index=frame.map_index, geometry=frame.geometry,
                charger=frame.charger,
            )

    def _place_docked_on_station(
        self, position: MowerPosition | None, *, map_index: int, geometry: str,
        charger: tuple[int, int] | None,
    ) -> MowerPosition | None:
        """Use the reported station for a docked mower on the verified frame.

        The one owner of this decision for the camera, the interactive overlay
        and position readers. Call it under the runtime geometry lock.
        """
        deadline = self._runtime_map_identity_expires_at
        live_frame = (
            deadline is not None and deadline.timestamp() > time.time()
            and self._observed_map_geometry == (map_index, geometry)
        )
        frame = self._position_frame
        static_station_frame = (
            frame is not None and frame.station_revision is not None
            and (frame.map_index, frame.geometry) == (map_index, geometry)
            and frame.charger == charger
            and self._position_tracker.geometry in (None, geometry)
            and self._observed_map_geometry in (None, (map_index, geometry))
        )
        if not (
            (live_frame or static_station_frame)
            and self._runtime_geometry_map_index in (None, map_index)
            and self._runtime_live_geometry_identity in (None, geometry)
        ):
            return position
        return position_at_charger(
            position, self._latest_snapshot, charger, map_index, time.time(),
            runtime_map_index=self._runtime_live_map_index,
        )

    def _bind_runtime_live_geometry(self, map_index: int, geometry: str) -> None:
        """Discard trails when a verified slot's coordinate frame is replaced."""
        if self._runtime_live_map_index != map_index:
            return
        if self._runtime_live_geometry_identity not in (None, geometry):
            self._runtime_live_track_segments = ()
        self._runtime_live_geometry_identity = geometry
        self._restore_runtime_trail(map_index, geometry)

    def _remember_runtime_trail(self) -> None:
        """Suspend only an observed trail; repeated retirement cannot renew it."""
        if (
            self._runtime_session_active is True
            and self._runtime_live_map_index is not None
            and self._runtime_live_task_id is not None
            and self._runtime_live_geometry_identity is not None
            and self._runtime_live_track_segments
        ):
            suspended_at = datetime.now(UTC).timestamp()
            if self._runtime_map_identity_expires_at is not None:
                # Lazy expiry must not grant a new recovery window to a lease
                # that actually ended long before this reader noticed it.
                suspended_at = min(
                    suspended_at, self._runtime_map_identity_expires_at.timestamp(),
                )
            segments = self._runtime_live_track_segments
            previous = self._runtime_trail_checkpoint
            if (
                previous is not None
                and previous.mission_generation is not None
                and (
                    previous.map_index, previous.task_id, previous.geometry,
                    previous.mission_generation,
                ) == (
                    self._runtime_live_map_index, self._runtime_live_task_id,
                    self._runtime_live_geometry_identity,
                    self._runtime_mission_generation,
                )
            ):
                # An unrecovered earlier part of the same mission stays first.
                segments = (*previous.segments, *segments)
            self._runtime_trail_checkpoint = _RuntimeTrailCheckpoint(
                self._runtime_live_map_index, self._runtime_live_task_id,
                self._runtime_live_geometry_identity,
                bounded_runtime_trail(segments), suspended_at,
                self._runtime_mission_generation,
            )

    def _suspended_trail_for_display(
        self, map_index: int, geometry: str,
    ) -> tuple[tuple[tuple[int, int], ...], ...]:
        """Return the trail suspended a moment ago, for display only.

        A lapsed map lease or an unscoped packet suspends the trail until the
        next verified packet restores it. Between the two, a viewer would see an
        empty map. Within the live window, and only for the same mission, frame
        and slot, readers may show the suspended segments. Nothing is restored.
        """
        checkpoint = self._runtime_trail_checkpoint
        if (
            checkpoint is None
            or self._runtime_live_track_segments
            or self._runtime_session_active is not True
            or checkpoint.mission_generation is None
            or checkpoint.mission_generation != self._runtime_mission_generation
            or checkpoint.map_index != map_index
            or checkpoint.geometry != geometry
            or not 0 <= datetime.now(UTC).timestamp() - checkpoint.suspended_at
            <= POSITION_MAX_AGE_SECONDS
        ):
            return ()
        return checkpoint.segments

    def _restore_runtime_trail(self, map_index: int, geometry: str) -> None:
        """Recover history only with fresh telemetry in its original context."""
        checkpoint = self._runtime_trail_checkpoint
        if checkpoint is None:
            return
        max_age = (
            TRAIL_MISSION_MAX_AGE_SECONDS
            if checkpoint.mission_generation is not None
            else POSITION_MAX_AGE_SECONDS
        )
        if (
            not 0 <= datetime.now(UTC).timestamp() - checkpoint.suspended_at
            <= max_age
            or (
                checkpoint.mission_generation is not None
                and checkpoint.mission_generation != self._runtime_mission_generation
            )
            or map_index != checkpoint.map_index
            or geometry != checkpoint.geometry
            or self._runtime_session_active is not True
        ):
            self._runtime_trail_checkpoint = None
            return
        # Supported short pose packets and missing/invalid callbacks cannot
        # establish a task change. Wait for task-bearing evidence to recover.
        if self._runtime_live_task_id is None:
            return
        if self._runtime_live_task_id != checkpoint.task_id:
            self._runtime_trail_checkpoint = None
            return
        blob = self._latest_runtime_status_blob
        observed_at = position_timestamp(getattr(blob, "received_at", None))
        if (
            not self._runtime_live_packet_admitted
            or not getattr(blob, "frame_valid", False)
            or observed_at is None
            or observed_at <= checkpoint.suspended_at
        ):
            return
        self._runtime_live_track_segments = bounded_runtime_trail((
            *checkpoint.segments, *self._runtime_live_track_segments,
        ))
        self._runtime_trail_checkpoint = None

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
            if deadline is None:
                # Retire against the previous deadline: coordinator revocation
                # must not restart an already elapsed recovery window.
                self._retire_runtime_live_tracking_locked()
            self._runtime_map_identity_expires_at = deadline
            if deadline is not None:
                self._expire_runtime_live_tracking()

    def update_runtime_map_identity(
        self, map_index: int | None, *, expires_at: datetime,
    ) -> None:
        """Atomically bind a host-verified MAPL lease before telemetry arrives.

        A changed or expired scope retires live inputs while preserving labelled
        history. Geometry reads cannot borrow the interval before the next pose.
        """
        with self._runtime_geometry_lock:
            expired = self._expire_runtime_live_tracking()
            if expired or map_index != self._runtime_geometry_map_index:
                self._retire_runtime_live_tracking_locked()
            self._runtime_map_identity_expires_at = (
                expires_at if map_index is not None else None
            )
            self._scope_runtime_geometry(map_index)
            self._expire_runtime_live_tracking()

    def _retire_runtime_live_tracking(self) -> None:
        """Revoke live publication, preserving history and consumed packets."""
        with self._runtime_geometry_lock:
            self._retire_runtime_live_tracking_locked()

    def invalidate_runtime_map_geometry(self) -> None:
        """Require a new geometry observation after an announced app switch.

        A switch can replace a slot's frame. Ordinary lease expiry preserves its
        observed frame for recovery; a switch retains only labelled history.
        """
        with self._runtime_geometry_lock:
            self._retire_runtime_live_tracking_locked()
            self._observed_map_geometry = None
            self._position_frame = None
            self._runtime_geometry_revision += 1

    def _retire_runtime_live_tracking_locked(self) -> None:
        self._remember_runtime_trail()
        # Keep the observed frame for history, but fence reads from the retired
        # scope. Repeated retirement cannot reopen or continually advance it.
        self._scope_runtime_geometry(None)
        self._position_tracker.invalidate_current()
        self._latest_runtime_status_blob = None
        self._runtime_live_packet_admitted = False
        self._runtime_live_track_segments = ()
        self._runtime_live_map_index = None
        self._runtime_live_task_id = None
        self._runtime_live_geometry_identity = None

    def _scope_runtime_geometry(self, map_index: int | None) -> None:
        """Fence pending reads when verified identity changes, under owner lock.

        Geometry scope survives an idle session: trail publication and coordinate
        frame ownership have distinct lifetimes.
        """
        checkpoint = self._runtime_trail_checkpoint
        if (
            checkpoint is not None and map_index is not None
            and checkpoint.map_index != map_index
        ):
            self._runtime_trail_checkpoint = None
        if self._runtime_geometry_map_index != map_index:
            self._runtime_geometry_map_index = map_index
            self._runtime_geometry_revision += 1

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
        mission_generation: int | None = None,
    ) -> None:
        """Publish telemetry against the same lock as geometry observations.

        ``mission_generation`` is the host's mission boundary. When supplied,
        a trail suspended by docking (a recharge) is kept and recovered only if
        the same mission, task and frame resume.
        """
        with self._runtime_geometry_lock:
            if active and mission_generation is not None:
                if (
                    self._runtime_mission_generation is not None
                    and self._runtime_mission_generation != mission_generation
                ):
                    self._runtime_live_track_segments = ()
                    self._runtime_trail_checkpoint = None
                    self._runtime_live_task_id = None
                    self._last_runtime_track_blob_hex = None
                self._runtime_mission_generation = mission_generation
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
        if map_index is not None:
            # An unscoped telemetry callback cannot revoke a fresh MAPL lease.
            # Explicit identity revocation and expiry own that transition.
            self._scope_runtime_geometry(map_index)
        if map_index is None or self._runtime_live_map_index not in (None, map_index):
            self._position_tracker.invalidate_current()
            self._latest_runtime_status_blob = None
        # The position owner consumes unscoped and repeated events. Timestamp
        # freshness alone cannot promote a rejected replay into recovery proof.
        self._runtime_live_packet_admitted = self._position_tracker.record(
            status_blob, map_index=map_index,
        )
        if self._runtime_live_packet_admitted or status_blob is None:
            self._latest_runtime_status_blob = (
                status_blob if map_index is not None else None
            )
        if not active and self._runtime_mission_generation is not None:
            # Docking for a recharge: suspend the observed trail while the
            # session is still marked active, labelled with its mission.
            self._remember_runtime_trail()
        self._runtime_session_active = active
        if not active or map_index is None:
            if active:
                self._remember_runtime_trail()
            elif (
                self._runtime_trail_checkpoint is not None
                and self._runtime_trail_checkpoint.mission_generation is None
            ):
                self._runtime_trail_checkpoint = None
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

        # Rejected telemetry cannot retarget the session or enter the buffer
        # that retirement may later preserve. Host map/idle changes still apply.
        task_id = (
            getattr(status_blob, "candidate_runtime_task_id", None)
            if self._runtime_live_packet_admitted else None
        )
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
            self._runtime_trail_checkpoint = None
        if map_index is not None:
            self._runtime_live_map_index = map_index
        if task_id is not None:
            self._runtime_live_task_id = task_id

        if status_blob is None or not self._runtime_live_packet_admitted:
            return

        observed = self._observed_map_geometry
        if observed is not None and observed[0] == map_index:
            # Fresh reception can restore a cached scene even when raw bytes
            # repeat. Trace append deduplication remains independent below.
            self._bind_runtime_live_geometry(map_index, observed[1])

        blob_hex = getattr(status_blob, "hex", None)
        if blob_hex and blob_hex == self._last_runtime_track_blob_hex:
            return

        segments = getattr(status_blob, "candidate_runtime_track_segments", ()) or ()
        # Isolated points cannot form a line. Their explicit breaks must remain
        # disconnected, and they must not evict useful segments from the budget.
        self._runtime_live_track_segments = (
            *self._runtime_live_track_segments,
            *tuple(
                tuple(tuple(point) for point in segment)
                for segment in segments if len(segment) >= 2
            ),
        )
        self._runtime_live_track_segments = bounded_runtime_trail(
            self._runtime_live_track_segments
        )
        if blob_hex:
            self._last_runtime_track_blob_hex = blob_hex
