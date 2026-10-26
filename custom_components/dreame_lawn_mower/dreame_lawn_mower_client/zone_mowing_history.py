"""Bounded, resumable mowing history associated with verified map revisions."""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .map_inventory import (
    map_revision_hash as map_revision_hash,
)
from .map_inventory import (
    selected_map_revision,
)
from .session_photos import (
    SESSION_EVENT_KEY,
    _async_read_session_summary,
    task_sessions_from_history,
)
from .vector_map import charger_in_bounds

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession

ZONE_HISTORY_SESSIONS = 30
ZONE_HISTORY_DOWNLOADS = 5
ZONE_HISTORY_BUDGET = 15.0
_MAX_TIMESTAMP = 253402300799


def _timestamp(value: Any) -> int | None:
    return value if type(value) is int and 0 <= value <= _MAX_TIMESTAMP else None


@dataclass(frozen=True)
class ZoneMowingRecord:
    """One zone outcome; a missing map revision cannot establish attribution."""

    zone_id: int
    status: int
    started_at: int | None
    ended_at: int | None
    mode: int | None
    session: str
    map_hash: str | None = None

    @property
    def completed(self) -> bool:
        return (
            self.status == 2
            and self.mode in (100, 102)
            and self.started_at is not None
            and self.ended_at is not None
            and self.ended_at >= self.started_at
        )


# Summary coordinates use the coarse MAPD units of the summary's own ``map``
# polygons (centimetres); drawable geometry uses units ten times finer.
_SUMMARY_COORDINATE_SCALE = 10


@dataclass(frozen=True)
class ChargerPosition:
    """Where a finished task reported the charging station, in map units."""

    x: int
    y: int
    heading: int | None
    observed_at: int | None
    map_hash: str | None = None


def charger_position(summary: Any) -> ChargerPosition | None:
    """Decode ``dock: [x, y, heading, ...]`` from one session summary."""
    if not isinstance(summary, Mapping):
        return None
    dock = summary.get("dock")
    if not isinstance(dock, list) or len(dock) < 2:
        return None
    x, y = dock[:2]
    if any(type(v) is not int or abs(v) > 1_000_000 for v in (x, y)):
        return None
    heading = dock[2] if len(dock) > 2 and type(dock[2]) is int else None
    return ChargerPosition(
        x * _SUMMARY_COORDINATE_SCALE,
        y * _SUMMARY_COORDINATE_SCALE,
        heading,
        _timestamp(summary.get("end")) or _timestamp(summary.get("start")),
        map_revision_hash(summary.get("md5")),
    )


@dataclass(frozen=True)
class ZoneGeometry:
    """Lawn zone boundaries one summary reported, in drawable map units."""

    map_hash: str
    zones: Mapping[int, tuple[tuple[int, int], ...]]
    observed_at: int | None = None


def zone_geometry(summary: Any) -> ZoneGeometry | None:
    """Decode the summary's own lawn-zone polygons (``map`` entries of type 0)."""
    if not isinstance(summary, Mapping):
        return None
    revision = map_revision_hash(summary.get("md5"))
    entries = summary.get("map")
    if revision is None or not isinstance(entries, list):
        return None
    zones: dict[int, tuple[tuple[int, int], ...]] = {}
    for entry in entries:
        if not isinstance(entry, Mapping) or entry.get("type") != 0:
            continue
        zone_id, data = entry.get("id"), entry.get("data")
        if type(zone_id) is not int or not isinstance(data, list) or len(data) < 3:
            continue
        points = [
            (point[0] * _SUMMARY_COORDINATE_SCALE, point[1] * _SUMMARY_COORDINATE_SCALE)
            for point in data
            if isinstance(point, list | tuple)
            and len(point) >= 2
            and all(
                type(value) is int and abs(value) <= 1_000_000 for value in point[:2]
            )
        ]
        if len(points) == len(data):
            zones[zone_id] = tuple(points)
    if not zones:
        return None
    return ZoneGeometry(
        revision,
        zones,
        _timestamp(summary.get("end")) or _timestamp(summary.get("start")),
    )


