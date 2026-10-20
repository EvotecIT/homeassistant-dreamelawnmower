"""Contract tests for the per-zone mowing history read from session summaries."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.zone_mowing_history import (  # noqa: E501
    ZoneMowingHistory,
    ZoneMowingRecord,
    async_get_zone_mowing_records,
    latest_zone_mowing,
    zone_mowing_records,
)
from custom_components.dreame_lawn_mower.sensor_zone_history import (
    DreameLawnMowerLeastRecentlyMowedZoneSensor,
    selected_map_revision,
    zone_mowing_overview,
)

from .test_session_photos import _client, _Cloud, _record

HASH = "a" * 32
OTHER_HASH = "b" * 32

MON = "ali_dreame/2026/10/14/IQ000001/-1234_171000000.0529.json"
TUE = "ali_dreame/2026/10/15/IQ000001/-1234_120000000.0529.json"
PATROL = "ali_dreame/2026/10/15/IQ000001/-1234_210000000.0529.json"
ZONES = [
    {"area_id": 1, "label": "Zone #1"},
    {"area_id": 2, "label": "Zone #2"},
    {"area_id": 3, "label": "Zone #3"},
]


def _summary(region_status, start: int, end: int, mode: int = 100) -> bytes:
    return json.dumps(
        {
            "region_status": region_status,
            "start": start,
            "end": end,
            "mode": mode,
            "md5": HASH,
        }
    ).encode()


def _cloud() -> _Cloud:
    return _Cloud(
        {
            # Shape of a real LiDAX Ultra 1000 summary: zone 1 mowed to the end.
            MON: _summary([[1, 2]], 1792012240, 1792022742),
            # Zone 3 finished, zone 2 interrupted.
            TUE: _summary([[3, 2], [2, 1]], 1792080000, 1792090000),
            # Point patrols report point_status, never region_status.
            PATROL: json.dumps({"point_status": [[1, 2]], "mode": 107}).encode(),
        },
        [
            _record(100, MON, 1000),
            _record(100, TUE, 2000),
            _record(107, PATROL, 3000),
        ],
    )


def test_region_status_becomes_one_record_per_zone() -> None:
    records = zone_mowing_records(
        {
            "region_status": [[2, 2], [1, 1], [2, 1], ["x", 2], [0, 2], [4]],
            "start": 10,
            "end": 20,
            "mode": 100,
            "md5": HASH,
        },
        MON,
    ).records

    assert records == (
        ZoneMowingRecord(2, 2, 10, 20, 100, MON, HASH),
        ZoneMowingRecord(1, 1, 10, 20, 100, MON, HASH),
    )
    assert [record.completed for record in records] == [True, False]
    assert zone_mowing_records({"point_status": [[1, 2]]}, PATROL).records == ()
    assert not zone_mowing_records(None, PATROL).understood


def test_history_reads_each_summary_once_and_reuses_known_ones() -> None:
    cloud = _cloud()
    downloads: list[str] = []
    original = cloud.async_get_interim_file_url

    async def counting(did, model, object_name, **kwargs):
        downloads.append(object_name)
        return await original(did, model, object_name, **kwargs)

    cloud.async_get_interim_file_url = counting
    client = _client(cloud)

    first = asyncio.run(async_get_zone_mowing_records(client))
    assert set(first.sessions) == {MON, TUE, PATROL}
    assert first.sessions[PATROL] == ()
    assert sorted(downloads) == sorted([MON, TUE, PATROL])

    downloads.clear()
    second = asyncio.run(async_get_zone_mowing_records(client, known=first))
    assert second == first
    assert downloads == []


def test_latest_completion_ignores_newer_interrupted_runs() -> None:
    sessions = asyncio.run(async_get_zone_mowing_records(_client(_cloud())))
    sessions.sessions["later"] = (
        ZoneMowingRecord(1, 1, 1792100000, 1792101000, 100, "x", HASH),
    )

    latest = latest_zone_mowing(sessions.sessions, map_hash=HASH)

    assert latest[1]["latest"].status == 1
    assert latest[1]["completed"].ended_at == 1792022742
    assert latest[2]["completed"] is None
    assert latest[3]["completed"].ended_at == 1792090000


def test_overview_puts_never_completed_zones_first_then_oldest() -> None:
    sessions = asyncio.run(async_get_zone_mowing_records(_client(_cloud())))

    overview = zone_mowing_overview(ZONES, sessions.sessions, map_hash=HASH)

    assert [item["zone_id"] for item in overview] == [2, 1, 3]
    assert overview[0]["last_completed_at"] is None
    assert overview[0]["last_status"] == 1
    assert overview[1]["last_completed_at"] == "2026-10-15T00:05:42+00:00"
    assert overview[2]["last_completed_at"] == "2026-10-15T18:46:40+00:00"
    assert "_order" not in overview[0]


def test_overview_without_history_keeps_zone_order() -> None:
    assert [item["zone_id"] for item in zone_mowing_overview(ZONES, None)] == [
        1,
        2,
        3,
    ]


def test_unattributed_history_does_not_transfer_completion_between_maps() -> None:
    sessions = {MON: (ZoneMowingRecord(1, 2, 10, 20, 100, MON),)}
    # Repeated numeric zone IDs do not establish which stored map was mowed.
    overview = zone_mowing_overview([{"area_id": 1, "label": "Other garden"}], sessions)
    assert overview[0]["last_completed_at"] is None


def test_late_summary_failure_preserves_successful_downloads_for_next_refresh() -> None:
    cloud = _cloud()
    cloud.records = [_record(100, MON, 3000), _record(100, TUE, 2000)]
    original = cloud.async_get_public_file
    downloads = []

    async def download(url, **kwargs):
        downloads.append(url)
        if TUE in url:
            raise TimeoutError("late response")
        return await original(url, **kwargs)

    cloud.async_get_public_file = download
    first = asyncio.run(async_get_zone_mowing_records(_client(cloud)))
    assert MON in first.sessions
    assert not first.complete
    downloads.clear()
    second = asyncio.run(async_get_zone_mowing_records(_client(cloud), known=first))
    assert MON in second.sessions
    assert all(MON not in url for url in downloads)


def _maps():
    from .test_native_zone_labels import _maps as native_maps

    maps = native_maps()
    for entry, revision in zip(maps["maps"], (HASH, OTHER_HASH), strict=True):
        entry.update(md5=revision, info={"hash": revision})
    return maps


@pytest.mark.parametrize("invalid", ["hash", "stale", "duplicate", "unverified"])
def test_map_revision_requires_current_unique_verified_slot(invalid) -> None:
    maps = _maps()
    assert selected_map_revision(maps, 0) == HASH
    if invalid == "hash":
        maps["maps"][0]["md5"] = OTHER_HASH
    elif invalid == "stale":
        maps["current_map_index"] = 1
    elif invalid == "duplicate":
        maps["maps"][1].update(md5=HASH, info={"hash": HASH})
    else:
        maps["maps"][0]["hash_match"] = False
    assert selected_map_revision(maps, 0) is None


def test_sensor_binds_map_identity_and_suppresses_incomplete_history() -> None:
    from .test_native_zone_labels import _vector

    sensor = object.__new__(DreameLawnMowerLeastRecentlyMowedZoneSensor)
    records = tuple(
        ZoneMowingRecord(zone, 2, 10, end, 100, MON, HASH)
        for zone, end in ((1, 30), (3, 20))
    )
    sensor.coordinator = SimpleNamespace(
        data=SimpleNamespace(available=True),
        selected_map_index=0,
        app_maps=_maps(),
        batch_device_data=None,
        vector_map_details=_vector(),
        zone_mowing_sessions=ZoneMowingHistory({MON: records}),
    )
    assert sensor.available
    assert sensor.native_value == "Zone #3"
    assert sensor.extra_state_attributes["zone_id"] == 3
    # Same numeric zones on another map cannot inherit the old completion dates.
    sensor.coordinator.selected_map_index = 1
    sensor.coordinator.app_maps["current_map_index"] = 1
    assert not sensor.available
    assert sensor.native_value is None
    assert sensor.extra_state_attributes["unknown_zone_ids"] == [1, 3]
    sensor.coordinator.selected_map_index = 0
    sensor.coordinator.app_maps["current_map_index"] = 0
    # Prioritize missing history without inventing the zone's last completion.
    only_zone_3 = (ZoneMowingRecord(3, 2, 10, 20, 100, MON, HASH),)
    sensor.coordinator.zone_mowing_sessions = ZoneMowingHistory({MON: only_zone_3})
    assert sensor.available
    assert sensor.native_value == "Courtyard (#1)"
    assert sensor.extra_state_attributes["zone_id"] == 1
    assert sensor.extra_state_attributes["last_completed_at"] is None
    assert sensor.extra_state_attributes["unknown_zone_ids"] == [1]
    # A completion outside the active zones cannot make an all-unknown view ready.
    unrelated_zone = (ZoneMowingRecord(99, 2, 10, 20, 100, MON, HASH),)
    sensor.coordinator.zone_mowing_sessions = ZoneMowingHistory({MON: unrelated_zone})
    assert not sensor.available
    assert sensor.native_value is None
    sensor.coordinator.zone_mowing_sessions = ZoneMowingHistory({MON: records}, (TUE,))
    assert not sensor.available
    assert not sensor.extra_state_attributes["history_complete"]


@pytest.mark.parametrize("invalid_end", [None, -1, 1792090000000, 10**30, 5])
def test_invalid_or_reversed_end_does_not_claim_completed_mowing(invalid_end) -> None:
    records = zone_mowing_records(
        {
            "region_status": [[1, 2]],
            "start": 10,
            "end": invalid_end,
            "mode": 100,
            "md5": HASH,
        },
        MON,
    )
    records = records.records
    assert not records[0].completed
    overview = zone_mowing_overview(ZONES, {MON: records}, map_hash=HASH)
    assert all(item["last_completed_at"] is None for item in overview)


@pytest.mark.parametrize("mode", [101, 103, 104, 107, 108])
def test_edge_spot_and_patrol_do_not_count_as_whole_zone_mowing(mode) -> None:
    decoded = zone_mowing_records(
        {"mode": mode, "region_status": [[1, 2]], "start": 10, "end": 20},
        MON,
    )
    assert decoded.records == ()
    assert decoded.understood


def test_history_hydration_resumes_without_starving_after_failure() -> None:
    names = [f"ali_dreame/2026/10/15/IQ000001/-1234_{n}.json" for n in range(8)]
    cloud = _Cloud(
        {name: _summary([[1, 2]], 10, 20) for name in names},
        [_record(100, name, 100 - n) for n, name in enumerate(names)],
    )
    calls = []
    original = cloud.async_get_interim_file_url

    async def sign(did, model, name, **kwargs):
        calls.append(name)
        if name == names[0]:
            raise TimeoutError("unavailable newest summary")
        return await original(did, model, name, **kwargs)

    cloud.async_get_interim_file_url = sign
    client = _client(cloud)
    history = asyncio.run(async_get_zone_mowing_records(client))
    assert not history.complete
    assert history.pending[-1] == names[0]
    calls.clear()
    history = asyncio.run(async_get_zone_mowing_records(client, known=history))
    assert len(calls) == 5
    assert len(history.sessions) == 5
    assert names[0] not in calls
    # Removed cloud events also leave the bounded in-memory summary cache.
    cloud.records = [_record(100, names[1], 100)]
    history = asyncio.run(async_get_zone_mowing_records(client, known=history))
    assert history.complete
    assert list(history.sessions) == [names[1]]


def test_cancelled_history_read_propagates_to_native_request_owner() -> None:
    cloud = _cloud()

    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError

    cloud.async_get_public_file = cancelled
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(async_get_zone_mowing_records(_client(cloud)))


def test_failed_history_refresh_marks_retained_cache_incomplete_and_backs_off() -> None:
    from custom_components.dreame_lawn_mower.coordinator import (
        DreameLawnMowerCoordinator,
    )

    coordinator = object.__new__(DreameLawnMowerCoordinator)
    record = ZoneMowingRecord(1, 2, 10, 20, 100, MON, HASH)
    coordinator.zone_mowing_sessions = ZoneMowingHistory({MON: (record,)})
    coordinator.zone_mowing_sessions_refreshed_at = None
    cloud_read = AsyncMock(side_effect=TimeoutError("history endpoint"))
    coordinator.client = SimpleNamespace(
        _descriptor=SimpleNamespace(did="did"),
        _async_cloud_read=cloud_read,
    )

    async def scenario():
        retained = await coordinator.async_refresh_zone_mowing_sessions()
        assert not retained.complete
        assert retained.sessions[MON] == (record,)
        assert coordinator.zone_mowing_sessions_refreshed_at is not None
        await coordinator.async_refresh_zone_mowing_sessions()
        assert cloud_read.await_count == 1

    asyncio.run(scenario())


def test_zone_history_task_coalesces_and_is_drained_before_client_shutdown() -> None:
    from custom_components.dreame_lawn_mower.coordinator import (
        DreameLawnMowerCoordinator,
    )

    async def scenario():
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def read(**kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        coordinator.hass = SimpleNamespace(
            async_create_background_task=lambda coro, name: asyncio.create_task(coro),
        )
        coordinator._shutting_down = False
        coordinator._metadata_refresh_task = None
        coordinator._metadata_shutdown_close_task = None
        coordinator.async_update_listeners = Mock()
        coordinator.async_refresh_zone_mowing_sessions = read
        coordinator._schedule_zone_history_refresh()
        task = coordinator._zone_history_refresh_task
        await started.wait()
        coordinator._schedule_zone_history_refresh()
        assert coordinator._zone_history_refresh_task is task
        assert await coordinator._async_drain_metadata_for_shutdown()
        assert cancelled.is_set()
        assert task.cancelled()
        assert coordinator._zone_history_refresh_task is None
        coordinator.async_update_listeners.assert_not_called()

    asyncio.run(scenario())


def test_shutdown_retires_photo_owner_and_cancels_zone_history() -> None:
    from custom_components.dreame_lawn_mower.coordinator import (
        DreameLawnMowerCoordinator,
    )
    from custom_components.dreame_lawn_mower.real_scene_cache import RealSceneCache

    async def scenario():
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        photo_started, photo_stopped = asyncio.Event(), asyncio.Event()
        history_started, history_stopped = asyncio.Event(), asyncio.Event()

        async def owned_read(started, stopped):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        photo = RealSceneCache(coordinator)
        photo._task = asyncio.create_task(owned_read(photo_started, photo_stopped))
        history = asyncio.create_task(owned_read(history_started, history_stopped))
        coordinator._real_scene_owner = photo
        coordinator._zone_history_refresh_task = history
        coordinator._metadata_refresh_task = None
        coordinator._metadata_shutdown_close_task = None
        coordinator._client_update_task = None
        coordinator.client = SimpleNamespace(set_update_callback=Mock())
        coordinator._async_shutdown_connectivity_recovery = AsyncMock()
        await photo_started.wait()
        await history_started.wait()

        await coordinator._async_stop_owned_tasks()
        assert await coordinator._async_drain_metadata_for_shutdown()

        assert photo_stopped.is_set()
        assert history_stopped.is_set()
        assert photo._closed and photo._task is None
        assert history.cancelled()
        assert coordinator._metadata_refresh_publish is False
        coordinator.client.set_update_callback.assert_called_once_with(None)

    asyncio.run(scenario())


def test_history_background_worker_contains_listener_failure() -> None:
    from custom_components.dreame_lawn_mower.coordinator import (
        DreameLawnMowerCoordinator,
    )

    async def scenario():
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        coordinator._shutting_down = False
        coordinator.zone_mowing_sessions = None
        coordinator.hass = SimpleNamespace(
            async_create_background_task=lambda coro, name: asyncio.create_task(coro),
        )
        coordinator.async_refresh_zone_mowing_sessions = AsyncMock(
            return_value=ZoneMowingHistory(),
        )
        coordinator.async_update_listeners = Mock(
            side_effect=ValueError("listener failed")
        )
        coordinator._schedule_zone_history_refresh()
        task = coordinator._zone_history_refresh_task
        assert await task is None
        assert coordinator._zone_history_refresh_task is None
        coordinator.async_update_listeners.assert_called_once_with()

    asyncio.run(scenario())


@pytest.mark.parametrize("shape", ["outcomes_missing", "mode_missing", "partial_rows"])
def test_unknown_newer_summary_keeps_old_completions_from_becoming_authoritative(
    shape,
) -> None:
    old = {
        "mode": 100,
        "md5": HASH,
        "start": 10,
        "end": 20,
        "region_status": [[1, 2], [3, 2]],
    }
    newer = {"mode": 100, "md5": HASH, "start": 30, "end": 40}
    if shape == "mode_missing":
        newer.pop("mode")
        newer["region_status"] = [[1, 2]]
    elif shape == "partial_rows":
        newer["region_status"] = [[1, 2], ["undecoded", 2]]
    cloud = _Cloud(
        {MON: json.dumps(old).encode(), TUE: json.dumps(newer).encode()},
        [_record(100, TUE, 2000), _record(100, MON, 1000)],
    )
    first = asyncio.run(async_get_zone_mowing_records(_client(cloud)))
    assert MON in first.sessions
    assert not first.complete
    assert TUE in first.pending
    if shape == "partial_rows":
        assert first.sessions[TUE][0].zone_id == 1
    # A later complete upload can resolve the unknown outcome without losing
    # cached summaries or permanently accepting the incomplete upload.
    cloud.files[TUE] = json.dumps({**old, "start": 30, "end": 40}).encode()
    second = asyncio.run(async_get_zone_mowing_records(_client(cloud), known=first))
    assert second.complete
    assert (
        latest_zone_mowing(second.sessions, map_hash=HASH)[1]["completed"].ended_at
        == 40
    )


def _history_coordinator(monkeypatch, reads):
    """Coordinator whose forced history reads return ``reads`` in order."""
    from custom_components.dreame_lawn_mower import coordinator_refresh
    from custom_components.dreame_lawn_mower.coordinator import (
        DreameLawnMowerCoordinator,
    )

    monkeypatch.setattr(
        coordinator_refresh, "ZONE_HISTORY_AFTER_MISSION_DELAYS", (0, 0, 0)
    )
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._shutting_down = False
    coordinator._zone_history_refresh_task = None
    coordinator._zone_history_mission_baseline = None
    coordinator._last_mission_active = None
    coordinator._zone_history_force_next = False
    coordinator.zone_mowing_sessions = ZoneMowingHistory({"old": ()})
    coordinator.hass = SimpleNamespace(
        async_create_background_task=lambda coro, name: asyncio.create_task(coro),
    )
    coordinator.async_update_listeners = Mock()
    forced = []

    async def refresh(*, force):
        if force:
            forced.append(force)
            if reads:
                coordinator.zone_mowing_sessions = reads.pop(0)
        return coordinator.zone_mowing_sessions

    coordinator.async_refresh_zone_mowing_sessions = refresh
    return coordinator, forced


def test_mission_end_reads_history_until_the_new_summary_lands(monkeypatch) -> None:
    async def scenario():
        coordinator, forced = _history_coordinator(
            monkeypatch,
            [
                ZoneMowingHistory({"old": ()}),
                ZoneMowingHistory({"old": (), "new": ()}, ("new",)),
                ZoneMowingHistory({"old": (), "new": ()}),
            ],
        )
        coordinator._observe_mission_end(True)
        coordinator._observe_mission_end(False)
        await coordinator._zone_history_refresh_task
        # Immediate read unchanged, then still pending, then decoded: stop.
        assert len(forced) == 3
        assert coordinator._zone_history_mission_baseline is None
        assert coordinator.async_update_listeners.call_count == 3

    asyncio.run(scenario())


def test_mission_end_follow_up_is_bounded(monkeypatch) -> None:
    async def scenario():
        coordinator, forced = _history_coordinator(monkeypatch, [])
        coordinator._observe_mission_end(True)
        coordinator._observe_mission_end(False)
        await coordinator._zone_history_refresh_task
        # One immediate read, then one per configured delay.
        assert len(forced) == 4
        assert coordinator._zone_history_mission_baseline is None

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "states", [(True, None, True), (False, False), (None, False), (True, True)]
)
def test_charging_pause_or_idle_does_not_follow_up(monkeypatch, states) -> None:
    async def scenario():
        coordinator, forced = _history_coordinator(monkeypatch, [])
        for state in states:
            coordinator._observe_mission_end(state)
        assert coordinator._zone_history_refresh_task is None
        assert forced == []

    asyncio.run(scenario())


def test_manual_refresh_forces_one_read(monkeypatch) -> None:
    async def scenario():
        coordinator, forced = _history_coordinator(
            monkeypatch, [ZoneMowingHistory({"old": (), "new": ()})]
        )
        coordinator._schedule_zone_history_refresh(force=True)
        await coordinator._zone_history_refresh_task
        assert forced == [True]
        assert coordinator._zone_history_force_next is False
        assert set(coordinator.zone_mowing_sessions.sessions) == {"old", "new"}

    asyncio.run(scenario())


def test_manual_refresh_during_a_running_read_is_not_lost(monkeypatch) -> None:
    async def scenario():
        coordinator, forced = _history_coordinator(monkeypatch, [])
        gate = asyncio.Event()
        calls = []

        async def refresh(*, force):
            calls.append(force)
            if len(calls) == 1:
                await gate.wait()
            return coordinator.zone_mowing_sessions

        coordinator.async_refresh_zone_mowing_sessions = refresh
        coordinator._schedule_zone_history_refresh()
        task = coordinator._zone_history_refresh_task
        await asyncio.sleep(0)
        coordinator._schedule_zone_history_refresh(force=True)
        assert coordinator._zone_history_refresh_task is task
        gate.set()
        await task
        assert calls == [False, True]

    asyncio.run(scenario())


def test_push_updates_catch_up_on_due_history() -> None:
    from datetime import UTC, datetime, timedelta

    from custom_components.dreame_lawn_mower.coordinator import (
        DreameLawnMowerCoordinator,
    )

    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator.zone_mowing_sessions = None
    coordinator.zone_mowing_sessions_refreshed_at = None
    # Never hydrated: the metadata barrier keeps ownership of the first read.
    assert coordinator._zone_history_due() is False
    coordinator.zone_mowing_sessions = ZoneMowingHistory({"old": ()})
    coordinator.zone_mowing_sessions_refreshed_at = datetime.now(UTC)
    assert coordinator._zone_history_due() is False
    coordinator.zone_mowing_sessions_refreshed_at -= timedelta(minutes=6)
    assert coordinator._zone_history_due() is True


def test_refresh_history_button_queues_a_forced_read() -> None:
    from custom_components.dreame_lawn_mower.button import (
        DreameLawnMowerRefreshMowingHistoryButton,
    )

    entity = object.__new__(DreameLawnMowerRefreshMowingHistoryButton)
    schedule = Mock()
    entity.coordinator = SimpleNamespace(_schedule_zone_history_refresh=schedule)
    asyncio.run(entity.async_press())
    schedule.assert_called_once_with(force=True)


def test_mission_end_during_a_running_read_is_followed(monkeypatch) -> None:
    async def scenario():
        coordinator, _ = _history_coordinator(monkeypatch, [])
        gate = asyncio.Event()
        calls = []
        arrived = ZoneMowingHistory({"old": (), "new": ()})

        async def refresh(*, force):
            calls.append(force)
            if len(calls) == 1:
                await gate.wait()
            elif len(calls) == 3:
                coordinator.zone_mowing_sessions = arrived
            return coordinator.zone_mowing_sessions

        coordinator.async_refresh_zone_mowing_sessions = refresh
        coordinator._schedule_zone_history_refresh()
        task = coordinator._zone_history_refresh_task
        await asyncio.sleep(0)
        coordinator._observe_mission_end(True)
        coordinator._observe_mission_end(False)
        assert coordinator._zone_history_refresh_task is task
        gate.set()
        await task
        # Regular read, forced catch-up, then follow-up until the summary lands.
        assert calls == [False, True, True]
        assert coordinator._zone_history_mission_baseline is None

    asyncio.run(scenario())


def test_manual_refresh_wakes_post_mission_delay(monkeypatch) -> None:
    """A button press bypasses the retry delay through the same worker."""
    from custom_components.dreame_lawn_mower import coordinator_refresh

    async def scenario():
        coordinator, forced = _history_coordinator(monkeypatch, [])
        monkeypatch.setattr(
            coordinator_refresh, "ZONE_HISTORY_AFTER_MISSION_DELAYS", (180,)
        )
        waiting = asyncio.Event()
        original = coordinator._ended_mission_summary_read

        def summary_read(baseline):
            waiting.set()
            return original(baseline)

        coordinator._ended_mission_summary_read = summary_read
        coordinator._observe_mission_end(True)
        coordinator._observe_mission_end(False)
        task = coordinator._zone_history_refresh_task
        try:
            await waiting.wait()
            coordinator._schedule_zone_history_refresh(force=True)
            await asyncio.wait_for(task, 1)
            assert forced == [True, True]
            assert coordinator._zone_history_refresh_task is None
        finally:
            if not task.done():
                task.cancel()

    asyncio.run(scenario())


def test_polled_mission_end_starts_history_followup(monkeypatch) -> None:
    """Polling observes the same mission boundary as realtime pushes."""
    async def scenario():
        coordinator, forced = _history_coordinator(monkeypatch, [])
        coordinator._last_mission_active = True
        snapshot = SimpleNamespace(
            available=True, activity="docked", docked=True,
            mowing_session_active=False,
        )
        coordinator.runtime_telemetry_cache = SimpleNamespace()
        coordinator.client = SimpleNamespace(
            async_refresh=AsyncMock(return_value=snapshot),
            update_runtime_live_tracking=Mock(),
        )
        coordinator._observe_runtime_mission_boundary = Mock(return_value=False)
        coordinator._schedule_metadata_refresh = Mock()
        result = await coordinator._async_update_data()
        assert result is snapshot
        assert coordinator._last_mission_active is False
        assert coordinator._zone_history_refresh_task is not None
        await coordinator._zone_history_refresh_task
        assert forced == [True] * 4

    asyncio.run(scenario())

def test_overview_carries_each_zone_area() -> None:
    zones = [
        {"area_id": 1, "label": "Zone #1", "area_m2": 120.5},
        {"area_id": 2, "label": "Zone #2", "area_m2": None},
    ]
    overview = zone_mowing_overview(zones, None)
    assert [(item["zone_id"], item["area_m2"]) for item in overview] == [
        (1, 120.5),
        (2, None),
    ]
