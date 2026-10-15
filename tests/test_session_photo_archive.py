"""Archive quota remains authoritative while cancelled executor work settles."""

import asyncio
import json
import threading
from pathlib import Path

import pytest

from custom_components.dreame_lawn_mower import session_photo_archive as archive
from tests.test_session_photos import SUMMARY, _cloud, _entity


def test_cancelled_write_cannot_overfill_archive_after_entity_replacement(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        entered, release = threading.Event(), threading.Event()
        sampled = asyncio.Event()
        write = archive._write_photo

        def blocked_write(target, content):
            if content == b"\xff\xd8one":
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test failed to release writer")
            return write(target, content)

        monkeypatch.setattr(archive, "_write_photo", blocked_write)
        monkeypatch.setattr(archive, "MAX_ARCHIVE_BYTES", 5)
        first_cloud, second_cloud = _cloud(), _cloud()
        first_cloud.files[SUMMARY] = json.dumps({"photo_list": ["1.jpg"]}).encode()
        second_cloud.files[SUMMARY] = json.dumps({"photo_list": ["2.jpg"]}).encode()
        first = _entity(tmp_path, first_cloud)
        first.hass.async_add_executor_job = asyncio.to_thread
        replacement = _entity(tmp_path, second_cloud)

        async def executor(func, *args):
            result = await asyncio.to_thread(func, *args)
            if func is archive._archive_size:
                sampled.set()
            return result

        replacement.hass.async_add_executor_job = executor
        task = asyncio.create_task(first.async_download_session_photos())
        try:
            await asyncio.wait_for(asyncio.to_thread(entered.wait), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            next_task = asyncio.create_task(replacement.async_download_session_photos())
            await asyncio.wait_for(sampled.wait(), 2)
            release.set()
            response = await asyncio.wait_for(next_task, 2)
            assert response == {"photos": [], "limited": True}
            assert [file.read_bytes() for file in tmp_path.rglob("*.jpg")] == [
                b"\xff\xd8one",
            ]
        finally:
            release.set()

    asyncio.run(scenario())


def test_usage_scan_tolerates_staging_file_disappearing(tmp_path, monkeypatch):
    partial = tmp_path / ".photo-pending.part"
    partial.write_bytes(b"pending")
    original = Path.is_file

    def disappearing_file(path):
        result = original(path)
        if path == partial and result:
            partial.unlink()
        return result

    monkeypatch.setattr(Path, "is_file", disappearing_file)
    assert archive._archive_size(tmp_path) == 0
