"""Saved map downloads preserve state across cancellation and identity changes."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientSession

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    client_map_maintenance,
    cloud_session,
)

from .test_async_app_commands import client_for


@pytest.mark.parametrize("outcome", ["success", "changed", "md5", "close", "malformed"])
@pytest.mark.parametrize("recovery", [False, True])
def test_saved_map_list_commit_requires_current_owner(monkeypatch, outcome, recovery):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        signing = AsyncMock(return_value="https://example.invalid/signed-map")

        async def download(self, url, *, deadline, max_bytes):
            assert url == "https://example.invalid/signed-map"
            entered.set()
            await release.wait()
            if outcome == "malformed":
                return b"{}" if recovery else b'{"mapstr":[{}],"curr_id":0}'
            return (
                b'[{"id": 1, "info": []}]'
                if recovery
                else b'{"mapstr": [], "curr_id": 0}'
            )

        monkeypatch.setattr(
            cloud_session.DreameCloudSession,
            "async_get_interim_file_url",
            signing,
        )
        monkeypatch.setattr(
            cloud_session.DreameCloudSession,
            "async_get_public_file",
            download,
        )
        async with ClientSession() as session:
            client = client_for(session)
            device = client._ensure_device()
            manager = device._map_manager
            manager._map_list_object_name = "saved-maps"
            manager._map_list_md5 = "original"
            manager._need_map_list_request = not recovery
            manager._recovery_map_list_object_name = "saved-recovery"
            manager._need_recovery_map_list_request = True
            manager._saved_map_data = {1: SimpleNamespace(recovery_map_list=["old"])}
            manager._map_list = [1]
            owner = client_map_maintenance.NativeMapLists(client, device)
            manager._native_list_request = owner.request
            request = (
                manager.request_recovery_map_list
                if recovery
                else manager.request_map_list
            )
            try:
                await asyncio.to_thread(request)
                await asyncio.wait_for(entered.wait(), 3)
                task = owner._tasks[recovery]
                await asyncio.to_thread(request)
                await asyncio.sleep(0)
                assert signing.await_count == 1
                if outcome == "changed":
                    if recovery:
                        manager._recovery_map_list_object_name = "replacement-recovery"
                    else:
                        manager._map_list_object_name = "replacement-maps"
                if outcome == "md5":
                    manager._map_list_md5 = "replacement"
                    manager._need_map_list_request = True
                if outcome == "close":
                    await asyncio.wait_for(client.async_close(), 3)
                    with pytest.raises(asyncio.CancelledError):
                        await task
                else:
                    release.set()
                    await asyncio.wait_for(task, 3)
                if outcome == "success":
                    if recovery:
                        assert manager._saved_map_data[1].recovery_map_list == []
                        assert manager._need_recovery_map_list_request is False
                        assert manager._map_list == [1]
                    else:
                        assert manager._saved_map_data == {}
                        assert manager._map_list == []
                        assert manager._need_map_list_request is False
                else:
                    assert list(manager._saved_map_data) == [1]
                    assert manager._map_list == [1]
                    assert manager._need_map_list_request is (
                        not recovery or outcome == "md5"
                    )
                    assert manager._need_recovery_map_list_request is True
                assert not session.closed
                assert not client._cloud_read_tasks
            finally:
                release.set()
                await client.async_close()

    asyncio.run(scenario())


@pytest.mark.parametrize("stale", [False, True])
def test_recovery_download_waits_for_saved_list_commit(monkeypatch, stale):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async with ClientSession() as session:
            client = client_for(session)
            device = client._ensure_device()
            manager = device._map_manager
            manager._map_list_object_name = "saved"
            manager._recovery_map_list_object_name = "recovery"
            manager._need_map_list_request = True
            manager._need_recovery_map_list_request = True

            async def sign(self, did, model, name, **kwargs):
                return name

            async def download(self, url, **kwargs):
                calls.append(url)
                if url == "saved":
                    entered.set()
                    await release.wait()
                    return b'{"mapstr": [], "curr_id": 0}'
                assert manager._need_map_list_request is False
                return b"[]"

            monkeypatch.setattr(
                cloud_session.DreameCloudSession, "async_get_interim_file_url", sign
            )
            monkeypatch.setattr(
                cloud_session.DreameCloudSession, "async_get_public_file", download
            )
            owner = client_map_maintenance.NativeMapLists(client, device)
            manager._native_list_request = owner.request
            try:
                manager.request_map_list()
                manager.request_recovery_map_list()
                await asyncio.wait_for(entered.wait(), 3)
                await asyncio.sleep(0)
                assert calls == ["saved"]
                if stale:
                    manager._map_list_md5 = "replacement"
                    manager._need_map_list_request = True
                release.set()
                await asyncio.wait_for(asyncio.gather(*owner._tasks.values()), 3)
                if stale:
                    assert calls == ["saved"]
                    assert manager._need_recovery_map_list_request is True
                    manager.request_map_list()
                    manager.request_recovery_map_list()
                    await asyncio.sleep(0)
                    await asyncio.wait_for(asyncio.gather(*owner._tasks.values()), 3)
                    assert calls.pop(0) == "saved"
                assert calls == ["saved", "recovery"]
                assert manager._need_recovery_map_list_request is False
            finally:
                release.set()
                await client.async_close()

    asyncio.run(scenario())
