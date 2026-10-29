"""History access, current-entry lifetime, coalescing, and storage contracts."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp import web

from custom_components.dreame_lawn_mower import task_history_api as api_module
from custom_components.dreame_lawn_mower import task_history_store as store_module
from custom_components.dreame_lawn_mower.runtime_cache import (
    DreameLawnMowerRuntimeTelemetryCache,
    observe_runtime_session_state,
    runtime_mission_session_started_at,
)
from tests.runtime_fixtures import runtime_hass
from tests.test_task_history import START, detail


def environment(monkeypatch):
    store = SimpleNamespace(
        async_load=AsyncMock(return_value=None),
        async_save=AsyncMock(),
        async_remove=AsyncMock(),
    )
    monkeypatch.setattr(store_module, "history_store", lambda *args: store)
    coordinator = SimpleNamespace(
        entry=SimpleNamespace(entry_id="entry"),
        client=SimpleNamespace(descriptor=SimpleNamespace(unique_id="mower")),
    )
    hass = runtime_hass(
        coordinators={"entry": coordinator},
        async_create_task=lambda coro, name: asyncio.create_task(coro),
    )
    owner = store_module.TaskHistoryOwner(hass, coordinator)
    coordinator.task_history = owner
    api = api_module.TaskHistoryAPI(hass)
    registry = SimpleNamespace(
        async_get_entity_id=Mock(return_value="lawn_mower.garden")
    )
    monkeypatch.setattr(api_module.er, "async_get", lambda hass: registry)
    permission = Mock(return_value=True)
    request = {
        "hass_user": SimpleNamespace(
            is_admin=False, permissions=SimpleNamespace(check_entity=permission)
        )
    }
    return hass, coordinator, owner, api, request, permission, store


def test_non_admin_reader_needs_mower_read_permission(monkeypatch):
    _, _, owner, api, request, permission, _ = environment(monkeypatch)
    assert api.authorize(request, "entry") is owner
    permission.assert_called_once_with("lawn_mower.garden", "read")
    permission.return_value = False
    with pytest.raises(web.HTTPForbidden):
        api.authorize(request, "entry")
    with pytest.raises(web.HTTPForbidden):
        api.authorize({}, "entry")
    with pytest.raises(web.HTTPNotFound):
        api.authorize(request, "another-entry")


def test_cancelled_waiter_shares_one_worker_and_close_drains_it(monkeypatch):
    async def run():
        _, _, owner, api, _, _, _ = environment(monkeypatch)
        started, finish = asyncio.Event(), asyncio.Event()

        async def refresh():
            started.set()
            await finish.wait()

        owner.history.async_refresh = AsyncMock(side_effect=refresh)
        first = asyncio.create_task(owner.async_read("list"))
        await started.wait()
        second = asyncio.create_task(owner.async_read("list"))
        await asyncio.sleep(0)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        closing = asyncio.create_task(owner.async_close())
        await asyncio.sleep(0)
        assert owner.closed and not closing.done()
        finish.set()
        await second
        await closing
        owner.history.async_refresh.assert_awaited_once()
        assert not owner._tasks
        with pytest.raises(web.HTTPNotFound):
            api.current("entry", owner)

    asyncio.run(run())


def test_detail_cannot_publish_after_unload_or_entry_replacement(monkeypatch):
    async def run():
        _, coordinator, owner, api, request, _, _ = environment(monkeypatch)

        async def read(key):
            coordinator.task_history = object()
            return detail()

        owner.async_read = read
        with pytest.raises(web.HTTPNotFound):
            await api_module.TaskHistoryDetailView(api).get(
                request, "entry", detail().id
            )

    asyncio.run(run())


def test_detail_delivers_safe_paths_and_never_current_settings(monkeypatch):
    async def run():
        _, _, owner, api, request, _, _ = environment(monkeypatch)
        owner.async_read = AsyncMock(return_value=detail())
        response = await api_module.TaskHistoryDetailView(api).get(
            request,
            "entry",
            detail().id,
        )
        payload = json.loads(response.body)
        assert payload["schema_version"] == 1 and payload["battery"] is None
        assert payload["photo_paths"] == [
            f"/api/dreame_lawn_mower/task-history/entry/{detail().id}/photo/0"
        ]
        assert b"ali_dreame" not in response.body
        assert response.headers["Cache-Control"] == "private, no-store"
        assert response.headers["Vary"] == "Authorization"

    asyncio.run(run())


def test_failed_load_does_not_overwrite_previous_history(monkeypatch):
    async def run():
        _, _, owner, _, _, _, store = environment(monkeypatch)
        store.async_load.side_effect = OSError("unreadable")
        await owner.async_load()
        owner._revision += 1
        await owner.async_close()
        store.async_save.assert_not_awaited()

    asyncio.run(run())


def test_final_private_store_write_is_drained_and_scope_checked(monkeypatch):
    async def run():
        _, _, owner, _, _, _, store = environment(monkeypatch)
        store.async_load.return_value = {"scope": "different-mower", "runs": [{}]}
        await owner.async_load()
        assert not owner.observations.runs
        owner.observations.observe(
            key="run",
            at=START,
            level=75,
            charging=False,
            active=True,
            map_revision="a" * 32,
            settings={},
        )
        owner._revision += 1
        await owner.async_close()
        record = store.async_save.call_args.args[0]
        assert record["scope"] == owner.scope
        assert record["runs"][0]["samples"][0]["level"] == 75
        await owner.async_close()
        store.async_save.assert_awaited_once()

    asyncio.run(run())


def test_owner_reload_with_fresh_runtime_cache_preserves_disjoint_task_segments(
    monkeypatch,
):
    async def run():
        _, coordinator, owner, _, _, _, store = environment(monkeypatch)
        clock = [START]
        monkeypatch.setattr(store_module.time, "time", lambda: clock[0])
        revision = "a" * 32

        def setup_runtime(target, event_at):
            target.runtime_telemetry_cache = DreameLawnMowerRuntimeTelemetryCache()
            observe_runtime_session_state(
                target.runtime_telemetry_cache,
                active_session=True,
                new_session_event_at=event_at,
                session_identity=1,
            )
            assert (
                runtime_mission_session_started_at(target.runtime_telemetry_cache)
                == event_at
            )
            target.app_maps = {
                "map_list_valid": True,
                "current_map_index": 0,
                "maps": [
                    {
                        "idx": 0,
                        "created": True,
                        "available": True,
                        "hash_match": True,
                        "md5": revision,
                        "info": {"hash": revision},
                    }
                ],
            }
            target.batch_device_data = {
                "batch_mowing_preferences": {
                    "maps": [
                        {
                            "idx": 0,
                            "available": True,
                            "mode": 0,
                            "preferences": [{"area_id": 0, "mowing_height_cm": 5.5}],
                        }
                    ]
                }
            }

        setup_runtime(coordinator, START)
        snapshot = SimpleNamespace(available=True, battery_level=80, charging=False)
        owner.observe(snapshot, True)
        clock[0] = START + 60
        snapshot.battery_level = 75
        owner.observe(snapshot, True)
        await owner.async_close()
        saved = json.loads(json.dumps(store.async_save.call_args.args[0]))
        _, fresh, resumed, _, _, _, fresh_store = environment(monkeypatch)
        clock[0] = START + 180
        fresh_store.async_load.return_value = saved
        await resumed.async_load()
        setup_runtime(fresh, START + 180)
        snapshot.battery_level = 70
        resumed.observe(snapshot, True)
        clock[0] = START + 300
        snapshot.battery_level = 65
        resumed.observe(snapshot, True)
        result = resumed.observations.for_task(detail())
        assert result["percentage_points"] == 10
        assert result["covered_seconds"] == 180
        assert result["coverage"] == "partial"
        assert result["settings_timing"] == "at_start"
        assert result["settings"]["zones"][0]["mowing_height_cm"] == 5.5
        assert result["samples"][2]["continuous"] is False
        await resumed.async_close()

    asyncio.run(run())
