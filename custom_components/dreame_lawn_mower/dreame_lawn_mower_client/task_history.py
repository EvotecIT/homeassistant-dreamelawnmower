"""Bounded task history using the existing private session-summary transport."""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from .map_inventory import map_revision_hash
from .session_photos import (
    SESSION_EVENT_KEY,
    SessionPhoto,
    TaskSession,
    _async_read_session_summary,
    session_photo_names,
    session_photo_object_name,
    task_sessions_from_history,
)

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession

MAX_TASKS = 30
MAX_MAP_POINTS = 12_000
MAX_MAP_FEATURES = 200
MAX_HISTORY_BYTES = 4 * 1024 * 1024


def task_id(device_id: str, session: TaskSession) -> str:
    """Keep private storage paths and device identifiers out of public URLs."""
    return hashlib.sha256(f"{device_id}\0{session.object_name}".encode()).hexdigest()[
        :32
    ]


def finite_number(value: Any, maximum: float = 1_000_000_000) -> float | None:
    """Accept bounded JSON numbers, excluding booleans and non-finite values."""
    if type(value) not in (int, float) or not 0 <= value <= maximum:
        return None
    number = float(value)
    return number if math.isfinite(number) and 0 <= number <= maximum else None


def timestamp(value: Any) -> int | None:
    """Accept valid positive Unix seconds without platform datetime conversion."""
    return value if type(value) is int and 0 < value <= 253402300799 else None


def task_mode(value: Any) -> int | None:
    """Normalize the completion-event offset once, before any HA/UI adapter."""
    if type(value) is not int:
        return None
    normalized = value - 100 if value >= 100 else value
    return normalized if 0 <= normalized <= 9 else None


@dataclass(frozen=True, slots=True)
class TaskMapFeature:
    """One polygon or route from this task's summary, in native map units."""

    kind: int | None
    zone_id: int | None
    points: tuple[tuple[int, int], ...]
    tracks: tuple[tuple[tuple[int, int], ...], ...]


@dataclass(frozen=True, slots=True)
class TaskDetail:
    """Safe normalized detail; private summary/photo paths stay in the owner."""

    id: str
    started_at: int | None
    ended_at: int | None
    duration_minutes: float | None
    area_sqm: float | None
    mode: int | None
    result: int | None
    map_revision: str | None
    features: tuple[TaskMapFeature, ...]
    zone_results: tuple[tuple[int, int], ...]
    faults: tuple[tuple[int | None, int], ...]
    photo_count: int
    recorded_settings: Mapping[str, int | float]
    per_zone_settings: bool

    def as_dict(self) -> dict[str, Any]:
        """Serialize only the stable, vendor-independent history contract."""
        return asdict(self)


def _integer(value: Any) -> int | None:
    return value if type(value) is int and abs(value) <= 1_000_000 else None


def _points(value: Any, budget: list[int]) -> tuple[tuple[int, int], ...]:
    if not isinstance(value, list) or len(value) > budget[0]:
        return ()
    points: list[tuple[int, int]] = []
    for point in value:
        if (
            not isinstance(point, list | tuple)
            or len(point) < 2
            or _integer(point[0]) is None
            or _integer(point[1]) is None
        ):
            return ()
        points.append((point[0] * 10, point[1] * 10))
    budget[0] -= len(points)
    return tuple(points)


def _tracks(value: Any, budget: list[int]) -> tuple[tuple[tuple[int, int], ...], ...]:
    """Respect native track breaks so separate mowing lanes are never joined."""
    if not isinstance(value, list) or len(value) > budget[0]:
        return ()
    runs: list[tuple[tuple[int, int], ...]] = []
    run: list[Any] = []
    for point in value:
        if (
            not isinstance(point, list | tuple)
            or len(point) < 2
            or _integer(point[0]) is None
            or _integer(point[1]) is None
        ):
            if run:
                runs.append(_points(run, budget))
                run = []
        else:
            run.append(point)
    if run:
        runs.append(_points(run, budget))
    return tuple(points for points in runs if points)


