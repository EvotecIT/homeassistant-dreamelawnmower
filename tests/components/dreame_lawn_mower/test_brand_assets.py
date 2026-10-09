"""Bundled brand images are served by HA's authenticated local branding API."""

from pathlib import Path

import pytest
from homeassistant.loader import async_get_custom_components
from homeassistant.setup import async_setup_component

import custom_components


async def test_brand_assets_are_served_from_the_integration(hass, hass_client):
    """The real loader and HTTP view return bundled PNGs without a placeholder."""
    pytest.importorskip(
        "homeassistant.components.brands", reason="Local branding requires HA 2026.3+"
    )
    integrations = await async_get_custom_components(hass)
    integration = integrations["dreame_lawn_mower"]
    assert integration.has_branding
    brand = Path(integration.file_path) / "brand"
    expected = Path(custom_components.__file__).parent / "dreame_lawn_mower" / "brand"
    assert brand.resolve() == expected.resolve()
    assert await async_setup_component(hass, "brands", {})
    client = await hass_client()
    images = sorted(brand.glob("*.png"))
    assert {"icon.png", "icon@2x.png"} <= {path.name for path in images}
    for path in images:
        response = await client.get(
            f"/api/brands/integration/dreame_lawn_mower/{path.name}?placeholder=no"
        )
        assert response.status == 200
        assert response.content_type == "image/png"
        assert await response.read() == path.read_bytes()
