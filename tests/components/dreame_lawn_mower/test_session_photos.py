"""Session photo downloads through Home Assistant's registered entity service."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import voluptuous as vol
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import EntityPlatform

from custom_components.dreame_lawn_mower import lawn_mower
from tests.test_session_photos import _client, _cloud


class _PhotoEntity(Entity):
    _attr_should_poll = False
    async_download_session_photos = (
        lawn_mower.DreameLawnMower.async_download_session_photos
    )

    def __init__(self):
        self.coordinator = SimpleNamespace(client=_client(_cloud()))
        self._session_photo_lock = asyncio.Lock()


async def test_registered_photo_service_validates_count_and_returns_saved_media(
    hass,
    tmp_path,
    monkeypatch,
):
    hass.config.media_dirs = {"garden": str(tmp_path)}
    entity = _PhotoEntity()
    platform = EntityPlatform(
        hass=hass,
        logger=logging.getLogger(__name__),
        domain="lawn_mower",
        platform_name="dreame_lawn_mower",
        platform=lawn_mower,
        scan_interval=timedelta(seconds=30),
        entity_namespace=None,
    )
    monkeypatch.setattr(lawn_mower, "async_get_current_platform", lambda: platform)
    monkeypatch.setattr(lawn_mower, "DreameLawnMower", lambda coordinator: entity)
    await lawn_mower.async_setup_entry(
        hass,
        SimpleNamespace(runtime_data=entity.coordinator),
        lambda entities: None,
    )
    entity.hass = hass
    entity.platform = platform
    entity.entity_id = "lawn_mower.photo_contract"
    entity.parallel_updates = platform._get_parallel_updates_semaphore(False)
    platform.domain_platform_entities[entity.entity_id] = entity

    for invalid in (0, 21):
        with pytest.raises(vol.Invalid):
            await hass.services.async_call(
                "dreame_lawn_mower",
                "download_session_photos",
                {"entity_id": entity.entity_id, "session_count": invalid},
                blocking=True,
                return_response=True,
            )
    response = await hass.services.async_call(
        "dreame_lawn_mower",
        "download_session_photos",
        {"entity_id": entity.entity_id},
        blocking=True,
        return_response=True,
    )
    saved = response[entity.entity_id]["photos"]
    assert [item["new"] for item in saved] == [True, True]
    assert Path(saved[0]["file"]).read_bytes() == b"\xff\xd8one"
    assert Path(saved[1]["file"]).read_bytes() == b"\xff\xd8two"