@dataclass(frozen=True)
class ZoneMowingHistory:
    """Successful summaries plus the unfinished hydration queue."""

    sessions: Mapping[str, tuple[ZoneMowingRecord, ...]] = field(default_factory=dict)
    pending: tuple[str, ...] = ()
    history_available: bool = True
    chargers: Mapping[str, ChargerPosition] = field(default_factory=dict)
    geometries: Mapping[str, ZoneGeometry] = field(default_factory=dict)

    def latest_charger(self, map_hash: str | None = None) -> ChargerPosition | None:
        """Newest station on the requested revision, without a foreign fallback."""
        ordered = sorted(
            self.chargers.values(), key=lambda item: item.observed_at or 0, reverse=True
        )
        revision = map_revision_hash(map_hash)
        for item in ordered:
            if revision is not None and item.map_hash == revision:
                return item
        return ordered[0] if revision is None and ordered else None

    def lineage_revision(
        self, zones: Iterable[tuple[int, Sequence[tuple[int, int]]]],
    ) -> str | None:
        """Newest summarized revision sharing an unchanged lawn zone with ``zones``.

        Each app edit saves a new revision, and untouched zones keep their
        boundary point for point. A re-created map or another slot does not
        reproduce an exact boundary, whatever its station coordinates, so it
        has no lineage.
        """
        current = {
            zone_id: tuple(tuple(point) for point in points)
            for zone_id, points in zones
            if len(points) >= 3
        }
        matches = [
            geometry
            for geometry in self.geometries.values()
            if any(
                current.get(zone_id) == points
                for zone_id, points in geometry.zones.items()
            )
        ]
        if not matches:
            return None
        return max(matches, key=lambda geometry: geometry.observed_at or 0).map_hash

    @property
    def complete(self) -> bool:
        return self.history_available and not self.pending

    @property
    def attributable(self) -> bool:
        return all(
            record.map_hash is not None
            for records in self.sessions.values()
            for record in records
        )


def reported_charger_for_map(
    history: ZoneMowingHistory | None,
    app_maps: Any,
    map_index: int,
    lineage: Mapping[str, str] | None = None,
) -> ChargerPosition | None:
    """Bind summary coordinates to one current, uniquely verified revision.

    A revision without its own summary uses its verified ancestor, recorded by
    ``record_map_lineage`` from the current geometry; nothing else carries over.
    """
    revision = selected_map_revision(app_maps, map_index)
    if history is None or revision is None:
        return None
    own = history.latest_charger(revision)
    if own is not None:
        return own
    ancestor = (lineage or {}).get(revision)
    return history.latest_charger(ancestor) if ancestor else None


def record_map_lineage(
    lineage: dict[str, str],
    history: ZoneMowingHistory | None,
    app_maps: Any,
    map_index: int,
    zones: Iterable[tuple[int, int, Sequence[tuple[int, int]]]],
) -> None:
    """Decide once, from accepted current geometry, which revision this one continues.

    ``zones`` are (zone_id, zone_type, points); only lawn zones (type 0) count.
    Paths without geometry read the decision through ``reported_charger_for_map``.
    """
    revision = selected_map_revision(app_maps, map_index)
    if revision is None:
        return
    ancestor = (
        history.lineage_revision(
            (zone_id, points) for zone_id, zone_type, points in zones if zone_type == 0
        )
        if history is not None
        else None
    )
    if ancestor is None or ancestor == revision:
        lineage.pop(revision, None)
    else:
        lineage[revision] = ancestor


def lineage_render_matches_history(
    before: ZoneMowingHistory | None,
    after: ZoneMowingHistory | None,
    rendered: tuple[Any, Any] | None,
    current: tuple[int, int] | None,
    bounds: tuple[int, int, int, int] | None,
) -> bool:
    """Accept a discovered charger only from unchanged history and matching pixels.

    Callers separately require an initially unknown station and unchanged map,
    presentation and switch context. A real history update must still invalidate.
    """
    return before is not None and before is after and current is not None and (
        rendered == charger_in_bounds(current, bounds)
    )


@dataclass(frozen=True)
class ZoneMowingSummary:
    """Keep irrelevant outcomes distinct from unsupported or partial decoding."""

    records: tuple[ZoneMowingRecord, ...] = ()
    understood: bool = False


