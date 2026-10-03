"""Bounded outcome records for explicitly requested unattended starts."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from .session_checkpoint import MAX_RUN_AGE_SECONDS

HISTORY_LIMIT = 5
TARGET_LIMIT = 16


class ScheduledRunHistory:
    """Keep command outcomes without treating acknowledgement as completion."""

    def __init__(self) -> None:
        self._records: deque[dict[str, Any]] = deque(maxlen=HISTORY_LIMIT)

    def record(
        self,
        task_type: str,
        status: str,
        reason: str,
        *,
        map_index: int | None = None,
        targets: list[Any] | None = None,
        detail: str | None = None,
        observed_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Capture a stable scope and bound retained target IDs explicitly."""
        targets = targets or []
        result = {
            "task_type": task_type,
            "status": status,
            "reason": reason,
            "observed_at": (observed_at or datetime.now(UTC)).isoformat(),
            "map_index": map_index,
            "target_ids": deepcopy(targets[:TARGET_LIMIT]),
            "target_count": len(targets),
            "targets_truncated": len(targets) > TARGET_LIMIT,
        }
        if detail:
            result["detail"] = detail[:255]
        self._records.appendleft(result)
        return deepcopy(result)

    def recent(self) -> list[dict[str, Any]]:
        """Return isolated copies, newest first."""
        return deepcopy(list(self._records))

    def latest(self) -> dict[str, Any] | None:
        """Return the most recent command outcome, including a skipped start."""
        return deepcopy(self._records[0]) if self._records else None

    def restore_checkpoint(self, records: Any, *, now: datetime) -> None:
        """Restore only bounded, typed outcomes within the retention window."""
        if not isinstance(records, list) or len(records) > HISTORY_LIMIT:
            return
        accepted = []
        for record in records:
            if _valid_record(record, now):
                accepted.append(deepcopy(record))
        self._records = deque(accepted, maxlen=HISTORY_LIMIT)


def _valid_record(record: Any, now: datetime) -> bool:
    required = {
        "task_type",
        "status",
        "reason",
        "observed_at",
        "map_index",
        "target_ids",
        "target_count",
        "targets_truncated",
    }
    if not isinstance(record, dict) or set(record) not in (
        required,
        required | {"detail"},
    ):
        return False
    if (
        record["task_type"] not in ("all", "zone", "spot", "edge")
        or record["status"] not in ("skipped", "failed", "submitted", "started")
        or not isinstance(record["reason"], str)
        or not 1 <= len(record["reason"]) <= 100
        or not all(
            char.isascii() and (char.isalnum() or char == "_")
            for char in record["reason"]
        )
        or (record["map_index"] is not None and not _valid_id(record["map_index"]))
        or type(record["target_count"]) is not int
        or not 0 <= record["target_count"] <= 65535
        or type(record["targets_truncated"]) is not bool
        or not isinstance(record["target_ids"], list)
        or len(record["target_ids"]) != min(record["target_count"], TARGET_LIMIT)
        or record["targets_truncated"] != (record["target_count"] > TARGET_LIMIT)
        or (
            "detail" in record
            and (not isinstance(record["detail"], str) or len(record["detail"]) > 255)
        )
    ):
        return False
    for target in record["target_ids"]:
        if record["task_type"] == "edge":
            if (
                not isinstance(target, list)
                or len(target) != 2
                or not all(_valid_id(part) for part in target)
            ):
                return False
        elif not _valid_id(target):
            return False
    try:
        observed = datetime.fromisoformat(record["observed_at"])
        return (
            observed.tzinfo is not None
            and 0 <= (now - observed).total_seconds() <= MAX_RUN_AGE_SECONDS
        )
    except (ValueError, TypeError, OverflowError):
        return False


def _valid_id(value: Any) -> bool:
    return type(value) is int and 0 <= value <= 65535
