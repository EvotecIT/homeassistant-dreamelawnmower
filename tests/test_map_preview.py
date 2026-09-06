"""Restart preview privacy, identity, age, and size contracts."""

import asyncio
import math
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from custom_components.dreame_lawn_mower import map_preview as preview_module
from custom_components.dreame_lawn_mower.map_preview import (
    MAX_PREVIEW_AGE_SECONDS,
    MAX_PREVIEW_BYTES,
    RestartMapPreview,
    decode_preview,
    encode_preview,
    preview_scope,
    verified_preview,
)

JPEG = b"\xff\xd8\xfftest-preview"


def test_preview_contains_only_image_identity_and_age():
    scope = preview_scope("mower-one", (1, "dark", 90))
    record = encode_preview(JPEG, scope, now=100)
    assert set(record) == {"scope", "saved_at", "jpeg"}
    assert decode_preview(record, scope, now=101) == (JPEG, 100)
    for device, context in [
        ("mower-two", (1, "dark", 90)),
        ("mower-one", (2, "dark", 90)),
        ("mower-one", (1, "light", 90)),
        ("mower-one", (1, "dark", 0)),
    ]:
        assert decode_preview(record, preview_scope(device, context), now=101) is None


@pytest.mark.parametrize("age", [-1, MAX_PREVIEW_AGE_SECONDS + 1, math.nan, math.inf])
def test_preview_rejects_invalid_age(age):
    record = encode_preview(JPEG, "scope", now=100)
    assert decode_preview(record, "scope", now=100 + age) is None


@pytest.mark.parametrize("record", [None, [], {}, {"scope": "scope"}])
def test_preview_rejects_missing_fields(record):
    assert decode_preview(record, "scope", now=100) is None


def test_preview_rejects_oversize_and_invalid_images():
    assert encode_preview(b"not-jpeg", "scope") is None
    assert encode_preview(JPEG + b"x" * MAX_PREVIEW_BYTES, "scope") is None
    record = encode_preview(JPEG, "scope", now=100)
    for encoded in ["!invalid!", "bm90LWpwZWc=", "A" * (MAX_PREVIEW_BYTES * 2)]:
        assert decode_preview({**record, "jpeg": encoded}, "scope", now=100) is None


def test_restart_storage_loads_once_and_throttles_writes(monkeypatch):
    async def exercise():
        output = BytesIO()
        Image.new("RGB", (120, 80), "green").save(output, format="JPEG")
        image = output.getvalue()
        initial = encode_preview(image, "map-one")
        loads = 0
        saves = []

        async def load():
            nonlocal loads
            loads += 1
            return initial

        async def save(record):
            saves.append(record)

        async def execute(callback, *args):
            return callback(*args)

        monkeypatch.setattr(
            preview_module, "preview_store",
            lambda *_: SimpleNamespace(async_load=load, async_save=save),
        )
        clock = [100.0]
        monkeypatch.setattr(preview_module.time, "monotonic", lambda: clock[0])
        preview = RestartMapPreview(
            SimpleNamespace(async_add_executor_job=execute), "id"
        )
        assert await preview.async_load("unknown-map") is None
        restored = await preview.async_load("map-one")
        assert restored[0] == image
        assert loads == 1
        await preview.async_save(image, "map-one")
        await preview.async_save(image, "map-one")
        assert len(saves) == 1
        clock[0] += 61
        await preview.async_save(image, "map-one")
        assert len(saves) == 2
        await preview.async_save(image, "map-two")
        assert len(saves) == 3
        assert saves[-1]["scope"] == "map-two"

    asyncio.run(exercise())


def test_restart_preview_verifies_jpeg_not_just_header():
    assert verified_preview(encode_preview(JPEG, "scope"), "scope") is None


def test_opt_out_drains_pending_write_before_removal(monkeypatch):
    async def exercise():
        writing = asyncio.Event()
        release = asyncio.Event()
        operations = []

        async def save(_):
            writing.set()
            await release.wait()
            operations.append("save")

        async def remove():
            operations.append("remove")

        monkeypatch.setattr(preview_module, "preview_store", lambda *_: SimpleNamespace(
            async_save=save, async_remove=remove
        ))
        preview = RestartMapPreview(SimpleNamespace(), "entry")
        pending_save = asyncio.create_task(preview.async_save(JPEG, "map"))
        await writing.wait()
        removal = asyncio.create_task(preview.async_remove())
        await asyncio.sleep(0)
        pending_save.cancel()
        release.set()
        await asyncio.gather(pending_save, return_exceptions=True)
        await removal
        await preview.async_save(JPEG, "other-map")
        assert operations == ["save", "remove"]
        assert await preview.async_load("map") is None

    asyncio.run(exercise())