def zone_mowing_records(
    summary: Any,
    session_object_name: str,
) -> ZoneMowingSummary:
    """Decode whole-zone runs; edge, spot and patrol outcomes are separate."""
    if not isinstance(summary, Mapping) or type(summary.get("mode")) is not int:
        return ZoneMowingSummary()
    if summary["mode"] in (101, 103, 104, 107, 108):
        return ZoneMowingSummary(understood=True)
    if summary["mode"] not in (100, 102):
        return ZoneMowingSummary()
    statuses = summary.get("region_status")
    if not isinstance(statuses, list):
        return ZoneMowingSummary()
    records = []
    seen: set[int] = set()
    understood = True
    for item in statuses:
        if not isinstance(item, list | tuple) or len(item) < 2:
            understood = False
            continue
        zone_id, status = item[:2]
        if (
            type(zone_id) is not int
            or type(status) is not int
            or zone_id <= 0
            or zone_id in seen
        ):
            understood = False
            continue
        seen.add(zone_id)
        records.append(
            ZoneMowingRecord(
                zone_id,
                status,
                _timestamp(summary.get("start")),
                _timestamp(summary.get("end")),
                summary["mode"],
                session_object_name,
                map_revision_hash(summary.get("md5")),
            )
        )
        if status == 2 and not records[-1].completed:
            understood = False
    return ZoneMowingSummary(tuple(records), understood)


async def async_get_zone_mowing_records(
    client: DreameLawnMowerClient,
    *,
    known: ZoneMowingHistory | None = None,
    deadline: float | None = None,
) -> ZoneMowingHistory:
    """Hydrate at most five summaries per pass and preserve successful progress."""
    known = known or ZoneMowingHistory()
    deadline = min(deadline or float("inf"), time.monotonic() + ZONE_HISTORY_BUDGET)
    descriptor = client._descriptor

    async def read(cloud: DreameCloudSession) -> ZoneMowingHistory:
        records = await cloud.async_get_property_history(
            descriptor.did,
            SESSION_EVENT_KEY,
            kind="event",
            require_response=True,
            limit=ZONE_HISTORY_SESSIONS,
            deadline=deadline,
        )
        if not isinstance(records, list):
            raise ValueError("Session history was not a list")
        names = list(
            dict.fromkeys(
                session.object_name
                for session in task_sessions_from_history(records)[
                    :ZONE_HISTORY_SESSIONS
                ]
            )
        )
        result = {
            name: known.sessions[name] for name in names if name in known.sessions
        }
        chargers = {
            name: known.chargers[name] for name in names if name in known.chargers
        }
        geometries = {
            name: known.geometries[name] for name in names if name in known.geometries
        }
        resolved = {name for name in result if name not in known.pending}
        missing = [name for name in names if name not in resolved]
        # Untried new summaries first; retain a rotated queue across partial passes.
        pending = [name for name in missing if name not in known.pending]
        pending.extend(name for name in known.pending if name in missing)
        attempted: list[str] = []
        for name in pending[:ZONE_HISTORY_DOWNLOADS]:
            if time.monotonic() >= deadline:
                break
            attempted.append(name)
            try:
                summary = await _async_read_session_summary(
                    cloud,
                    client,
                    name,
                    deadline=deadline,
                )
            except Exception:  # noqa: BLE001 - optional history retains partial progress
                # Cancellation is BaseException and must propagate to its owner.
                break
            if summary is not None:
                charger = charger_position(summary)
                if charger is not None:
                    chargers[name] = charger
                geometry = zone_geometry(summary)
                if geometry is not None:
                    geometries[name] = geometry
                decoded = zone_mowing_records(summary, name)
                result[name] = decoded.records
                if decoded.understood:
                    resolved.add(name)
        unfinished = [name for name in pending if name not in resolved]
        # One unavailable old object cannot permanently starve the remaining queue.
        unfinished = [name for name in unfinished if name not in attempted] + [
            name for name in unfinished if name in attempted
        ]
        return ZoneMowingHistory(
            result, tuple(unfinished), chargers=chargers, geometries=geometries
        )

    return await client._async_cloud_read(read)


def latest_zone_mowing(
    sessions: Mapping[str, tuple[ZoneMowingRecord, ...]],
    *,
    map_hash: str | None = None,
) -> dict[int, dict[str, ZoneMowingRecord | None]]:
    """Return outcomes only for the requested verified map revision."""
    revision = map_revision_hash(map_hash)
    if revision is None:
        return {}
    result: dict[int, dict[str, ZoneMowingRecord | None]] = {}
    ordered = sorted(
        (
            record
            for records in sessions.values()
            for record in records
            if record.map_hash == revision
        ),
        key=lambda record: (
            record.ended_at if record.ended_at is not None else record.started_at or 0
        ),
        reverse=True,
    )
    for record in ordered:
        entry = result.setdefault(record.zone_id, {"latest": record, "completed": None})
        if entry["completed"] is None and record.completed:
            entry["completed"] = record
    return result
