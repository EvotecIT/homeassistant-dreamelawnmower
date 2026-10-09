"""Native auto-switch writes preserve rollback and cancellation ownership."""

import asyncio
import time
from unittest.mock import Mock

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    client_device_actions,
    client_mqtt_messages,
    client_refresh,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device_types import (
    DirtyData,
    DreameMowerAutoSwitchProperty,
    DreameMowerCleaningMode,
    DreameMowerProperty,
)

from .test_async_app_commands import client_for
from .test_async_cloud_session import cloud_strings, login_response, server


@pytest.mark.parametrize(
    "entrypoint,reply",
    [
        (entrypoint, reply)
        for entrypoint in [
            "setting",
            "callback",
            "response",
            "mixed_response",
            "refresh",
            "property_read",
            "rollback",
            "mqtt",
        ]
        for reply in ["success", "rejected", "cancel"]
    ]
    + [("mqtt", "reconnect")],
)
def test_native_auto_switch_write(monkeypatch, reply, entrypoint):
    strings = cloud_strings("dreame")
    prop = DreameMowerAutoSwitchProperty.CLEANING_ROUTE

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            payload = (await request.json())["data"]
            if (
                entrypoint in {"refresh", "property_read"}
                and payload["method"] == "get_properties"
            ):
                return web.json_response(
                    {
                        "code": 0,
                        "data": {
                            "result": [
                                {
                                    "did": str(DreameMowerProperty.CLEANING_MODE.value),
                                    "code": 0,
                                    "value": DreameMowerCleaningMode.MOWING.value,
                                }
                            ]
                        },
                    }
                )
            calls.append(payload)
            assert payload["method"] == "set_properties"
            assert payload["params"][0]["value"] == f'{{"k":"{prop.value}","v":1}}'
            entered.set()
            await release.wait()
            return web.json_response(
                {
                    "code": 0,
                    "data": {"result": [{"code": -1 if reply == "rejected" else 0}]},
                }
            )

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            device = client._device
            device.capability.auto_switch_settings = True
            device.property_mapping = {
                **device.property_mapping,
                DreameMowerProperty.AUTO_SWITCH_SETTINGS: {"siid": 4, "piid": 5},
            }
            device.auto_switch_data = {prop.name: 2}
            device._dirty_auto_switch_data = {}
            device._ready = True
            device.capability.cleaning_route = True
            device.data[DreameMowerProperty.CLEANING_MODE.value] = (
                DreameMowerCleaningMode.MOWING.value
            )
            device.status.cleaning_mode = DreameMowerCleaningMode.UNKNOWN
            pending_transitions = []
            if entrypoint in {
                "response",
                "mixed_response",
                "refresh",
                "property_read",
                "mqtt",
            }:
                device.data[DreameMowerProperty.CLEANING_MODE.value] = (
                    DreameMowerCleaningMode.UNKNOWN.value
                )
            if entrypoint == "rollback":
                mode_id = DreameMowerProperty.CLEANING_MODE.value
                device.data[mode_id] = DreameMowerCleaningMode.UNKNOWN.value
                device._dirty_data[mode_id] = DirtyData(
                    value=DreameMowerCleaningMode.UNKNOWN.value,
                    previous_value=DreameMowerCleaningMode.MOWING.value,
                    update_time=time.time() - device._restore_timeout - 1,
                )
                device._property_update_callback[mode_id].append(
                    lambda previous: pending_transitions.append(previous)
                )
                monkeypatch.setattr(device, "schedule_update", Mock())
            if entrypoint == "mixed_response":
                device.data[DreameMowerProperty.TASK_STATUS.value] = 6
                device._property_update_callback[
                    DreameMowerProperty.TASK_STATUS.value
                ] = [
                    lambda previous: pending_transitions.append(previous),
                ]
            if reply == "reconnect":
                device._property_update_callback[
                    DreameMowerProperty.CLEANING_MODE.value
                ].append(lambda previous: pending_transitions.append(previous))
            notifications = []
            monkeypatch.setattr(
                device,
                "_property_changed",
                lambda: notifications.append(device.auto_switch_data[prop.name]),
            )
            monkeypatch.setattr(
                device._protocol,
                "set_property",
                Mock(side_effect=AssertionError("Blocking write")),
            )

            def plan(current):
                if entrypoint == "property_read":
                    return current._request_properties_plan(
                        [DreameMowerProperty.CLEANING_MODE]
                    )
                if entrypoint in {"response", "mixed_response"}:
                    rows = [
                        {
                            "did": str(DreameMowerProperty.CLEANING_MODE.value),
                            "code": 0,
                            "value": DreameMowerCleaningMode.MOWING.value,
                        }
                    ]
                    if entrypoint == "mixed_response":
                        rows.append(
                            {
                                "did": str(DreameMowerProperty.TASK_STATUS.value),
                                "code": 0,
                                "value": 0,
                            }
                        )
                    return current._handle_properties_plan(rows)
                if entrypoint == "callback":
                    return current._cleaning_mode_changed_plan()
                return current._set_auto_switch_property_plan(prop, 1)

            if entrypoint == "mqtt":
                device._default_properties.append(DreameMowerProperty.CLEANING_MODE)
                receiver = client_mqtt_messages.NativeMqttMessages(client, device)
                device._native_message_receiver = receiver.request
                device._native_connected_receiver = receiver.request_connected
                mapping = device.property_mapping[DreameMowerProperty.CLEANING_MODE]
                message = {
                    "method": "properties_changed",
                    "params": [
                        {**mapping, "value": DreameMowerCleaningMode.MOWING.value},
                    ],
                }
                device.data[DreameMowerProperty.BATTERY_LEVEL.value] = 0
                if reply == "success":
                    # One malformed packet must not stop later valid messages.
                    await asyncio.to_thread(
                        device._message_callback,
                        {
                            "method": "properties_changed",
                            "params": [{}],
                        },
                    )
                await asyncio.to_thread(device._message_callback, message)
                await asyncio.sleep(0)
                task = receiver._task
                assert task is not None
            elif entrypoint == "rollback":
                monkeypatch.setattr(device, "_select_update_properties", lambda: [])
                task = asyncio.create_task(
                    client_refresh.async_update_device(
                        client, deadline=time.monotonic() + 20
                    )
                )
            elif entrypoint == "refresh":
                monkeypatch.setattr(
                    device,
                    "_select_update_properties",
                    lambda: [DreameMowerProperty.CLEANING_MODE],
                )
                finish = Mock()

                def finish_plan():
                    yield from ()
                    finish()

                monkeypatch.setattr(device, "_finish_update_plan", finish_plan)
                task = asyncio.create_task(
                    client_refresh.async_update_device(
                        client, deadline=time.monotonic() + 20
                    )
                )
            else:
                task = asyncio.create_task(
                    client_device_actions.async_run_device_plan(client, plan)
                )
            try:
                await asyncio.wait_for(entered.wait(), 5)
                if entrypoint == "mqtt":
                    battery_mapping = device.property_mapping[
                        DreameMowerProperty.BATTERY_LEVEL
                    ]
                    later_message = {
                        "method": "properties_changed",
                        "params": [{**battery_mapping, "value": 55}],
                    }
                    await asyncio.to_thread(device._message_callback, later_message)
                    later_message["params"][0]["value"] = 77
                    assert device.data[DreameMowerProperty.BATTERY_LEVEL.value] == 0
                if reply == "reconnect":
                    monkeypatch.setattr(device, "schedule_update", Mock())
                    await asyncio.to_thread(device._connected_callback)
                    release.set()
                    # The ordered owner resets state after draining the old plan.
                    await task
                    assert len(calls) == 1
                    assert device.last_realtime_message is None
                    assert device.data[DreameMowerProperty.BATTERY_LEVEL.value] == 0
                    assert not receiver._messages
                    assert not device._pending_property_callbacks
                    assert pending_transitions == []
                    # The new connection still delivers messages normally.
                    later_message["params"][0]["value"] = 88
                    await asyncio.to_thread(device._message_callback, later_message)
                    await asyncio.sleep(0)
                    await receiver._task
                    assert device.data[DreameMowerProperty.BATTERY_LEVEL.value] == 88
                    assert device.last_realtime_message is not None
                    assert len(calls) == 1
                    return
                if reply == "cancel":
                    if entrypoint == "mqtt":
                        await client.async_close()
                    else:
                        task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                else:
                    release.set()
                    await task
                assert len(calls) == 1
                expected = [1, 2] if reply == "rejected" else [1]
                if (
                    entrypoint
                    in {
                        "response",
                        "mixed_response",
                        "refresh",
                        "property_read",
                        "rollback",
                        "mqtt",
                    }
                    and reply != "cancel"
                ):
                    expected.append(2 if reply == "rejected" else 1)
                if entrypoint == "mqtt":
                    if reply != "cancel":
                        expected.append(2 if reply == "rejected" else 1)
                    else:
                        # Client close retains the device's disconnect notification.
                        expected.append(1)
                        assert device.disconnected
                    assert device.data[DreameMowerProperty.BATTERY_LEVEL.value] == (
                        0 if reply == "cancel" else 55
                    )
                    assert not receiver._messages
                assert notifications == expected
                assert device.auto_switch_data[prop.name] == (
                    2 if reply == "rejected" else 1
                )
                assert (prop.name in device._dirty_auto_switch_data) is (
                    reply != "rejected"
                )
                assert not session.closed
                if entrypoint == "rollback":
                    assert mode_id not in device._dirty_data
                    release.set()
                    await client_refresh.async_update_device(
                        client, deadline=time.monotonic() + 20
                    )
                    assert len(calls) == 1
                    assert pending_transitions == [DreameMowerCleaningMode.MOWING.value]
                if entrypoint == "refresh":
                    assert finish.call_count == (0 if reply == "cancel" else 1)
                if entrypoint == "mixed_response":
                    if reply == "cancel":
                        release.set()
                        await client_device_actions.async_run_device_plan(client, plan)
                    assert pending_transitions == [6]
                    assert len(calls) == 1
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                await client.async_close()

    asyncio.run(scenario())
