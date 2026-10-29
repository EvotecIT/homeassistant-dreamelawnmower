"""Contract tests for read-only task session photo downloads."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.dreame_lawn_mower import session_photo_archive as archive
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    DreameLawnMowerConnectionError,
    public_download,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.http_response import (
    HttpResponseError,
    async_read_bounded_response,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.session_photos import (  # noqa: E501
    SessionPhoto,
    TaskSession,
    async_get_session_photos,
    person_photo_object_names,
    session_photo_names,
    session_photo_object_name,
    task_sessions_from_history,
)
from custom_components.dreame_lawn_mower.lawn_mower import DreameLawnMower
from custom_components.dreame_lawn_mower.session_photo_archive import (
    photo_archive_target,
)

SUMMARY = "ali_dreame/2026/10/15/IQ000001/-1234_010301701.0529.json"
OLDER = "ali_dreame/2026/10/15/IQ000001/-1234_004547682.0529.json"


def _record(task: object, summary: object, created: int) -> dict:
    return {
        "history": json.dumps(
            [
                {"piid": 1, "value": task},
                {"piid": 8, "value": 1792026016},
                {"piid": 9, "value": summary},
            ]
        ),
        "createTime": created,
    }


class _Cloud:
    def __init__(self, files: dict[str, bytes | None], records: list[dict]) -> None:
        self.files = files
        self.records = records
        self.history_calls: list[tuple] = []
        self.missing_status = 404

    async def async_get_property_history(self, did, key, **kwargs):
        self.history_calls.append((did, key, kwargs))
        return self.records

    async def async_get_interim_file_url(self, did, model, object_name, **kwargs):
        return f"https://signed/{object_name}" if object_name in self.files else None

    async def async_get_public_file(self, url, **kwargs):
        content = self.files[url.removeprefix("https://signed/")]
        if content is None:
            raise public_download.PublicDownloadError(
                "object unavailable",
                reason="http_status",
                status=self.missing_status,
            )

        async def chunks(size):
            yield content

        response = SimpleNamespace(
            headers={},
            content=SimpleNamespace(iter_chunked=chunks),
        )
        return await async_read_bounded_response(
            response, max_bytes=kwargs["max_bytes"]
        )


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
    sessions = task_sessions_from_history(
        [
            _record(108, OLDER, 1000),
            {"history": "not json"},
            _record(107, "ali_dreame/no-summary.txt", 3000),
            "garbage",
            _record(107, SUMMARY, 2000),
        ]
    )

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


def test_session_photos_read_event_history_and_summaries() -> None:
    async def scenario():
        cloud = _cloud()

        photos = await async_get_session_photos(_client(cloud), session_count=2)

        assert cloud.history_calls[0][1] == "4.1"
        assert cloud.history_calls[0][2]["kind"] == "event"
        assert cloud.history_calls[0][2]["limit"] == 20
        assert [(photo.file_name, photo.session.task) for photo in photos] == [
            ("1.jpg", 107),
            ("2.jpg", 107),
        ]

    asyncio.run(scenario())


def _entity(tmp_path: Path, cloud: _Cloud) -> DreameLawnMower:
    async def executor(func, *args):
        return func(*args)

    entity = object.__new__(DreameLawnMower)
    entity._session_photo_lock = asyncio.Lock()
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


def test_download_service_saves_new_photos_to_media_and_skips_existing(
    tmp_path: Path,
) -> None:
    async def scenario():
        entity = _entity(tmp_path, _cloud())
        folder = tmp_path / "dreame_lawn_mower" / "my_mower"

        first = await entity.async_download_session_photos()

        assert Path(first["photos"][0]["file"]).read_bytes() == b"\xff\xd8one"
        assert Path(first["photos"][1]["file"]).read_bytes() == b"\xff\xd8two"
        assert [item["new"] for item in first["photos"]] == [True, True]
        assert not list(folder.rglob("*.part"))

        target = Path(first["photos"][0]["file"])
        target.write_bytes(b"kept")
        second = await entity.async_download_session_photos()

        assert [item["new"] for item in second["photos"]] == [False, False]
        assert target.read_bytes() == b"kept"

    asyncio.run(scenario())


def test_download_service_reports_cloud_failures(tmp_path: Path) -> None:
    async def scenario():
        entity = _entity(tmp_path, _cloud())

        async def failing(read):
            raise DreameLawnMowerConnectionError("offline")

        entity.coordinator.client._async_cloud_read = failing

        with pytest.raises(HomeAssistantError, match="Session photos unavailable"):
            await entity.async_download_session_photos()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "name",
    [
        "../escape.jpg",
        "..\\escape.jpg",
        "/escape.jpg",
        "C:\\escape.jpg",
        "photo.jpg:stream",
        "photo\x00.jpg",
        ".",
        "..",
        "photo.txt",
    ],
)
def test_cloud_photo_names_cannot_escape_media_storage(name: str) -> None:
    assert session_photo_names({"photo_list": [name, "safe.jpg"]}) == ["safe.jpg"]
    assert session_photo_object_name(SUMMARY, name) is None
    photo = SessionPhoto(name, "object", TaskSession(SUMMARY, 107, 2000))
    with pytest.raises(ValueError, match="filename"):
        photo_archive_target(Path("media"), photo)


def test_invalid_property_ids_do_not_hide_valid_session_history() -> None:
    record = _record(107, SUMMARY, 2000)
    values = json.loads(record["history"])
    values.extend([{"piid": [], "value": 1}, {"piid": {}, "value": 2}])
    record["history"] = json.dumps(values)
    assert task_sessions_from_history([record]) == [
        TaskSession(SUMMARY, 107, 2000, started_at=1792026016)
    ]


def test_unusable_newest_summary_does_not_hide_older_photos() -> None:
    cloud = _cloud()
    cloud.records.insert(0, _record(107, "ali_dreame/IQ1/missing_1.json", 3000))
    photos = asyncio.run(async_get_session_photos(_client(cloud)))
    assert [photo.file_name for photo in photos] == ["1.jpg", "2.jpg"]


def test_oversized_summary_does_not_hide_older_photos() -> None:
    cloud = _cloud()
    newest = "ali_dreame/IQ1/large_1.json"
    cloud.records.insert(0, _record(107, newest, 3000))
    cloud.files[newest] = b"x" * (1024 * 1024 + 1)
    photos = asyncio.run(async_get_session_photos(_client(cloud)))
    assert [photo.file_name for photo in photos] == ["1.jpg", "2.jpg"]


def test_reused_filename_keeps_distinct_cloud_objects_separate(tmp_path: Path) -> None:
    first = SessionPhoto(
        "1.jpg", "IQ1/first/ali_dreame/1.jpg", TaskSession(SUMMARY, 107, 1)
    )
    second = SessionPhoto(
        "1.jpg", "IQ1/second/ali_dreame/1.jpg", TaskSession(OLDER, 107, 2)
    )
    assert photo_archive_target(tmp_path, first) != photo_archive_target(
        tmp_path, second
    )


def test_overlapping_calls_save_once_and_preserve_complete_bytes(
    tmp_path: Path,
) -> None:
    async def scenario():
        entity = _entity(tmp_path, _cloud())
        first, second = await asyncio.gather(
            entity.async_download_session_photos(),
            entity.async_download_session_photos(),
        )
        assert [photo["new"] for photo in first["photos"]] == [True, True]
        assert [photo["new"] for photo in second["photos"]] == [False, False]
        assert Path(first["photos"][0]["file"]).read_bytes() == b"\xff\xd8one"
        assert not list(tmp_path.rglob("*.part"))

    asyncio.run(scenario())


def test_atomic_publication_never_replaces_a_competing_file(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "1.jpg"
    original = archive.os.link

    def competing_writer(source, destination):
        target.write_bytes(b"other writer")
        original(source, destination)

    monkeypatch.setattr(archive.os, "link", competing_writer)
    assert archive._write_photo(target, b"our bytes") is False
    assert target.read_bytes() == b"other writer"
    assert not list(tmp_path.glob("*.part"))


def test_failed_publication_cleans_up_staging_file(tmp_path: Path, monkeypatch) -> None:
    def failing_link(*args):
        raise OSError("filesystem failure")

    monkeypatch.setattr(archive.os, "link", failing_link)
    with pytest.raises(OSError, match="filesystem failure"):
        archive._write_photo(tmp_path / "1.jpg", b"photo")
    assert not list(tmp_path.iterdir())


def test_service_uses_registered_media_source_without_local(tmp_path: Path) -> None:
    entity = _entity(tmp_path, _cloud())
    entity.hass.config.media_dirs = {"photos": str(tmp_path)}
    response = asyncio.run(entity.async_download_session_photos())
    assert len(response["photos"]) == 2
    assert all(
        Path(item["file"]).is_relative_to(tmp_path) for item in response["photos"]
    )


def test_service_rejects_missing_media_source_before_cloud_access(
    tmp_path: Path,
) -> None:
    cloud = _cloud()
    entity = _entity(tmp_path, cloud)
    entity.hass.config.media_dirs = {}
    with pytest.raises(HomeAssistantError, match="media directory"):
        asyncio.run(entity.async_download_session_photos())
    assert not cloud.history_calls


def test_batch_and_archive_limits_keep_saved_photos(
    tmp_path: Path, monkeypatch
) -> None:
    entity = _entity(tmp_path, _cloud())
    monkeypatch.setattr(archive, "MAX_BATCH_BYTES", 7)
    first = asyncio.run(entity.async_download_session_photos())
    assert len(first["photos"]) == 1
    assert first["limited"] is True
    target = Path(first["photos"][0]["file"])
    monkeypatch.setattr(archive, "MAX_ARCHIVE_BYTES", 7)
    second = asyncio.run(entity.async_download_session_photos())
    assert len(second["photos"]) == 1
    assert second["photos"][0]["new"] is False
    assert second["limited"] is True
    assert target.read_bytes() == b"\xff\xd8one"


def test_transport_failure_is_not_reported_as_a_storage_limit(tmp_path, monkeypatch):
    async def failing(*args, **kwargs):
        raise HttpResponseError("incomplete body", reason="truncated_content")

    monkeypatch.setattr(archive, "async_download_session_photo", failing)
    entity = _entity(tmp_path, _cloud())
    with pytest.raises(HomeAssistantError, match="Session photos unavailable"):
        asyncio.run(entity.async_download_session_photos())


@pytest.mark.parametrize("batch_bytes,expected", [(8, [b"ok"]), (4, [])])
def test_oversized_photo_is_skipped_without_bypassing_batch_budget(
    tmp_path,
    monkeypatch,
    batch_bytes,
    expected,
):
    cloud = _cloud()
    cloud.files["IQ000001/-1234/ali_dreame/1.jpg"] = b"oversized"
    cloud.files["IQ000001/-1234/ali_dreame/2.jpg"] = b"ok"
    monkeypatch.setattr(archive, "MAX_SESSION_PHOTO_BYTES", 3)
    monkeypatch.setattr(archive, "MAX_BATCH_BYTES", batch_bytes)
    response = asyncio.run(_entity(tmp_path, cloud).async_download_session_photos())
    assert response["limited"] is True
    assert [
        Path(photo["file"]).read_bytes() for photo in response["photos"]
    ] == expected
    assert not list(tmp_path.rglob("*.part"))


@pytest.mark.parametrize("status", [404, 410, 403, 429, 500])
def test_missing_signed_objects_do_not_hide_later_photos(tmp_path, status):
    cloud = _cloud()
    cloud.missing_status = status
    missing = "ali_dreame/IQ1/deleted_1.json"
    cloud.records.insert(0, _record(107, missing, 3000))
    cloud.files[missing] = None
    cloud.files["IQ000001/-1234/ali_dreame/1.jpg"] = None
    entity = _entity(tmp_path, cloud)
    if status not in (404, 410):
        with pytest.raises(HomeAssistantError, match="Session photos unavailable"):
            asyncio.run(entity.async_download_session_photos())
        return
    response = asyncio.run(entity.async_download_session_photos())
    assert [Path(photo["file"]).read_bytes() for photo in response["photos"]] == [
        b"\xff\xd8two"
    ]
    assert response["limited"] is False


def test_listing_caps_total_photos_and_deduplicates_cloud_objects() -> None:
    cloud = _cloud()
    names = [f"{index}.jpg" for index in range(120)]
    cloud.files[SUMMARY] = json.dumps({"photo_list": names}).encode()
    cloud.records.append(_record(107, SUMMARY, 2000))
    photos = asyncio.run(async_get_session_photos(_client(cloud), session_count=3))
    assert len(photos) == 100
    assert len({photo.object_name for photo in photos}) == 100


def test_batch_timeout_keeps_successfully_saved_photos(
    tmp_path: Path, monkeypatch
) -> None:
    async def scenario():
        entity = _entity(tmp_path, _cloud())
        download = archive.async_download_session_photo

        async def delayed(client, photo, **kwargs):
            if photo.file_name == "2.jpg":
                await asyncio.Event().wait()
            return await download(client, photo, **kwargs)

        monkeypatch.setattr(archive, "async_download_session_photo", delayed)
        monkeypatch.setattr(archive, "BATCH_TIMEOUT", 0.1)
        with pytest.raises(HomeAssistantError, match="timed out"):
            await entity.async_download_session_photos()
        assert [path.read_bytes() for path in tmp_path.rglob("*.jpg")] == [
            b"\xff\xd8one"
        ]
        assert not list(tmp_path.rglob("*.part"))

    asyncio.run(scenario())


DETECTED = 1792074543
PERSON = f"IQ000001/-1234/ali_dreame/{DETECTED - 1}_person.jpg"


def _person_entity(
    tmp_path: Path, cloud: _Cloud, events: list[dict]
) -> DreameLawnMower:
    entity = _entity(tmp_path, cloud)
    entity.coordinator.mower_condition_history = SimpleNamespace(
        recent=lambda: [dict(event) for event in events]
    )
    return entity


def _detection(seconds: int = DETECTED) -> dict:
    observed = datetime.fromtimestamp(seconds + 0.79, UTC).isoformat()
    return {"code": 27, "message": "Human detected", "observed_at": observed}


def test_person_photo_candidates_start_one_second_before_detection() -> None:
    names = person_photo_object_names(SUMMARY, DETECTED + 0.79)

    assert names[:3] == [
        f"IQ000001/-1234/ali_dreame/{DETECTED + offset}_person.jpg"
        for offset in (-1, -2, 0)
    ]
    assert len(names) == len(set(names)) == 64
    assert f"IQ000001/-1234/ali_dreame/{DETECTED + 3}_person.jpg" in names
    # Observed on a LiDAX Ultra 1000: photo named 26 s before the notice.
    assert f"IQ000001/-1234/ali_dreame/{DETECTED - 26}_person.jpg" in names
    assert f"IQ000001/-1234/ali_dreame/{DETECTED - 60}_person.jpg" in names
    assert person_photo_object_names("summary.json", DETECTED) == []


def test_person_photo_service_saves_the_latest_detection_once(tmp_path: Path) -> None:
    cloud = _cloud()
    # The cloud signs any name; absent candidates answer 404.
    cloud.files[f"IQ000001/-1234/ali_dreame/{DETECTED - 2}_person.jpg"] = None
    cloud.files[PERSON] = b"\xff\xd8person"
    entity = _person_entity(
        tmp_path,
        cloud,
        [{"code": 74, "observed_at": "2026-10-15T00:45:46+00:00"}, _detection()],
    )

    first = asyncio.run(entity.async_download_person_photo())
    second = asyncio.run(entity.async_download_person_photo())

    saved = Path(first["photos"][0]["file"])
    assert saved.name == f"{DETECTED - 1}_person.jpg"
    assert saved.read_bytes() == b"\xff\xd8person"
    assert tmp_path / "dreame_lawn_mower" / "my_mower" in saved.parents
    assert first["photos"][0]["new"] is True
    assert second["photos"] == [{"file": str(saved), "new": False}]
    assert cloud.history_calls[0][2]["kind"] == "event"


def test_person_photo_service_accepts_an_explicit_detection_time(
    tmp_path: Path,
) -> None:
    cloud = _cloud()
    cloud.files[PERSON] = b"\xff\xd8person"
    entity = _person_entity(tmp_path, cloud, [])

    response = asyncio.run(
        entity.async_download_person_photo(
            datetime.fromtimestamp(DETECTED, UTC)
        )
    )

    assert Path(response["photos"][0]["file"]).read_bytes() == b"\xff\xd8person"


def test_person_photo_service_requires_an_observed_detection(tmp_path: Path) -> None:
    entity = _person_entity(tmp_path, _cloud(), [{"code": 74}])

    with pytest.raises(HomeAssistantError, match="No person detection"):
        asyncio.run(entity.async_download_person_photo())


def test_person_photo_service_reports_when_no_photo_exists(tmp_path: Path) -> None:
    entity = _person_entity(tmp_path, _cloud(), [_detection()])

    response = asyncio.run(entity.async_download_person_photo())

    assert response == {"photos": [], "limited": False}
    assert not (tmp_path / "dreame_lawn_mower").exists()
