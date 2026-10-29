"""Sparse battery observations and immutable first-observed task settings."""

from __future__ import annotations

import math
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from .map_inventory import map_revision_hash
from .mowing_preferences import MOWING_PREFERENCE_UPDATE_FIELDS
from .task_history import TaskDetail, finite_number, timestamp

MAX_OBSERVED_RUNS = 30
MAX_RUN_SAMPLES = 500
MAX_SAMPLE_GAP = 300
MAX_RUN_AGE = 90 * 86400


@dataclass(frozen=True, slots=True)
class BatterySample:
    """One reading; charging and offline gaps must not count as mowing use."""

    at: int
    level: int
    charging: bool
    active: bool
    continuous: bool = True


@dataclass(slots=True)
class ObservedTask:
    """A mission identified by the existing runtime owner's boundary."""

    key: str
    first_seen: int
    map_revision: str | None
    settings: dict[str, Any]
    samples: list[BatterySample] = field(default_factory=list)
    settings_observed_at: int | None = None
    pending_break: bool = False


def capture_settings(preferences: Any, map_index: int | None) -> dict[str, Any]:
    """Keep only decoded, current-map settings, without raw packets or text."""
    maps = preferences.get("maps") if isinstance(preferences, Mapping) else None
    selected = (
        [
            row
            for row in maps
            if isinstance(row, Mapping) and row.get("idx") == map_index
        ]
        if isinstance(maps, list)
        else []
    )
    if (
        type(map_index) is not int
        or not 0 <= map_index <= 255
        or len(selected) != 1
        or selected[0].get("available") is not True
    ):
        return {}
    rows = selected[0].get("preferences")
    entries: list[dict[str, Any]] = []
    for row in rows[:200] if isinstance(rows, list) else []:
        if not isinstance(row, Mapping):
            continue
        entry: dict[str, Any] = {}
        for key in ("area_id", "version", *MOWING_PREFERENCE_UPDATE_FIELDS):
            value = row.get(key)
            if isinstance(value, bool | int | float) and (
                type(value) is bool or finite_number(abs(value)) is not None
            ):
                entry[key] = value
        if entry:
            entries.append(entry)
    mode = selected[0].get("mode")
    return {
        "map_index": map_index,
        "mode": mode if type(mode) is int else None,
        "zones": entries,
    }


