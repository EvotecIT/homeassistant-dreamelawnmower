from __future__ import annotations

import asyncio

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.lan_video import (
    DreameLawnMowerLanVideoEndpoint,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerCameraStreamRuntimeInputs,
)
from custom_components.dreame_lawn_mower.video_lan_cache import (
    DreameLawnMowerVideoLanCache,
    _decode_cache_payload,
)


def test_lan_cache_restores_only_non_secret_runtime_identity_and_endpoint() -> None:
    inputs, endpoint = _decode_cache_payload(
        {
            "did": "did-1",
            "product_id": "product-1",
            "device_name": "device-1",
            "stream_channel": 0,
            "endpoint": {
                "product_id": "product-1",
                "device_name": "device-1",
                "address": "192.0.2.25",
                "port": 9000,
                "response_version": "2.4",
                "source": "proven_session",
            },
        },
        expected_did="did-1",
    )

    assert inputs is not None
    assert inputs.lan_identity_ready is True
    assert inputs.ready is False
    assert inputs.p2p_info is None
    assert inputs.secret_id is None
    assert inputs.secret_key is None
    assert inputs.app_id is None
    assert inputs.app_secret is None
    assert inputs.lan_client_token is None
    assert endpoint is not None
    assert endpoint.address == "192.0.2.25"
    assert endpoint.port == 9000


def test_lan_cache_rejects_identity_from_another_mower() -> None:
    assert _decode_cache_payload(
        {
            "did": "other-did",
            "product_id": "product-1",
            "device_name": "device-1",
        },
        expected_did="did-1",
    ) == (None, None)


def test_lan_cache_drops_endpoint_that_does_not_match_cached_identity() -> None:
    inputs, endpoint = _decode_cache_payload(
        {
            "did": "did-1",
            "product_id": "product-1",
            "device_name": "device-1",
            "endpoint": {
                "product_id": "other-product",
                "device_name": "device-1",
                "address": "192.0.2.25",
                "port": 9000,
            },
        },
        expected_did="did-1",
    )

    assert inputs is not None
    assert endpoint is None


@pytest.mark.parametrize("address", ["not-an-ip", "127.0.0.1", "0.0.0.0", "224.0.0.1"])
def test_lan_cache_rejects_unsafe_endpoint_address(address: str) -> None:
    inputs, endpoint = _decode_cache_payload(
        {
            "did": "did-1",
            "product_id": "product-1",
            "device_name": "device-1",
            "endpoint": {
                "product_id": "product-1",
                "device_name": "device-1",
                "address": address,
                "port": 9000,
            },
        },
        expected_did="did-1",
    )

    assert inputs is not None
    assert endpoint is None


def test_lan_cache_clear_endpoint_persists_identity_without_endpoint() -> None:
    async def _run() -> tuple[object | None, dict[str, object]]:
        saved: dict[str, object] = {}

        class _Store:
            async def async_save(self, payload: dict[str, object]) -> None:
                saved.update(payload)

        cache = object.__new__(DreameLawnMowerVideoLanCache)
        cache._store = _Store()
        cache._did = "did-1"
        cache.inputs = DreameLawnMowerCameraStreamRuntimeInputs(
            source="lan_video_cache",
            did="did-1",
            product_id="product-1",
            device_name="device-1",
        )
        cache.endpoint = DreameLawnMowerLanVideoEndpoint(
            product_id="product-1",
            device_name="device-1",
            address="192.0.2.25",
            port=9000,
            response_version="cached",
        )

        await cache.async_clear_endpoint()
        return cache.endpoint, saved

    endpoint, saved = asyncio.run(_run())

    assert endpoint is None
    assert saved["product_id"] == "product-1"
    assert "endpoint" not in saved
