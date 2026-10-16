"""Bounded, resumable mowing history associated with verified map revisions."""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .session_photos import (
    SESSION_EVENT_KEY,
    _async_read_session_summary,
    task_sessions_from_history,
)

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession

ZONE_HISTORY_SESSIONS = 30
ZONE_HISTORY_DOWNLOADS = 5
ZONE_HISTORY_BUDGET = 15.0
_MAP_HASH = re.compile(r"[0-9a-fA-F]{32}")
_MAX_TIMESTAMP = 253402300799


def map_revision_hash(value: Any) -> str | None:
    """Accept an explicit map revision, never a zone number as map identity."""
    return (
        value.lower() if isinstance(value, str) and _MAP_HASH.fullmatch(value) else None
    )


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


@dataclass(frozen=True)
class ZoneMowingHistory:
    """Successful summaries plus the unfinished hydration queue."""

    sessions: Mapping[str, tuple[ZoneMowingRecord, ...]] = field(default_factory=dict)
    pending: tuple[str, ...] = ()
    history_available: bool = True

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


def zone_mowing_records(
    summary: Any,
    session_object_name: str,
) -> tuple[ZoneMowingRecord, ...]:
    """Decode whole-zone runs; edge, spot and patrol outcomes are separate."""
    if (
        not isinstance(summary, Mapping)
        or type(summary.get("mode")) is not int
        or summary.get("mode") not in (100, 102)
    ):
        return ()
    statuses = summary.get("region_status")
    if not isinstance(statuses, list):
        return ()
    records = []
    seen: set[int] = set()
    for item in statuses:
        if not isinstance(item, list | tuple) or len(item) < 2:
            continue
        zone_id, status = item[:2]
        if (
            type(zone_id) is not int
            or type(status) is not int
            or zone_id <= 0
            or zone_id in seen
        ):
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
    return tuple(records)


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
        missing = [name for name in names if name not in result]
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
                result[name] = zone_mowing_records(summary, name)
        unfinished = [name for name in pending if name not in result]
        # One unavailable old object cannot permanently starve the remaining queue.
        unfinished = [name for name in unfinished if name not in attempted] + [
            name for name in unfinished if name in attempted
        ]
        return ZoneMowingHistory(result, tuple(unfinished))

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
