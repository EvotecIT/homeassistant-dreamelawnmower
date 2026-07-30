"""Home Assistant task ownership contracts."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

from custom_components.dreame_lawn_mower.ha_tasks import create_background_task


def test_long_lived_work_uses_home_assistant_background_owner() -> None:
    async def scenario() -> None:
        created: list[tuple[str, asyncio.Task[str]]] = []

        def create_background(coroutine, name: str) -> asyncio.Task[str]:
            task = asyncio.create_task(coroutine)
            created.append((name, task))
            return task

        hass = SimpleNamespace(
            async_create_background_task=create_background,
            async_create_task=Mock(
                side_effect=AssertionError(
                    "background work must not extend config-entry setup"
                )
            ),
        )

        task = create_background_task(
            hass,
            asyncio.sleep(0, result="ready"),
            "dreame-background",
        )

        assert await task == "ready"
        assert created == [("dreame-background", task)]
        hass.async_create_task.assert_not_called()

    asyncio.run(scenario())
