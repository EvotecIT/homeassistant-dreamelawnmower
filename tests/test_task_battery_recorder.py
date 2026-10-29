"""Recorder backfill is scoped, conservative, and never reconstructs settings."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import State

from custom_components.dreame_lawn_mower import task_battery_recorder as module
from tests.test_task_history import START, detail


def test_recorder_excludes_docking_interruptions_and_labels_partial_source(monkeypatch):
    async def run():
        import homeassistant.components.recorder as recorder

        def state(entity, value, seconds):
            when = datetime.fromtimestamp(START + seconds, UTC)
            return State(entity, value, last_changed=when, last_updated=when)

        battery_id, mower_id = "sensor.garden_battery", "lawn_mower.garden"
        rows = {
            battery_id: [
                state(battery_id, value, at)
                for at, value in [
                    (0, "80"),
                    (60, "75"),
                    (180, "70"),
                    (240, "65"),
                    (300, "60"),
                ]
            ],
            mower_id: [
                state(mower_id, value, at)
                for at, value in [(0, "mowing"), (90, "docked"), (120, "mowing")]
            ],
        }
        execute = AsyncMock(return_value=rows)
        monkeypatch.setattr(
            recorder,
            "get_instance",
            lambda hass: SimpleNamespace(
                async_add_executor_job=execute,
            ),
        )
        registry = SimpleNamespace(
            async_get_entity_id=lambda domain, *args: (
                battery_id if domain == "sensor" else mower_id
            )
        )
        monkeypatch.setattr(module.er, "async_get", lambda hass: registry)
        hass = SimpleNamespace(config=SimpleNamespace(components={"recorder"}))
        coordinator = SimpleNamespace(
            client=SimpleNamespace(descriptor=SimpleNamespace(unique_id="mower"))
        )
        result = await module.async_recorder_battery(hass, coordinator, detail())
        assert result["source"] == "recorder" and result["coverage"] == "partial"
        # 80→75 before docking, then 70→65→60 after a fresh valid reading.
        assert result["percentage_points"] == 15
        assert result["settings"] == {} and result["settings_timing"] == "unavailable"
        query = execute.call_args.args[0]
        assert query.args[3] == [battery_id, mower_id]
        assert query.keywords["no_attributes"] is True

    asyncio.run(run())


@pytest.mark.parametrize("invalid", ["unknown", "unavailable", "-1", "101"])
def test_recorder_unknown_battery_breaks_even_a_short_mowing_interval(
    monkeypatch, invalid
):
    async def run():
        import homeassistant.components.recorder as recorder

        def state(entity, value, seconds):
            at = datetime.fromtimestamp(START + seconds, UTC)
            return State(entity, value, last_changed=at, last_updated=at)

        rows = {
            "sensor.battery": [
                state("sensor.battery", level, seconds)
                for seconds, level in [
                    (0, "80"),
                    (30, invalid),
                    (60, "70"),
                    (120, "65"),
                ]
            ],
            "lawn_mower.garden": [state("lawn_mower.garden", "mowing", 0)],
        }
        monkeypatch.setattr(
            recorder,
            "get_instance",
            lambda hass: SimpleNamespace(
                async_add_executor_job=AsyncMock(return_value=rows),
            ),
        )
        monkeypatch.setattr(
            module.er,
            "async_get",
            lambda hass: SimpleNamespace(
                async_get_entity_id=lambda domain, *args: (
                    "sensor.battery" if domain == "sensor" else "lawn_mower.garden"
                ),
            ),
        )
        result = await module.async_recorder_battery(
            SimpleNamespace(config=SimpleNamespace(components={"recorder"})),
            SimpleNamespace(
                client=SimpleNamespace(descriptor=SimpleNamespace(unique_id="mower"))
            ),
            detail(),
        )
        assert result["percentage_points"] == 5
        assert result["covered_seconds"] == 60
        assert result["samples"][1]["continuous"] is False

    asyncio.run(run())
