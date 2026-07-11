"""Coordinator availability regression checks."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.dreame_lawn_mower.coordinator import (
    DreameLawnMowerCoordinator,
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
