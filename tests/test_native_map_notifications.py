"""Map arrival commands use the shared native driver before notifying listeners."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    client_device_actions,
    device_commands,
    device_state,
)

from .test_async_app_commands import client_for
from .test_async_cloud_session import cloud_strings, login_response, server
from .test_async_dock import configure_mode_restoration


def test_native_map_arrival_restores_before_notifying(monkeypatch):
    strings = cloud_strings("dreame")
    requests = []

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        payload = (await request.json())["data"]
        requests.append(payload)
        result = [{"code": 0}] if payload["method"] == "set_properties" else {"code": 0}
        return web.json_response({"code": 0, "data": {"result": result}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            fake = configure_mode_restoration(client)
            fake.status.go_to_zone.size = 10
            fake.status.go_to_zone.x = fake.status.go_to_zone.y = 0
            fake.status.current_map = SimpleNamespace(
                robot_position=SimpleNamespace(x=0, y=0),
                docked=False,
            )
            fake._map_manager = SimpleNamespace(ready=True)
            owner = device_commands._DreameMowerDeviceCommandMixin
            fake._restore_go_to_zone_plan = lambda stop=False: (
                owner._restore_go_to_zone_plan(fake, stop)
            )
            fake._property_changed = Mock(side_effect=lambda: requests.append("notify"))
            try:
                await client_device_actions.async_run_device_plan(
                    client,
                    lambda device: (
                        device_state._DreameMowerDeviceStateMixin._map_changed_plan(
                            fake
                        )
                    ),
                )
                assert [item["method"] for item in requests[:-1]] == [
                    "action",
                    "set_properties",
                ]
                assert requests[0]["params"]["aiid"] == 1
                assert requests[-1] == "notify"
                assert fake.status.go_to_zone is None
                fake._update_cleaning_mode.assert_not_called()
            finally:
                await client.async_close()

    asyncio.run(scenario())
