"""Compact capability and schedule facts without account or device identifiers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def build_compatibility_summary(
    descriptor: Any,
    snapshot: Any,
    *,
    features: Mapping[str, Any],
    schedules: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Explain reported support without turning missing evidence into rejection."""
    entries = schedules.get("schedules", []) if isinstance(schedules, Mapping) else []
    slots = []
    protocols = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        protocol = entry.get("protocol")
        if protocol in {"document", "tables"}:
            protocols.add(protocol)
        else:
            protocol = "unknown"
        slots.append(
            {
                "map_index": entry.get("idx"),
                "protocol": protocol,
                "read_status": entry.get("read_status", "unknown"),
                "reason": "schedule_read_failed"
                if entry.get("error")
                else "tasks_incomplete"
                if entry.get("task_errors")
                else None,
                "plan_count": len(entry.get("plans", [])),
            }
        )
    return {
        "brand": "MOVA" if descriptor.account_type == "mova" else "Dreame",
        "region": descriptor.country,
        "model": descriptor.model,
        "display_model": descriptor.display_model,
        "firmware": getattr(snapshot, "firmware_version", None),
        "features": dict(features),
        "schedule_protocol": (
            next(iter(protocols))
            if len(protocols) == 1
            else "mixed"
            if protocols
            else "unknown"
        ),
        "schedule_slots": slots,
    }
