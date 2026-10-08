"""Cancelled native writes restore unsent state and retain uncertain writes."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import Mock

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    client_device_actions,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device import (
    DreameMowerDevice,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device_types import (
    DirtyData,
    DreameMowerAutoSwitchProperty,
    DreameMowerProperty,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    DeviceUpdateFailedException,
)

from .test_async_app_commands import client_for
from .test_async_cloud_session import cloud_strings, login_response, server


@pytest.mark.parametrize("kind", ["property", "auto_switch"])
@pytest.mark.parametrize("pending", [False, True])
def test_cancel_before_dispatch_restores_previous_cache_and_pending_write(
    monkeypatch, kind, pending,
):
    async def run():
        strings = cloud_strings("dreame")
        entered = asyncio.Event()
        writes = []

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            writes.append(await request.json())
            pytest.fail("A cancelled undispatched plan sent a write")

        async def wait_before_dispatch(*args, **kwargs):
            entered.set()
            await asyncio.Future()

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            device = client._device
            device.schedule_update = Mock()
            device._property_changed = Mock()
            original, updated = (2, 1) if kind == "auto_switch" else (20, 30)
            previous = DirtyData(original, 0, time.time() - 1) if pending else None
            if kind == "auto_switch":
                prop = DreameMowerAutoSwitchProperty.CLEANING_ROUTE
                device.capability.auto_switch_settings = True
                device.property_mapping = {
                    **device.property_mapping,
                    DreameMowerProperty.AUTO_SWITCH_SETTINGS: {"siid": 4, "piid": 5},
                }
                device.auto_switch_data = {prop.name: original}
                cache = device.auto_switch_data
                dirty = device._dirty_auto_switch_data
                key = prop.name
            else:
                prop = DreameMowerProperty.VOLUME
                device.data[prop.value] = original
                cache = device.data
                dirty = device._dirty_data
                key = prop.value
            def create(current):
                if kind == "auto_switch":
                    return current._set_auto_switch_property_plan(prop, updated)
                return current._set_property_plan(prop, updated)
            if previous is not None:
                dirty[key] = previous
            monkeypatch.setattr(
                client_device_actions, "async_device_rpc", wait_before_dispatch,
            )
            operation = asyncio.create_task(
                client_device_actions.async_run_device_plan(client, create)
            )
            try:
                await asyncio.wait_for(entered.wait(), 2)
                assert cache[key] == updated
                operation.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await operation
                assert cache[key] == original
                if previous is None:
                    assert key not in dirty
                else:
                    assert dirty[key] is previous
                assert writes == []
                assert not session.closed
            finally:
                if not operation.done():
                    operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)
                await client.async_close()

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["property", "auto_switch"])
@pytest.mark.parametrize("reply", ["rejected", "error"])
def test_failed_followup_write_retains_previous_pending_readback(kind, reply):
    device = DreameMowerDevice("Pending property", None, " ")
    device.schedule_update = Mock()
    device._property_changed = Mock()
    previous = DirtyData(20, 10, time.time() - 1)
    if kind == "auto_switch":
        prop = DreameMowerAutoSwitchProperty.CLEANING_ROUTE
        previous = DirtyData(2, 0, time.time() - 1)
        device.capability.auto_switch_settings = True
        device.property_mapping = {
            **device.property_mapping,
            DreameMowerProperty.AUTO_SWITCH_SETTINGS: {"siid": 4, "piid": 5},
        }
        device.auto_switch_data = {prop.name: previous.value}
        dirty = device._dirty_auto_switch_data
        key = prop.name
    else:
        prop = DreameMowerProperty.VOLUME
        device.data[prop.value] = previous.value
        dirty = device._dirty_data
        key = prop.value
    def setter():
        if kind == "auto_switch":
            return device.set_auto_switch_property(prop, 1)
        return device.set_property(prop, 30)
    dirty[key] = previous
    device._protocol.set_property = Mock(
        return_value=[{"code": -1}],
        side_effect=TimeoutError("uncertain acknowledgement")
        if reply == "error" else None,
    )
    try:
        if kind == "property" and reply == "error":
            with pytest.raises(DeviceUpdateFailedException):
                setter()
        else:
            setter()
        cached = (
            device.auto_switch_data[prop.name]
            if kind == "auto_switch" else device.data[prop.value]
        )
        assert cached == previous.value
        assert dirty[key] is previous
        device._protocol.set_property.assert_called_once()
    finally:
        device.disconnect()


def test_cancel_after_property_dispatch_keeps_uncertain_write_for_readback(
    monkeypatch,
):
    async def run():
        strings = cloud_strings("dreame")
        entered, release = asyncio.Event(), asyncio.Event()
        writes = []
        prop = DreameMowerProperty.VOLUME

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            payload = (await request.json())["data"]
            assert payload["method"] == "set_properties"
            assert payload["params"][0]["value"] == 30
            writes.append(payload)
            entered.set()
            await release.wait()
            return web.json_response({"code": 0, "data": {"result": [{"code": 0}]}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            device = client._device
            device.data[prop.value] = 20
            device.schedule_update = Mock()
            device._property_changed = Mock()
            operation = asyncio.create_task(
                client_device_actions.async_run_device_plan(
                    client, lambda current: current._set_property_plan(prop, 30),
                )
            )
            try:
                await asyncio.wait_for(entered.wait(), 2)
                dirty = device._dirty_data[prop.value]
                operation.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await operation
                assert device.data[prop.value] == 30
                assert device._dirty_data[prop.value] is dirty
                assert dirty.value == 30 and dirty.previous_value == 20
                assert len(writes) == 1
                assert not session.closed
            finally:
                release.set()
                if not operation.done():
                    operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)
                await client.async_close()

    asyncio.run(run())