def decode_task_detail(
    device_id: str,
    session: TaskSession,
    summary: Mapping[str, Any],
) -> TaskDetail:
    """Decode the summary's own geometry; never substitute the current map."""
    start, end = timestamp(summary.get("start")), timestamp(summary.get("end"))
    if start is not None and end is not None and end < start:
        end = None
    features: list[TaskMapFeature] = []
    budget = [MAX_MAP_POINTS]
    entries = summary.get("map")
    for entry in entries[:MAX_MAP_FEATURES] if isinstance(entries, list) else []:
        if not isinstance(entry, Mapping):
            continue
        points = _points(entry.get("data"), budget)
        tracks = _tracks(entry.get("track"), budget)
        if points or tracks:
            features.append(
                TaskMapFeature(
                    _integer(entry.get("type")),
                    _integer(entry.get("id")),
                    points,
                    tracks,
                )
            )
    outcomes = summary.get("region_status")
    zone_results = (
        tuple(
            (row[0], row[1])
            for row in outcomes[:MAX_MAP_FEATURES]
            if isinstance(row, list)
            and len(row) >= 2
            and type(row[0]) is int
            and type(row[1]) is int
        )
        if isinstance(outcomes, list)
        else ()
    )
    faults = summary.get("faults")
    decoded_faults = (
        tuple(
            (timestamp(item[0]), item[1])
            for item in faults[:100]
            if isinstance(item, list | tuple)
            and len(item) >= 2
            and _integer(item[1]) is not None
        )
        if isinstance(faults, list)
        else ()
    )
    settings: dict[str, int | float] = {}
    pref = summary.get("pref")
    if isinstance(pref, list) and len(pref) >= 2:
        height = finite_number(pref[0], 1_000)
        efficiency = _integer(pref[1])
        if height is not None:
            settings["mowing_height_cm"] = height / 10
        if efficiency is not None:
            settings["efficient_mode"] = efficiency
    return TaskDetail(
        task_id(device_id, session),
        start,
        end,
        finite_number(summary.get("time")),
        finite_number(summary.get("areas")),
        task_mode(summary.get("mode", session.task)),
        _integer(summary.get("result")),
        map_revision_hash(summary.get("md5")),
        tuple(features),
        zone_results,
        decoded_faults,
        len(session_photo_names(summary)),
        settings,
        summary.get("pre_type") == 1,
    )


