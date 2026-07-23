"""Regression checks for private Home Assistant point-cloud delivery."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from homeassistant.exceptions import Unauthorized

from custom_components.dreame_lawn_mower.const import DOMAIN
from custom_components.dreame_lawn_mower.point_cloud_api import (
    POINT_CLOUD_API_DATA_KEY,
    DreameLawnMowerPointCloudAPI,
    DreameLawnMowerPointCloudView,
    async_setup_point_cloud_api,
    point_cloud_api_path,
)
from dreame_lawn_mower_client import (
    DreameLawnMowerPointCloudDownload,
    DreameLawnMowerPointCloudMetadata,
)


def _download(map_index: int = 0) -> DreameLawnMowerPointCloudDownload:
    content = b"private-pcd"
    return DreameLawnMowerPointCloudDownload(
        map_index=map_index,
        content=content,
        metadata=DreameLawnMowerPointCloudMetadata(
            version="0.7",
            fields=("x", "y", "z"),
            sizes=(4, 4, 4),
            types=("F", "F", "F"),
            counts=(1, 1, 1),
            width=1,
            height=1,
            points=1,
            data_encoding="binary",
            bytes_per_point=12,
            header_bytes=100,
            payload_bytes=12,
            total_bytes=112,
        ),
    )


def test_point_cloud_api_caches_recent_downloads() -> None:
    calls = 0

    async def download(**kwargs: Any) -> DreameLawnMowerPointCloudDownload:
        nonlocal calls
        calls += 1
        return _download(kwargs["map_index"])

    hass = SimpleNamespace(
        data={
            DOMAIN: {
                "entry-1": SimpleNamespace(
                    client=SimpleNamespace(
                        async_download_app_map_point_cloud=download,
                    )
                )
            }
        }
    )
    api = DreameLawnMowerPointCloudAPI(hass)

    async def run() -> None:
        first = await api.async_get("entry-1", 0)
        second = await api.async_get("entry-1", 0)
        assert first is second

    asyncio.run(run())

    assert calls == 1


def test_point_cloud_api_deduplicates_concurrent_refreshes() -> None:
    calls = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def download(**kwargs: Any) -> DreameLawnMowerPointCloudDownload:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return _download(kwargs["map_index"])

    hass = SimpleNamespace(
        data={
            DOMAIN: {
                "entry-1": SimpleNamespace(
                    client=SimpleNamespace(
                        async_download_app_map_point_cloud=download,
                    )
                )
            }
        }
    )
    api = DreameLawnMowerPointCloudAPI(hass)

    async def run() -> None:
        first = asyncio.create_task(api.async_get("entry-1", 0, refresh=True))
        second = asyncio.create_task(api.async_get("entry-1", 0, refresh=True))
        await started.wait()
        release.set()
        results = await asyncio.gather(first, second)
        assert results[0] is results[1]

    asyncio.run(run())

    assert calls == 1


def test_point_cloud_api_purges_unloaded_entry() -> None:
    calls = 0

    async def download(**kwargs: Any) -> DreameLawnMowerPointCloudDownload:
        nonlocal calls
        calls += 1
        return _download(kwargs["map_index"])

    hass = SimpleNamespace(
        data={
            DOMAIN: {
                "entry-1": SimpleNamespace(
                    client=SimpleNamespace(
                        async_download_app_map_point_cloud=download,
                    )
                )
            }
        }
    )
    api = DreameLawnMowerPointCloudAPI(hass)

    async def run() -> None:
        await api.async_get("entry-1", 0)
        api.purge_entry("entry-1")
        await api.async_get("entry-1", 0)

    asyncio.run(run())

    assert calls == 2


def test_point_cloud_api_registration_is_idempotent() -> None:
    registered: list[object] = []
    hass = SimpleNamespace(
        data={},
        http=SimpleNamespace(register_view=registered.append),
    )

    first = async_setup_point_cloud_api(hass)
    second = async_setup_point_cloud_api(hass)

    assert first is second
    assert hass.data[DOMAIN][POINT_CLOUD_API_DATA_KEY] is first
    assert len(registered) == 1


def test_point_cloud_api_path_is_local_and_contains_no_cloud_details() -> None:
    path = point_cloud_api_path("entry-1", 2)

    assert path == "/api/dreame_lawn_mower/point-cloud/entry-1/2"
    assert path.startswith("/")
    assert "http" not in path


class _FakeRequest(dict[str, Any]):
    def __init__(self, *, is_admin: bool, query: dict[str, str] | None = None) -> None:
        super().__init__(hass_user=SimpleNamespace(is_admin=is_admin))
        self.query = query or {}


def test_point_cloud_view_returns_private_attachment_to_admin() -> None:
    calls: list[tuple[str, int, bool]] = []

    async def get(
        entry_id: str,
        map_index: int,
        *,
        refresh: bool,
    ) -> DreameLawnMowerPointCloudDownload:
        calls.append((entry_id, map_index, refresh))
        return _download(map_index)

    view = DreameLawnMowerPointCloudView(SimpleNamespace(async_get=get))

    response = asyncio.run(
        view.get(
            _FakeRequest(is_admin=True, query={"refresh": "1"}),
            "entry-1",
            "2",
        )
    )

    assert calls == [("entry-1", 2, True)]
    assert response.body == b"private-pcd"
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Content-Disposition"] == (
        'attachment; filename="dreame-map-2.pcd"'
    )
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_point_cloud_view_requires_admin() -> None:
    view = DreameLawnMowerPointCloudView(SimpleNamespace())

    async def run() -> None:
        with pytest.raises(Unauthorized):
            await view.get(_FakeRequest(is_admin=False), "entry-1", "0")

    asyncio.run(run())