class TaskObservations:
    """Bound observations independently of HA, its recorder, and cloud transport."""

    def __init__(self) -> None:
        self.runs: dict[str, ObservedTask] = {}
        self._current_key: str | None = None

    def interrupt(self) -> bool:
        """Preserve a discontinuity even when no valid battery can be sampled."""
        changed = False
        for run in self.runs.values():
            if (not run.samples or run.samples[-1].active) and not run.pending_break:
                run.pending_break = True
                changed = True
        return changed

    def observe(
        self,
        *,
        key: str | None,
        at: int,
        level: Any,
        charging: bool,
        active: bool,
        map_revision: str | None,
        settings: dict[str, Any],
    ) -> bool:
        """Save changed readings or a five-minute coverage anchor, never zeros."""
        if key is None or len(key) > 60 or timestamp(at) is None:
            return self.interrupt()
        revision = map_revision_hash(map_revision)
        storage_key = f"{key}|{revision or 'unverified'}"
        changed = False
        if storage_key != self._current_key:
            changed = self.interrupt()
            self._current_key = storage_key
        run = self.runs.get(storage_key)
        if run is None:
            if not active:
                return False
            run = self.runs[storage_key] = ObservedTask(
                storage_key,
                at,
                revision,
                deepcopy(settings) if revision is not None else {},
                settings_observed_at=at if settings and revision is not None else None,
            )
            changed = True
        if (
            run.settings_observed_at is None
            and settings
            and active
            and revision is not None
        ):
            run.settings = deepcopy(settings)
            run.settings_observed_at = at
            changed = True
        self._prune(at)
        if type(level) is not int or not 0 <= level <= 100:
            return self.interrupt() or changed
        previous = run.samples[-1] if run.samples else None
        if not active and previous is not None and not previous.active:
            return changed
        if previous is not None and (
            at <= previous.at
            or (
                (level, charging, active)
                == (previous.level, previous.charging, previous.active)
                and at - previous.at < MAX_SAMPLE_GAP // 2
                and not run.pending_break
            )
        ):
            return changed
        run.samples.append(
            BatterySample(
                at,
                level,
                charging,
                active,
                previous is not None and not run.pending_break,
            )
        )
        run.pending_break = False
        if len(run.samples) > MAX_RUN_SAMPLES:
            del run.samples[:-MAX_RUN_SAMPLES]
        return True

    def _prune(self, at: int) -> None:
        ordered = sorted(
            self.runs.values(), key=lambda item: item.first_seen, reverse=True
        )
        self.runs = {
            item.key: item
            for item in ordered[:MAX_OBSERVED_RUNS]
            if at - item.first_seen <= MAX_RUN_AGE
        }

    def for_task(self, detail: TaskDetail) -> dict[str, Any] | None:
        """Attach disjoint observation segments on the task's verified revision.

        The byte-sized runtime identity is not a cloud session identifier.
        Matching therefore requires containment and disjoint intervals, never
        a nearest timestamp or today's map/settings. Partial coverage stays
        explicitly partial, including a restart, late join, or missing polls.
        """
        start, end = detail.started_at, detail.ended_at
        if start is None or end is None:
            return None
        candidates = [
            run
            for run in self.runs.values()
            if start - 5 <= run.first_seen <= end
            and max(
                (sample.at for sample in run.samples if sample.active),
                default=run.first_seen,
            )
            <= end + 5
            and detail.map_revision is not None
            and run.map_revision == detail.map_revision
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda run: run.first_seen)
        for before, after in zip(candidates, candidates[1:], strict=False):
            last = max(
                (sample.at for sample in before.samples), default=before.first_seen
            )
            if after.first_seen <= last:
                return None
        samples: list[BatterySample] = []
        for run in candidates:
            segment = [sample for sample in run.samples if start <= sample.at <= end]
            if segment:
                segment[0] = replace(segment[0], continuous=False)
                samples.extend(segment)
        samples = samples[-MAX_RUN_SAMPLES:]
        result = battery_observation(
            samples, start=start, end=end, area=detail.area_sqm
        )
        captured = next(
            (
                run
                for run in candidates
                if run.settings_observed_at is not None and run.settings
            ),
            None,
        )
        settings_at = captured.settings_observed_at if captured else None
        result["settings"] = deepcopy(captured.settings) if captured else {}
        result["settings_observed_at"] = settings_at
        result["settings_timing"] = (
            "unavailable"
            if settings_at is None
            else "at_start"
            if abs(settings_at - start) <= 5
            else "first_observed"
        )
        return result

    def export(self) -> dict[str, Any]:
        """Return bounded JSON data for the host's private atomic store."""
        return {"runs": [asdict(run) for run in self.runs.values()]}

    def restore(self, record: Any, *, now: int) -> None:
        """Treat disk data as untrusted and keep only valid, bounded evidence."""
        restored: dict[str, ObservedTask] = {}
        rows = record.get("runs") if isinstance(record, Mapping) else None
        for row in rows[:MAX_OBSERVED_RUNS] if isinstance(rows, list) else []:
            if not isinstance(row, Mapping):
                continue
            key, first = row.get("key"), timestamp(row.get("first_seen"))
            if (
                not isinstance(key, str)
                or len(key) > 100
                or first is None
                or not 0 <= now - first <= MAX_RUN_AGE
            ):
                continue
            raw_settings = row.get("settings")
            # Revalidate through the same whitelist used for live settings.
            settings = (
                capture_settings(
                    {
                        "maps": [
                            {
                                "idx": raw_settings.get("map_index"),
                                "available": True,
                                "mode": raw_settings.get("mode"),
                                "preferences": raw_settings.get("zones"),
                            }
                        ]
                    },
                    raw_settings.get("map_index"),
                )
                if isinstance(raw_settings, dict)
                else {}
            )
            samples: list[BatterySample] = []
            raw_samples = row.get("samples")
            for sample in (
                raw_samples[:MAX_RUN_SAMPLES] if isinstance(raw_samples, list) else []
            ):
                if not isinstance(sample, Mapping):
                    continue
                at, level = timestamp(sample.get("at")), sample.get("level")
                if (
                    at is not None
                    and first <= at <= now
                    and (not samples or at > samples[-1].at)
                    and type(level) is int
                    and 0 <= level <= 100
                    and type(sample.get("charging")) is bool
                    and type(sample.get("active")) is bool
                ):
                    samples.append(
                        BatterySample(
                            at,
                            level,
                            sample["charging"],
                            sample["active"],
                            sample.get("continuous") is True,
                        )
                    )
            settings_at = timestamp(row.get("settings_observed_at"))
            if samples or settings:
                restored[key] = ObservedTask(
                    key,
                    first,
                    map_revision_hash(row.get("map_revision")),
                    settings,
                    samples,
                    settings_at
                    if settings_at is not None
                    and first <= settings_at <= now
                    and settings
                    else None,
                    True,  # A restart never establishes continuous observation.
                )
        self.runs = restored
        self._current_key = None


def battery_observation(
    samples: list[BatterySample],
    *,
    start: int,
    end: int,
    area: float | None,
    source: str = "observed",
) -> dict[str, Any]:
    """Calculate measured drops only across short, non-charging intervals."""
    used, covered, charges = 0, 0, 0
    for before, after in zip(samples, samples[1:], strict=False):
        gap = after.at - before.at
        if after.level > before.level:
            charges += 1
        if (
            0 < gap <= MAX_SAMPLE_GAP
            and before.active
            and after.active
            and after.continuous
            and not before.charging
            and not after.charging
            and after.level <= before.level
        ):
            used += before.level - after.level
            covered += gap
    span = end - start
    complete = bool(
        samples
        and span > 0
        and samples[0].at - start <= 5
        and end - samples[-1].at <= 5
        and covered >= span - 10
    )
    return {
        "source": source,
        "coverage": "complete" if complete else "partial",
        "covered_seconds": covered,
        "percentage_points": used if covered else None,
        "percentage_points_per_sqm": (
            round(used / area, 4)
            if covered and area is not None and math.isfinite(area) and area > 0
            else None
        ),
        "charging_rises": charges,
        "samples": [asdict(sample) for sample in samples],
    }
