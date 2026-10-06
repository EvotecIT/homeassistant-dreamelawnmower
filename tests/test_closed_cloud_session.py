"""Borrowed session failures retain the public connection-error contract."""

import asyncio

import pytest
from aiohttp import ClientSession

from .test_async_cloud_session import (
    OPTIONS,
    DreameLawnMowerClient,
    DreameLawnMowerConnectionError,
    server,
)


@pytest.mark.parametrize("operation", ["discovery", "read", "command"])
def test_closed_borrowed_session_raises_connection_error(monkeypatch, operation):
    async def handler(request):
        pytest.fail("A closed session must not dispatch a cloud request")

    async def scenario():
        async with server(monkeypatch, handler):
            session = ClientSession()
            await session.close()
            if operation == "discovery":
                with pytest.raises(
                    DreameLawnMowerConnectionError, match="session is closed"
                ):
                    await DreameLawnMowerClient.async_discover_devices(
                        **OPTIONS, session=session
                    )
                return
            from .test_async_app_commands import client_for

            client = client_for(session)
            try:
                # A cached token exercises the authenticated request rather than login.
                from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
                    cloud_session,
                )

                client._async_cloud = cloud_session.DreameCloudSession(session, **OPTIONS)
                client._async_cloud._token = "cached"
                client._async_cloud._expires_at = float("inf")
                with pytest.raises(
                    DreameLawnMowerConnectionError, match="session is closed"
                ):
                    if operation == "read":
                        await client.async_get_cloud_device_info()
                    else:
                        await client.async_approve_firmware_update()
                assert not client._cloud_read_tasks
            finally:
                await client.async_close()
            assert session.closed

    asyncio.run(scenario())
