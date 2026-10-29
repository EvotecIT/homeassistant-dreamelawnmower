"""Task history and photos through Home Assistant's real read-only auth stack."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from homeassistant.components.http.auth import async_sign_path
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dreame_lawn_mower.const import DOMAIN
from custom_components.dreame_lawn_mower.task_history_api import (
    async_setup_task_history_api,
    task_history_api_path,
)
from custom_components.dreame_lawn_mower.task_history_store import TaskHistoryOwner
from tests.test_task_history import detail


async def test_read_only_user_history_and_photo_signatures_are_path_scoped(
    hass,
    hass_client_no_auth,
    hass_read_only_user,
):
    assert await async_setup_component(hass, "http", {"http": {}})
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "lawn_mower",
        DOMAIN,
        "mower_mower",
        config_entry=entry,
    )
    coordinator = SimpleNamespace(
        client=SimpleNamespace(descriptor=SimpleNamespace(unique_id="mower")),
        entry=entry,
    )
    owner = TaskHistoryOwner(hass, coordinator)
    coordinator.task_history = owner
    entry.runtime_data = coordinator
    task = detail()
    owner.async_read = AsyncMock(return_value=task)
    owner.history.photos[task.id] = (object(),)
    photo_bytes = b"\x89PNG\r\n\x1a\nprivate-photo"
    owner.async_photo = AsyncMock(return_value=photo_bytes)
    async_setup_task_history_api(hass)
    http = await hass_client_no_auth()
    path = f"{task_history_api_path(entry.entry_id)}/{task.id}"
    assert (await http.get(path)).status == 401
    owner.async_read.assert_not_called()
    token = await hass.auth.async_create_refresh_token(
        hass_read_only_user,
        client_id="https://example.invalid/",
    )
    signed = async_sign_path(
        hass, path, timedelta(seconds=60), refresh_token_id=token.id
    )
    response = await http.get(signed)
    assert response.status == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    payload = await response.json()
    assert payload["schema_version"] == 1
    assert "ali_dreame" not in str(payload)
    photo_path = payload["photo_paths"][0]
    assert (await http.get(photo_path)).status == 401
    assert (await http.get(photo_path + "?" + signed.split("?", 1)[1])).status == 401
    owner.async_photo.assert_not_called()
    signed_photo = async_sign_path(
        hass,
        photo_path,
        timedelta(seconds=60),
        refresh_token_id=token.id,
    )
    photo = await http.get(signed_photo)
    assert photo.status == 200
    assert photo.headers["Content-Type"] == "image/png"
    assert photo.headers["X-Content-Type-Options"] == "nosniff"
    assert await photo.read() == photo_bytes
    del entry.runtime_data
    assert (await http.get(signed_photo)).status == 404
    owner.async_photo.assert_awaited_once_with(task.id, 0)
    await owner.async_close()
