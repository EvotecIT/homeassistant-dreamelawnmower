"""Map arrival commands use the shared native driver before notifying listeners."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    client_device_actions,
    device_commands,
    device_state,
    device_types,
)

from .test_async_app_commands import client_for
from .test_async_cloud_session import cloud_strings, login_response, server
from .test_async_dock import configure_mode_restoration


@pytest.mark.parametrize("trigger", ["map", "error"])
def test_native_restoration_precedes_map_notification(monkeypatch, trigger):
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
            fake._map_manager = SimpleNamespace(
                ready=True,
                editor=SimpleNamespace(refresh_map=lambda: requests.append("notify")),
            )
            fake.status.has_error = True
            owner = device_commands._DreameMowerDeviceCommandMixin
            state_owner = device_state._DreameMowerDeviceStateMixin
            fake._restore_go_to_zone_plan = lambda stop=False: (
                owner._restore_go_to_zone_plan(fake, stop)
            )
            fake._property_changed = Mock(side_effect=lambda: requests.append("notify"))
            try:
                await client_device_actions.async_run_device_plan(
                    client,
                    lambda device: (
                        state_owner._map_changed_plan(fake)
                        if trigger == "map"
                        else state_owner._error_changed_plan(fake, 0)
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


@pytest.mark.parametrize("read_fails", [False, True])
def test_task_completion_restores_mode_before_refreshing_properties(
    monkeypatch, read_fails
):

    strings = cloud_strings("dreame")
    requests = []

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        payload = (await request.json())["data"]
        requests.append(payload)
        if payload["method"] == "get_properties" and read_fails:
            return web.Response(status=503)
        result = [{"code": 0}] if payload["method"] == "set_properties" else []
        return web.json_response({"code": 0, "data": {"result": result}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            fake = configure_mode_restoration(client)
            fake.get_property = lambda prop: (
                device_types.DreameMowerTaskStatus.COMPLETED.value
            )
            fake.schedule_update = Mock(wraps=fake.schedule_update)
            fake._map_manager = Mock()
            fake._ready = True
            fake.capability.disable_sensor_cleaning = True
            fake.property_mapping[device_types.DreameMowerProperty.BLADES_LEFT] = {
                "siid": 9,
                "piid": 1,
            }
            fake.data = {device_types.DreameMowerProperty.BLADES_LEFT.value: 80}
            fake._protocol.prefer_cloud = True
            fake._protocol.dreame_cloud = True
            fake._protocol.get_properties = Mock(
                side_effect=AssertionError("Blocking read")
            )
            fake._handle_properties = Mock(return_value=False)

            def apply(rows):
                assert client._device._state_lock._is_owned()
                yield from ()
                return fake._handle_properties(rows)

            monkeypatch.setattr(client._device, "_handle_properties_plan", apply)
            fake._request_properties_plan = lambda properties: (
                device_state._DreameMowerDeviceStateMixin._request_properties_plan(
                    fake, properties
                )
            )
            try:
                await client_device_actions.async_run_device_plan(
                    client,
                    lambda device: (
                        device_state._DreameMowerDeviceStateMixin._task_status_changed_plan(
                            fake,
                            device_types.DreameMowerTaskStatus.CRUISING_POINT.value,
                        )
                    ),
                )
                assert [item["method"] for item in requests] == [
                    "set_properties",
                    "get_properties",
                ]
                assert requests[-1]["params"] == [
                    {
                        "did": str(device_types.DreameMowerProperty.BLADES_LEFT.value),
                        "siid": 9,
                        "piid": 1,
                    }
                ]
                assert fake.status.go_to_zone is None
                assert fake._handle_properties.call_count == (0 if read_fails else 1)
                fake.schedule_update.assert_any_call(1, True)
            finally:
                await client.async_close()

    asyncio.run(scenario())
