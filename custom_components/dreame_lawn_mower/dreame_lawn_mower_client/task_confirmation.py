"""Evidence for acknowledged task starts before native targets are published."""

from __future__ import annotations

from .models import DreameLawnMowerSnapshot
from .mowing_tasks import MOWING_TASK_SPOT, MOWING_TASK_ZONE


def targeted_task_conflicts(
    snapshot: DreameLawnMowerSnapshot,
    expected_operation: int,
    requested_target_ids: frozenset[int] | None,
) -> bool:
    """Return whether published native metadata contradicts the request."""
    operation = getattr(snapshot, "task_operation", None)
    if operation is not None and operation != expected_operation:
        return True
    if requested_target_ids is None:
        return False
    if expected_operation == MOWING_TASK_ZONE:
        target_ids = getattr(snapshot, "task_region_ids", None)
    elif expected_operation == MOWING_TASK_SPOT:
        target_ids = getattr(snapshot, "task_area_ids", None)
    else:
        return False
    return target_ids is not None and frozenset(target_ids) != requested_target_ids


def acknowledged_task_started_without_metadata(
    snapshot: DreameLawnMowerSnapshot,
    baseline: DreameLawnMowerSnapshot,
) -> bool:
    """Recognize a fresh inactive-to-mowing transition with no target metadata.

    This confirms an acknowledged start, not its exact targets. Callers must
    exclude interrupted commands, failed readbacks and observed contradictions.
    A cached heartbeat or an already-active baseline cannot authorize fallback.
    """
    return bool(
        baseline.mowing_session_active is False
        and baseline.activity in {"docked", "idle"}
        and snapshot.task_status_source == "heartbeat_property_read"
        and snapshot.mowing_session_active is True
        and snapshot.task_status in {"starting", "mowing"}
        and snapshot.activity == "mowing"
        and snapshot.task_operation is None
        and snapshot.task_region_ids is None
        and snapshot.task_area_ids is None
    )
