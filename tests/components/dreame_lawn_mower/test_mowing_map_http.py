"""Exercise private map delivery through Home Assistant's real HTTP auth stack."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from homeassistant.components.http.auth import async_sign_path
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dreame_lawn_mower.const import DOMAIN
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import map_projection
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.mowing_map import (
    MowingMapScene,
)
from custom_components.dreame_lawn_mower.mowing_map_api import (
    async_setup_mowing_map_api,
    mowing_map_api_path,
)


async def test_signed_scene_and_background_are_private_and_path_scoped(
    hass,
    hass_client_no_auth,
    hass_read_only_user,
):
    """A signed scene URL cannot authorize its separately signed background."""
    assert await async_setup_component(hass, "http", {"http": {}})
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "camera",
        DOMAIN,
        "mower_map",
        config_entry=entry,
    )
    scene = MowingMapScene(
        "a" * 64,
        0,
        1,
        "Garden",
        b"private-png",
        map_projection.vector_map_projection(0, 0, 1000, 500),
        (0, 0, 1000, 500),
    )
    client = SimpleNamespace(
        descriptor=SimpleNamespace(unique_id="mower"),
        async_get_mowing_map_scene=AsyncMock(return_value=scene),
        mowing_map_runtime_overlay=Mock(return_value={"position": None, "trail": []}),
    )
    hass.data[DOMAIN] = {
        entry.entry_id: SimpleNamespace(
            client=client,
            entry=entry,
            app_maps={
                "map_list_valid": True,
                "current_map_index": 0,
                "maps": [
                    {
                        "idx": 0,
                        "current": True,
                        "created": True,
                        "info": {"hash": "garden-a", "size": 100},
                    }
                ],
            },
        )
    }
    async_setup_mowing_map_api(hass)
    http = await hass_client_no_auth()
    path = mowing_map_api_path(entry.entry_id)
    assert (await http.get(path)).status == 401
    client.async_get_mowing_map_scene.assert_not_called()

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
    assert payload["revision"] == scene.revision
    background_path = payload["background_path"]
    assert (await http.get(background_path)).status == 401
    assert (
        await http.get(background_path + "?" + signed.split("?", 1)[1])
    ).status == 401
    signed_background = async_sign_path(
        hass,
        background_path,
        timedelta(seconds=60),
        refresh_token_id=token.id,
    )
    background = await http.get(signed_background)
    assert background.status == 200
    assert await background.read() == b"private-png"
    assert background.headers["Content-Type"] == "image/png"
    client.async_get_mowing_map_scene.assert_awaited_once()
