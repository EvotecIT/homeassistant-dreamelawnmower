"""Native property-scan chunking, output and cancellation contracts."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    DreameLawnMowerConnectionError,
)

from .test_async_cloud_session import cloud_strings, login_response, server
from .test_async_map_objects import make_client


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
@pytest.mark.parametrize("metadata", ["supplied", "failed", "disabled"])
@pytest.mark.parametrize("only_values", [False, True])
def test_native_scan_matches_sync_labels_counts_and_order(
    monkeypatch, account_type, metadata, only_values
):
    strings = cloud_strings(account_type)
    values = {"9.9": '{"current_map":true}', "2.1": 13, "3.1": ""}
    definition = {
        "payload": {"keyDefine": {"2.1": {"en": {"13": "Cloud charge complete"}}}}
    }
    calls = []

    async def scenario():
        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            body = await request.json()
            calls.append(body)
            assert request.path == "/dreame-user-iot/iotstatus/props"
            return web.json_response(
                {
                    "code": 0,
                    "data": {
                        "records": [
                            {"key": k, "value": values[k]}
                            for k in body["keys"].split(",")
                        ]
                    },
                }
            )

        async with server(monkeypatch, handler), ClientSession() as session:
            client = make_client(session, account_type)
            client._sync_get_cloud_properties = lambda keys: [
                {"key": k, "value": values[k]} for k in keys
            ]
            client._sync_get_cloud_key_definition = lambda language: (
                definition if metadata == "supplied" else None
            )
            client.async_get_cloud_key_definition = AsyncMock(return_value=definition)
            if metadata == "failed":
                client.async_get_cloud_key_definition.side_effect = (
                    DreameLawnMowerConnectionError("metadata unavailable")
                )
            options = dict(
                keys="9.9,2.1,3.1",
                siids=None,
                piid_start=1,
                piid_end=1,
                chunk_size=2,
                language="en",
                only_values=only_values,
                include_key_definition=metadata != "disabled",
            )
            expected = client._sync_scan_cloud_properties(**options)
            try:
                result = await client.async_scan_cloud_properties(**options)
                assert result == expected
                assert calls == [
                    {"did": "42", "keys": "9.9,2.1"},
                    {"did": "42", "keys": "3.1"},
                ]
                assert (
                    result["requested_key_count"] == result["returned_entry_count"] == 3
                )
                assert result["displayed_entry_count"] == (2 if only_values else 3)
                assert result["summary"]["candidate_map_entry_count"] == 1
                if metadata == "disabled":
                    client.async_get_cloud_key_definition.assert_not_awaited()
                else:
                    client.async_get_cloud_key_definition.assert_awaited_once_with(
                        language="en"
                    )
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


def test_empty_scan_skips_properties_and_metadata():
    async def scenario():
        async with ClientSession() as session:
            client = make_client(session)
            client.async_get_cloud_properties = AsyncMock()
            client.async_get_cloud_key_definition = AsyncMock()
            try:
                result = await client.async_scan_cloud_properties(keys=[])
                assert result["entries"] == [] and result["requested_key_count"] == 0
                client.async_get_cloud_properties.assert_not_awaited()
                client.async_get_cloud_key_definition.assert_not_awaited()
            finally:
                await client.async_close()

    asyncio.run(scenario())


def test_scan_close_stops_later_chunks_and_metadata(monkeypatch):
    strings = cloud_strings("dreame")

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            calls.append(await request.json())
            entered.set()
            await release.wait()
            return web.json_response({"code": 0, "data": []})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = make_client(session)
            client.async_get_cloud_key_definition = AsyncMock()
            task = asyncio.create_task(
                client.async_scan_cloud_properties(keys=["2.1", "9.9"], chunk_size=1)
            )
            try:
                await asyncio.wait_for(entered.wait(), 3)
                await asyncio.wait_for(client.async_close(), 3)
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert calls == [{"did": "42", "keys": "2.1"}]
                client.async_get_cloud_key_definition.assert_not_awaited()
                assert not client._cloud_read_tasks
                assert not session.closed
            finally:
                release.set()
                await client.async_close()

    asyncio.run(scenario())
