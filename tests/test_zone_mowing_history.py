"""Contract tests for the per-zone mowing history read from session summaries."""

from __future__ import annotations

import asyncio
import json

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.session_photos import (  # noqa: E501
    ZoneMowingRecord,
    async_get_zone_mowing_records,
    latest_zone_mowing,
    zone_mowing_records,
)
from custom_components.dreame_lawn_mower.sensor_zone_history import (
    zone_mowing_overview,
)

from .test_session_photos import _client, _Cloud, _record

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
        {"region_status": region_status, "start": start, "end": end, "mode": mode}
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
        },
        MON,
    )

    assert records == (
        ZoneMowingRecord(2, 2, 10, 20, 100, MON),
        ZoneMowingRecord(1, 1, 10, 20, 100, MON),
    )
    assert [record.completed for record in records] == [True, False]
    assert zone_mowing_records({"point_status": [[1, 2]]}, PATROL) == ()
    assert zone_mowing_records(None, PATROL) == ()


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
    assert set(first) == {MON, TUE, PATROL}
    assert first[PATROL] == ()
    assert sorted(downloads) == sorted([MON, TUE, PATROL])

    downloads.clear()
    second = asyncio.run(async_get_zone_mowing_records(client, known=first))
    assert second == first
    assert downloads == []


def test_latest_completion_ignores_newer_interrupted_runs() -> None:
    sessions = asyncio.run(async_get_zone_mowing_records(_client(_cloud())))
    sessions["later"] = (ZoneMowingRecord(1, 1, 1792100000, 1792101000, 100, "x"),)

    latest = latest_zone_mowing(sessions)

    assert latest[1]["latest"].status == 1
    assert latest[1]["completed"].ended_at == 1792022742
    assert latest[2]["completed"] is None
    assert latest[3]["completed"].ended_at == 1792090000


def test_overview_puts_never_completed_zones_first_then_oldest() -> None:
    sessions = asyncio.run(async_get_zone_mowing_records(_client(_cloud())))

    overview = zone_mowing_overview(ZONES, sessions)

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
