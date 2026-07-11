"""Coordinator availability regression checks."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.dreame_lawn_mower.coordinator import (
    DreameLawnMowerCoordinator,
    _runtime_tracking_active,
)


def test_offline_snapshot_discards_stale_coordinator_data() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator.data = SimpleNamespace(state="stale")
    coordinator.client = SimpleNamespace(
        async_refresh=lambda: _offline_snapshot(),
    )

    with pytest.raises(UpdateFailed, match="Mower is offline"):
        asyncio.run(coordinator._async_update_data())

    assert coordinator.data is None


async def _offline_snapshot() -> SimpleNamespace:
    return SimpleNamespace(available=False)


def test_runtime_tracking_respects_explicit_inactive_heartbeat() -> None:
    snapshot = SimpleNamespace(
        mowing_session_active=False,
        activity="mowing",
    )

    assert _runtime_tracking_active(snapshot) is False


def test_runtime_tracking_falls_back_when_heartbeat_state_is_unknown() -> None:
    snapshot = SimpleNamespace(
        mowing_session_active=None,
        activity="paused",
    )

    assert _runtime_tracking_active(snapshot) is True
