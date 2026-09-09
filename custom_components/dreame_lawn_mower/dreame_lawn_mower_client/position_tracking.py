"""Map-scoped live, last-known and observed dock positions."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from threading import RLock
from typing import Any

POSITION_MAX_AGE_SECONDS = 90
LAST_POSITION_MAX_AGE_SECONDS = 24 * 60 * 60
DOCK_POSITION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60


def position_timestamp(value: Any) -> float | None:
    """Normalize an observation timestamp without accepting naive dates."""
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.timestamp() if parsed.tzinfo is not None else None
        except (ValueError, OverflowError):
            return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            timestamp = float(value)
            return timestamp if math.isfinite(timestamp) else None
        except OverflowError:
            return None
    return None


def snapshot_is_docked(snapshot: Any) -> bool:
    """Charging and charging-completed both establish physical docking.

    This is state evidence only; it never manufactures dock coordinates.
    """
    return bool(
        snapshot is not None
        and getattr(snapshot, "available", False)
        and getattr(snapshot, "activity", None) not in {"mowing", "returning"}
        and (
            getattr(snapshot, "docked", False)
            or getattr(snapshot, "charging", False)
            or getattr(snapshot, "state", None) in {"charging", "charging_completed"}
        )
    )


def map_position_identity(vector_map: Any) -> str:
    """Invalidate retained locations when a map slot's geometry is replaced."""
    boundary = vector_map.boundary
    geometry = (
        vector_map.map_index,
        vector_map.map_id,
        (boundary.x1, boundary.y1, boundary.x2, boundary.y2) if boundary else None,
        tuple((zone.zone_id, zone.points) for zone in vector_map.zones),
        tuple((path.path_id, path.points) for path in vector_map.paths),
    )
    return hashlib.sha256(repr(geometry).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class MowerPosition:
    """A native-coordinate position with the time and origin of its evidence."""

    x: int
    y: int
    heading: float | None
    observed_at: float
    map_index: int
    status: str = "current"

    def details(self) -> dict[str, Any]:
        """Metadata shared by camera and interactive-map consumers."""
        return {
            "position_status": self.status,
            "position_observed_at": datetime.fromtimestamp(
                self.observed_at, UTC
            ).isoformat(),
            "position_source": "observed_docked_pose"
            if self.status == "known_dock"
            else "runtime_telemetry",
        }


class MowerPositionTracker:
    """Retain validated positions without reusing trails as dock evidence.

    Coordinates are bound to the map observed when their packet arrived, then
    validated against its geometry. A bounded checkpoint may retain one map's
    historical evidence, but it can never restore a live input packet.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._input: MowerPosition | None = None
        self._last: MowerPosition | None = None
        self._dock: MowerPosition | None = None
        self._last_event: float | None = None
        self._geometry: str | None = None
        self._geometry_changed_at: float | None = None

    def checkpoint(self, *, now: datetime | None = None) -> dict[str, Any] | None:
        """Export at most two validated poses and a geometry fingerprint."""
        current = (now or datetime.now(UTC)).timestamp()
        with self._lock:
            if self._geometry is None:
                return None
            last = self._last
            dock = self._dock
            if (
                last
                and not 0 <= current - last.observed_at <= LAST_POSITION_MAX_AGE_SECONDS
            ):
                last = None
            if (
                dock
                and not 0 <= current - dock.observed_at <= DOCK_POSITION_MAX_AGE_SECONDS
            ):
                dock = None
            if last is None and dock is None:
                return None
            return {
                "geometry": self._geometry,
                "last": asdict(replace(last, status="last_known", heading=None))
                if last
                else None,
                "dock": asdict(dock) if dock else None,
            }

    def restore_checkpoint(self, record: Any, *, now: datetime | None = None) -> None:
        """Restore history only; fresh map geometry must match before display."""
        if not isinstance(record, dict) or set(record) != {"geometry", "last", "dock"}:
            return
        geometry = record["geometry"]
        if (
            not isinstance(geometry, str)
            or len(geometry) != 64
            or any(char not in "0123456789abcdef" for char in geometry)
        ):
            return
        current = (now or datetime.now(UTC)).timestamp()
        last = _checkpoint_position(record["last"], "last_known", current)
        dock = _checkpoint_position(record["dock"], "known_dock", current)
        with self._lock:
            # Setup must load before live callbacks begin. Never overwrite live
            # evidence if a late or repeated restore reaches this owner.
            if self._input is not None or self._geometry is not None:
                return
            self._geometry = geometry
            self._last, self._dock = last, dock
            self._last_event = max(
                (pose.observed_at for pose in (last, dock) if pose is not None),
                default=None,
            )

    def invalidate_current(self) -> None:
        """Retire live input while preserving scoped historical position evidence."""
        with self._lock:
            self._input = None

    def record(
        self, blob: Any, *, map_index: int | None, now: datetime | None = None
    ) -> None:
        """Bind a new position packet to its already-verified app map slot."""
        if blob is None or not getattr(blob, "frame_valid", False):
            return
        observed = position_timestamp(getattr(blob, "received_at", None))
        current = (now or datetime.now(UTC)).timestamp()
        if observed is None or observed > current + 5:
            return
        x, y = (
            getattr(blob, "candidate_runtime_pose_x", None),
            getattr(blob, "candidate_runtime_pose_y", None),
        )
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (x, y)):
            return
        with self._lock:
            if self._last_event is not None and observed <= self._last_event:
                return
            self._last_event = observed
            # Do not bind this same cached packet to a newly selected map later.
            if map_index is None:
                self._input = None
                return
            heading = getattr(blob, "candidate_runtime_heading_deg", None)
            if (
                not isinstance(heading, (int, float))
                or isinstance(heading, bool)
                or not math.isfinite(heading)
            ):
                heading = None
            self._input = MowerPosition(x, y, heading, observed, map_index)

    def resolve(
        self,
        *,
        map_index: int,
        geometry: str,
        contains: Callable[[int, int], bool],
        snapshot: Any,
        now: datetime | None = None,
    ) -> MowerPosition | None:
        """Prefer current telemetry, or proven docking, then labelled history."""
        current = (now or datetime.now(UTC)).timestamp()
        with self._lock:
            owned_position = self._input or self._last or self._dock
            if owned_position is not None and owned_position.map_index != map_index:
                # A projection is not a map switch. Only fresh, map-bound input
                # can move the live owner and its historical geometry context.
                return None
            if self._geometry != geometry:
                if self._geometry is not None:
                    self._geometry_changed_at = current
                self._geometry = geometry
                self._last = self._dock = None
            position = self._input
            docked = snapshot_is_docked(snapshot)
            # A docked heartbeat can precede the physical charging property.
            # Its flag must not borrow an older returning/paused timestamp to
            # promote a pre-arrival position into observed dock coordinates.
            docked_at = (
                position_timestamp(getattr(snapshot, "state_event_at", None))
                if getattr(snapshot, "state", None)
                in {"charging", "charging_completed"}
                else None
            )
            if (
                position is not None
                and position.map_index == map_index
                and -5 <= current - position.observed_at <= POSITION_MAX_AGE_SECONDS
                and (
                    self._geometry_changed_at is None
                    or position.observed_at >= self._geometry_changed_at
                )
                and contains(position.x, position.y)
            ):
                self._last = position
                # A pose recorded before returning to the station is not the
                # dock. Require an ordered pose while the device is docked.
                if (
                    docked
                    and docked_at is not None
                    and docked_at <= position.observed_at
                ):
                    self._dock = replace(position, status="known_dock")
                if not docked and getattr(snapshot, "available", False):
                    return position
            if docked and self._dock is not None:
                if (
                    self._dock.map_index == map_index
                    and contains(self._dock.x, self._dock.y)
                    and 0
                    <= current - self._dock.observed_at
                    <= DOCK_POSITION_MAX_AGE_SECONDS
                ):
                    return self._dock
            if self._last is not None:
                if (
                    self._last.map_index == map_index
                    and contains(self._last.x, self._last.y)
                    and 0
                    <= current - self._last.observed_at
                    <= LAST_POSITION_MAX_AGE_SECONDS
                ):
                    return replace(self._last, status="last_known", heading=None)
            return None


def _checkpoint_position(record: Any, status: str, now: float) -> MowerPosition | None:
    """Validate untrusted persisted pose scalars before they enter the tracker."""
    if not isinstance(record, dict) or set(record) != {
        "x",
        "y",
        "heading",
        "observed_at",
        "map_index",
        "status",
    }:
        return None
    age_limit = (
        DOCK_POSITION_MAX_AGE_SECONDS
        if status == "known_dock"
        else LAST_POSITION_MAX_AGE_SECONDS
    )
    observed = position_timestamp(record["observed_at"])
    if (
        record["status"] != status
        or observed is None
        or not 0 <= now - observed <= age_limit
        or not all(
            type(record[key]) is int and abs(record[key]) <= 2**31 - 1
            for key in ("x", "y")
        )
        or type(record["map_index"]) is not int
        or not 0 <= record["map_index"] <= 65535
    ):
        return None
    heading = record["heading"]
    if heading is not None and (
        type(heading) not in (int, float)
        or not 0 <= heading <= 360
        or not math.isfinite(heading)
    ):
        return None
    return MowerPosition(
        record["x"], record["y"], heading, observed, record["map_index"], status
    )
