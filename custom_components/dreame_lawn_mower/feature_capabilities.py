"""Expose the client's capability decisions through Home Assistant surfaces."""

from __future__ import annotations

from typing import Any

from .dreame_lawn_mower_client.feature_capabilities import (
    resolved_feature_capabilities,
)


def coordinator_feature_capabilities(coordinator: Any) -> dict[str, dict[str, str]]:
    """Combine the current snapshot with retained positive runtime evidence."""
    evidence = getattr(coordinator, "feature_capability_evidence", None)
    observed, advertised = evidence() if callable(evidence) else ((), ())
    return resolved_feature_capabilities(
        getattr(coordinator, "data", None),
        descriptor=getattr(getattr(coordinator, "client", None), "descriptor", None),
        observed=observed,
        advertised=advertised,
    )
