"""Contract tests for read-only task session photo downloads."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    DreameLawnMowerConnectionError,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.session_photos import (  # noqa: E501
    async_get_session_photos,
    session_photo_names,
    session_photo_object_name,
    task_sessions_from_history,
)
from custom_components.dreame_lawn_mower.lawn_mower import DreameLawnMower

SUMMARY = "ali_dreame/2026/10/15/IQ000001/-1234_010301701.0529.json"
OLDER = "ali_dreame/2026/10/15/IQ000001/-1234_004547682.0529.json"


def _record(task: object, summary: object, created: int) -> dict:
    return {
        "history": json.dumps([
            {"piid": 1, "value": task},
            {"piid": 8, "value": 1792026016},
            {"piid": 9, "value": summary},
        ]),
        "createTime": created,
    }


class _Cloud:
    def __init__(self, files: dict[str, bytes], records: list[dict]) -> None:
        self.files = files
        self.records = records
        self.history_calls: list[tuple] = []

    async def async_get_property_history(self, did, key, **kwargs):
        self.history_calls.append((did, key, kwargs))
        return self.records

    async def async_get_interim_file_url(self, did, model, object_name, **kwargs):
        return f"https://signed/{object_name}" if object_name in self.files else None

    async def async_get_public_file(self, url, **kwargs):
        return self.files[url.removeprefix("https://signed/")]


def _client(cloud: _Cloud) -> SimpleNamespace:
    async def cloud_read(read):
        return await read(cloud)

    return SimpleNamespace(
        _descriptor=SimpleNamespace(did="did", model="mova.mower.g2529c"),
        _async_cloud_read=cloud_read,
    )


def _cloud() -> _Cloud:
    return _Cloud(
        {
            SUMMARY: json.dumps(
                {"photo_list": ["1.jpg", "2.jpg", "2.jpg"], "mode": 107}
            ).encode(),
            OLDER: json.dumps({"photo_list": []}).encode(),
            "IQ000001/-1234/ali_dreame/1.jpg": b"\xff\xd8one",
            "IQ000001/-1234/ali_dreame/2.jpg": b"\xff\xd8two",
        },
        [_record(108, OLDER, 1000), _record(107, SUMMARY, 2000)],
    )


def test_session_events_decode_newest_first_and_skip_malformed_records() -> None:
    sessions = task_sessions_from_history([
        _record(108, OLDER, 1000),
        {"history": "not json"},
        _record(107, "ali_dreame/no-summary.txt", 3000),
        "garbage",
        _record(107, SUMMARY, 2000),
    ])

    assert [(item.task, item.object_name) for item in sessions] == [
        (107, SUMMARY),
        (108, OLDER),
    ]
    assert task_sessions_from_history(None) == []


def test_photo_object_name_follows_the_vendor_album_layout() -> None:
    assert (
        session_photo_object_name(SUMMARY, "1792026162.jpg")
        == "IQ000001/-1234/ali_dreame/1792026162.jpg"
    )
    assert session_photo_object_name(SUMMARY, "") is None
    assert session_photo_object_name(SUMMARY, "../x.jpg") is None
    assert session_photo_object_name("summary.json", "1.jpg") is None
    assert session_photo_object_name("ali_dreame/IQ1/plain.json", "1.jpg") is None


def test_photo_names_are_deduplicated_in_vendor_order() -> None:
    assert session_photo_names({"photo_list": ["b.jpg", "a.jpg", "b.jpg", 3]}) == [
        "b.jpg",
        "a.jpg",
    ]
    assert session_photo_names({"photo_list": None}) == []
    assert session_photo_names([]) == []


async def test_session_photos_read_event_history_and_summaries() -> None:
    cloud = _cloud()

    photos = await async_get_session_photos(_client(cloud), session_count=2)

    assert cloud.history_calls[0][1] == "4.1"
    assert cloud.history_calls[0][2]["kind"] == "event"
    assert cloud.history_calls[0][2]["limit"] == 2
    assert [(photo.file_name, photo.session.task) for photo in photos] == [
        ("1.jpg", 107),
        ("2.jpg", 107),
    ]


def _entity(tmp_path: Path, cloud: _Cloud) -> DreameLawnMower:
    async def executor(func, *args):
        return func(*args)

    entity = object.__new__(DreameLawnMower)
    entity.coordinator = SimpleNamespace(client=_client(cloud))
    entity.hass = SimpleNamespace(
        config=SimpleNamespace(
            media_dirs={"local": str(tmp_path)},
            path=lambda *parts: str(tmp_path.joinpath(*parts)),
        ),
        async_add_executor_job=executor,
    )
    entity.entity_id = "lawn_mower.my_mower"
    return entity


async def test_download_service_saves_new_photos_to_media_and_skips_existing(
    tmp_path: Path,
) -> None:
    entity = _entity(tmp_path, _cloud())
    folder = tmp_path / "dreame_lawn_mower" / "my_mower"

    first = await entity.async_download_session_photos()

    assert (folder / "1.jpg").read_bytes() == b"\xff\xd8one"
    assert (folder / "2.jpg").read_bytes() == b"\xff\xd8two"
    assert [item["new"] for item in first["photos"]] == [True, True]
    assert not list(folder.glob(".*.part"))

    (folder / "1.jpg").write_bytes(b"kept")
    second = await entity.async_download_session_photos()

    assert [item["new"] for item in second["photos"]] == [False, False]
    assert (folder / "1.jpg").read_bytes() == b"kept"


async def test_download_service_reports_cloud_failures(tmp_path: Path) -> None:
    entity = _entity(tmp_path, _cloud())

    async def failing(read):
        raise DreameLawnMowerConnectionError("offline")

    entity.coordinator.client._async_cloud_read = failing

    with pytest.raises(HomeAssistantError, match="Session photos unavailable"):
        await entity.async_download_session_photos()
