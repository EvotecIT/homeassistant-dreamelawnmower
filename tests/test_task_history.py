"""Task history contracts across private transport and normalized details."""

import asyncio
import json
from dataclasses import replace

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.session_photos import (  # noqa: E501
    TaskSession,
    task_sessions_from_history,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.task_history import (
    MAX_MAP_POINTS,
    TaskHistory,
    decode_task_detail,
    finite_number,
    task_id,
    task_mode,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.task_observations import (  # noqa: E501
    MAX_RUN_AGE,
    BatterySample,
    TaskObservations,
    battery_observation,
    capture_settings,
)
from tests.test_session_photos import SUMMARY, _client, _Cloud, _record

START = 1_792_026_016
HASH = "a" * 32


def summary():
    return {
        "start": START,
        "end": START + 300,
        "time": 5,
        "areas": 120.5,
        "mode": 102,
        "result": 1,
        "md5": HASH,
        "map": [
            {
                "id": 4,
                "type": 0,
                "data": [[0, 0], [100, 0], [0, 100]],
                "track": [
                    [10, 10],
                    [20, 20],
                    [2147483647, 2147483647],
                    [80, 80],
                    [90, 90],
                ],
            }
        ],
        "region_status": [[4, 2]],
        "faults": [[START + 10, 27]],
        "pref": [55, 1],
        "pre_type": 1,
        "photo_list": ["task.jpg"],
    }


def detail():
    return decode_task_detail(
        "private-device", TaskSession(SUMMARY, 102, START * 1000), summary()
    )


def test_historical_geometry_breaks_faults_and_settings_are_normalized():
    task = detail()
    assert task.area_sqm == 120.5
    assert task.features[0].points == ((0, 0), (1000, 0), (0, 1000))
    assert task.features[0].tracks == (
        ((100, 100), (200, 200)),
        ((800, 800), (900, 900)),
    )
    assert task.faults == ((START + 10, 27),)
    assert task.recorded_settings == {"mowing_height_cm": 5.5, "efficient_mode": 1}
    assert task.per_zone_settings
    payload = json.dumps(task.as_dict())
    assert "private-device" not in payload and "ali_dreame" not in payload
    assert task_id("another-device", TaskSession(SUMMARY, 102, None)) != task.id


def test_malformed_large_geometry_and_nonfinite_metrics_stay_unavailable():
    raw = summary()
    raw.update(areas=float("nan"), end=START - 1, time=True)
    raw["map"][0]["track"] = [[0, 0]] * (MAX_MAP_POINTS + 1)
    task = decode_task_detail("device", TaskSession(SUMMARY, None, None), raw)
    assert task.area_sqm is None and task.ended_at is None
    assert task.duration_minutes is None and not task.features[0].tracks
    assert finite_number(10**1000) is None


def test_event_area_units_follow_firmware_mode_without_altering_summary_units():
    records = []
    for mode in (2, 102):
        record = _record(mode, SUMMARY, START * 1000)
        values = json.loads(record["history"])
        values.extend([{"piid": 3, "value": 12500}, {"piid": 7, "value": 0}])
        record["history"] = json.dumps(values)
        records.append(record)
    old, current = task_sessions_from_history(records)
    assert (old.area_sqm, old.result) == (12500, 3)
    assert (current.area_sqm, current.result) == (125, 0)
    assert detail().area_sqm == 120.5


def test_history_downloads_only_discovered_sessions_and_reuses_valid_detail():
    async def run():
        cloud = _Cloud(
            {SUMMARY: json.dumps(summary()).encode()},
            [_record(102, SUMMARY, START * 1000)],
        )
        client = _client(cloud)
        client.descriptor = client._descriptor
        client.descriptor.unique_id = "mower"
        history = TaskHistory(client)
        await history.async_refresh()
        key = next(iter(history.sessions))
        assert await history.async_detail("not-owned") is None
        task = await history.async_detail(key)
        assert task.photo_count == 1
        assert history.photos[key][0].object_name.endswith("task.jpg")
        cloud.files.clear()
        assert await history.async_detail(key) is task
        await history.async_refresh()
        assert len(cloud.history_calls) == 1

    asyncio.run(run())


def test_battery_excludes_charging_rises_offline_gaps_and_unknown_zero():
    samples = [
        BatterySample(START, 80, False, True),
        BatterySample(START + 60, 75, False, True),
        BatterySample(START + 120, 90, True, True),
        BatterySample(START + 180, 88, False, True),
        BatterySample(START + 900, 60, False, True),
    ]
    result = battery_observation(samples, start=START, end=START + 900, area=100)
    assert result["percentage_points"] == 5
    assert result["percentage_points_per_sqm"] == 0.05
    assert result["covered_seconds"] == 60 and result["charging_rises"] == 1
    assert result["coverage"] == "partial"
    unknown = battery_observation([], start=START, end=START + 900, area=100)
    assert unknown["percentage_points"] is None


def observe(history, at, level, **changes):
    args = dict(
        key="mission",
        at=at,
        level=level,
        charging=False,
        active=True,
        map_revision=HASH,
        settings={"map_index": 0, "mode": 0, "zones": []},
    )
    args.update(changes)
    return history.observe(**args)


def test_observations_survive_restart_without_replacing_first_settings():
    history = TaskObservations()
    assert observe(history, START, 80)
    assert not observe(history, START + 1, 80)
    assert observe(history, START + 60, 75, settings={"map_index": 1})
    restored = TaskObservations()
    restored.restore(json.loads(json.dumps(history.export())), now=START + 300)
    assert observe(restored, START + 300, 70)
    result = restored.for_task(detail())
    # The saved-to-resumed interval is a restart gap, even under the same key.
    assert result["percentage_points"] == 5
    assert result["settings"]["map_index"] == 0
    assert result["settings_timing"] == "at_start"
    assert restored.for_task(replace(detail(), map_revision="b" * 32)) is None
    observe(restored, START + 100, 65, key="overlap")
    assert restored.for_task(detail()) is None


def test_late_observation_does_not_claim_start_settings_or_complete_coverage():
    history = TaskObservations()
    assert observe(history, START + 60, 80)
    assert observe(history, START + 300, 70)
    result = history.for_task(detail())
    assert result["settings_timing"] == "first_observed"
    assert result["coverage"] == "partial"


def test_unknown_battery_keeps_start_settings_without_inventing_reading():
    history = TaskObservations()
    assert observe(history, START, None)
    result = history.for_task(detail())
    assert result["percentage_points"] is None and result["samples"] == []
    assert result["settings_timing"] == "at_start"


def test_summary_cache_round_trip_preserves_private_history_not_signed_urls():
    cloud = _Cloud({}, [])
    client = _client(cloud)
    client.descriptor = client._descriptor
    client.descriptor.unique_id = "mower"
    history = TaskHistory(client)
    session = TaskSession(SUMMARY, 102, START * 1000, started_at=START)
    key = task_id("mower", session)
    history.sessions[key] = session
    raw = summary()
    raw["map"][0]["track"] = [[index, 0] for index in range(MAX_MAP_POINTS - 3)]
    raw["pref"] = [None, 1]
    expected = history._retain_detail(key, session, raw)
    saved = json.loads(json.dumps(history.export()))
    restored = TaskHistory(client)
    restored.restore(saved)
    assert restored.stale
    assert restored.details[key] == expected
    assert len(restored.details[key].features[0].tracks[0]) == MAX_MAP_POINTS - 3
    assert restored.details[key].recorded_settings == {"efficient_mode": 1}
    assert restored.photos[key] == history.photos[key]
    assert "https://" not in json.dumps(saved)


@pytest.mark.parametrize("raw,expected", [(0, 0), (103, 3), (4, None), (109, None)])
def test_only_supported_task_modes_are_published(raw, expected):
    assert task_mode(raw) == expected


@pytest.mark.parametrize("operation", ["unknown", "missing_session", "inactive"])
def test_observations_expire_without_a_known_active_mission(operation):
    history = TaskObservations()
    observe(history, START, 80)
    future = START + MAX_RUN_AGE + 1
    if operation == "unknown":
        changed = history.interrupt(at=future)
    else:
        changed = observe(
            history,
            future,
            80,
            key=None if operation == "missing_session" else "idle",
            active=False,
        )
    assert changed
    assert history.for_task(detail()) is None
    assert history.export()["runs"] == []


def test_settings_capture_uses_only_one_available_map_and_known_fields():
    prefs = {
        "maps": [
            {
                "idx": 2,
                "available": True,
                "mode": 1,
                "preferences": [
                    {
                        "area_id": 4,
                        "mowing_height_cm": 5.5,
                        "edge_mowing_walk_mode": 0,
                        "raw_packet": "private",
                        "url": "https://private",
                    }
                ],
            }
        ]
    }
    captured = capture_settings(prefs, 2)
    assert captured["zones"] == [
        {"area_id": 4, "mowing_height_cm": 5.5, "edge_mowing_walk_mode": 0}
    ]
    assert capture_settings(prefs, 1) == {}
    prefs["maps"].append(prefs["maps"][0])
    assert capture_settings(prefs, 2) == {}


def test_unknown_battery_interrupts_even_a_short_interval_and_survives_storage():
    history = TaskObservations()
    observe(history, START, 80)
    observe(history, START + 30, None)
    restored = TaskObservations()
    restored.restore(json.loads(json.dumps(history.export())), now=START + 40)
    observe(restored, START + 60, 70)
    observe(restored, START + 120, 65)
    result = restored.for_task(detail())
    assert result["percentage_points"] == 5
    assert result["covered_seconds"] == 60
    assert result["samples"][1]["continuous"] is False


def test_unverified_and_foreign_map_readings_do_not_acquire_later_provenance():
    history = TaskObservations()
    observe(history, START, 90, map_revision=None, settings={"map_index": 1})
    settings = {
        "map_index": 0,
        "mode": 0,
        "zones": [{"area_id": 0, "mowing_height_cm": 6.5}],
    }
    observe(history, START + 60, 80, settings=settings)
    observe(history, START + 180, 75)
    observe(history, START + 240, 60, map_revision="b" * 32)
    observe(history, START + 300, 55, settings={"map_index": 9})
    result = history.for_task(detail())
    assert result["percentage_points"] == 5
    assert [sample["level"] for sample in result["samples"]] == [80, 75, 55]
    assert result["samples"][-1]["continuous"] is False
    assert result["settings"] == settings
    assert result["settings_observed_at"] == START + 60
    assert result["settings_timing"] == "first_observed"
