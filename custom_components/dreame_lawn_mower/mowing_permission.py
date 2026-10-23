"""Read an optional HA garden permission without inventing mower rain state."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.util import dt as dt_util

from .debug import sanitize_diagnostic_text


def permission_block(
    hass: Any,
    entity_id: str,
    max_age: int,
    *,
    now: datetime | None = None,
) -> tuple[str, str] | None:
    """Require an explicit, recent ``on``; missing and stale readings fail closed.

    A composite permission helper owns sensor freshness and garden rules. This
    check bounds the helper's own reporting age, not its unchanged-state age.
    """
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state not in {"on", "off"}:
        return "external_permission_unknown", f"{entity_id} has no valid on/off state"
    reported = getattr(state, "last_reported", state.last_updated)
    age = ((now or dt_util.utcnow()) - reported).total_seconds()
    if max_age and (age < 0 or age > max_age):
        return "external_permission_stale", f"{entity_id} has no recent report"
    if state.state == "off":
        detail = state.attributes.get("reason") or state.attributes.get("block_reason")
        return (
            "external_permission_blocked",
            sanitize_diagnostic_text(
                f"{entity_id}: {detail or 'mowing not permitted'}"
            )[:255],
        )
    return None