class TaskHistory:
    """One mower's bounded private index and lazily downloaded task details."""

    def __init__(self, client: DreameLawnMowerClient) -> None:
        self.client = client
        self.sessions: OrderedDict[str, TaskSession] = OrderedDict()
        self.details: dict[str, TaskDetail] = {}
        self.photos: dict[str, tuple[SessionPhoto, ...]] = {}
        self.refreshed_at: float | None = None
        self.stale = False
        self.revision = 0

    async def async_refresh(self) -> None:
        """Read at most 100 events and retain the newest 30 distinct sessions."""
        if self.refreshed_at is not None and time.monotonic() - self.refreshed_at < 60:
            return
        deadline = time.monotonic() + 30

        async def read(cloud: DreameCloudSession) -> Any:
            return await cloud.async_get_property_history(
                self.client.descriptor.did,
                SESSION_EVENT_KEY,
                kind="event",
                require_response=True,
                limit=100,
                deadline=deadline,
            )

        records = await self.client._async_cloud_read(read)
        sessions: OrderedDict[str, TaskSession] = OrderedDict()
        for session in task_sessions_from_history(records):
            key = task_id(self.client.descriptor.unique_id, session)
            sessions.setdefault(key, session)
            if len(sessions) == MAX_TASKS:
                break
        self.sessions = sessions
        self.details = {
            key: value for key, value in self.details.items() if key in sessions
        }
        self.photos = {
            key: value for key, value in self.photos.items() if key in sessions
        }
        self.refreshed_at = time.monotonic()
        self.stale = False
        self.revision += 1

    def export(self) -> dict[str, Any]:
        """Persist the bounded private index and normalized historical drawings."""
        tasks = []
        remaining = MAX_HISTORY_BYTES
        for key, session in self.sessions.items():
            row: dict[str, Any] = {"session": asdict(session)}
            detail = self.details.get(key)
            if detail is not None:
                summary = _summary_for_storage(detail, self.photos.get(key, ()))
                size = len(json.dumps(summary, allow_nan=False).encode())
                if size <= remaining:
                    row["summary"] = summary
                    remaining -= size
            tasks.append(row)
        return {"tasks": tasks}

    def restore(self, record: Any) -> None:
        """Revalidate persisted data through the same event and summary decoders."""
        rows = record.get("tasks") if isinstance(record, Mapping) else None
        for row in rows[:MAX_TASKS] if isinstance(rows, list) else []:
            if not isinstance(row, Mapping) or not isinstance(
                row.get("session"), Mapping
            ):
                continue
            saved = row["session"]
            # Values are already normalized here, so use legacy mode units for
            # decoding area, then restore the original mode and result below.
            decoded = task_sessions_from_history(
                [
                    {
                        "history": [
                            {"piid": 1, "value": saved.get("task")},
                            {"piid": 9, "value": saved.get("object_name")},
                        ],
                        "createTime": saved.get("created_at_ms"),
                    }
                ]
            )
            if not decoded:
                continue
            session = TaskSession(
                decoded[0].object_name,
                decoded[0].task,
                decoded[0].created_at_ms,
                timestamp(saved.get("started_at")),
                finite_number(saved.get("duration_minutes")),
                finite_number(saved.get("area_sqm")),
                _integer(saved.get("result")),
                _integer(saved.get("start_mode")),
            )
            key = task_id(self.client.descriptor.unique_id, session)
            self.sessions[key] = session
            summary = row.get("summary")
            if isinstance(summary, Mapping):
                self._retain_detail(key, session, summary)
        self.stale = bool(self.sessions)
        self.revision = 0

    async def async_detail(self, key: str) -> TaskDetail | None:
        """Download only a session already discovered for this mower."""
        if key in self.details:
            return self.details[key]
        session = self.sessions.get(key)
        if session is None:
            return None
        deadline = time.monotonic() + 30

        async def read(cloud: DreameCloudSession) -> Mapping[str, Any] | None:
            return await _async_read_session_summary(
                cloud,
                self.client,
                session.object_name,
                deadline=deadline,
            )

        summary = await self.client._async_cloud_read(read)
        if summary is None:
            return None
        return self._retain_detail(key, session, summary)

    def _retain_detail(
        self,
        key: str,
        session: TaskSession,
        summary: Mapping[str, Any],
    ) -> TaskDetail:
        detail = decode_task_detail(self.client.descriptor.unique_id, session, summary)
        photos = []
        for name in session_photo_names(summary):
            path = session_photo_object_name(session.object_name, name)
            if path is not None:
                photos.append(SessionPhoto(name, path, session))
        self.details[key] = detail
        self.photos[key] = tuple(photos)
        self.revision += 1
        return detail


def _summary_for_storage(
    detail: TaskDetail, photos: tuple[SessionPhoto, ...]
) -> dict[str, Any]:
    """Keep no unknown fields, download URLs, image bytes, or raw packets."""
    entries = []
    for feature in detail.features:
        track: list[list[int]] = []
        for run in feature.tracks:
            if track:
                track.append([2147483647, 2147483647])
            track.extend([[x // 10, y // 10] for x, y in run])
        entries.append(
            {
                "type": feature.kind,
                "id": feature.zone_id,
                "data": [[x // 10, y // 10] for x, y in feature.points],
                "track": track,
            }
        )
    settings = detail.recorded_settings
    return {
        "start": detail.started_at,
        "end": detail.ended_at,
        "time": detail.duration_minutes,
        "areas": detail.area_sqm,
        "mode": detail.mode,
        "result": detail.result,
        "md5": detail.map_revision,
        "map": entries,
        "region_status": list(detail.zone_results),
        "faults": list(detail.faults),
        "pre_type": 1 if detail.per_zone_settings else 0,
        "pref": [
            settings.get("mowing_height_cm", 0) * 10,
            settings.get("efficient_mode"),
        ]
        if "mowing_height_cm" in settings
        else [],
        "photo_list": [photo.file_name for photo in photos],
    }
