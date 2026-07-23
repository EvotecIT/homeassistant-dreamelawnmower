"""Regression checks for private Home Assistant point-cloud delivery."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from homeassistant.exceptions import Unauthorized

from custom_components.dreame_lawn_mower.const import DOMAIN
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    DreameLawnMowerPointCloudError,
)
from custom_components.dreame_lawn_mower.point_cloud_api import (
    POINT_CLOUD_API_DATA_KEY,
    DreameLawnMowerPointCloudAPI,
    DreameLawnMowerPointCloudView,
    async_setup_point_cloud_api,
    current_point_cloud_api_path,
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


def test_point_cloud_api_evicts_downloads_when_ttl_expires() -> None:
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
    api = DreameLawnMowerPointCloudAPI(hass, cache_ttl=0.01)

    async def run() -> None:
        await api.async_get("entry-1", 0)
        assert api._cache
        await asyncio.sleep(0.03)
        assert not api._cache
        await api.async_get("entry-1", 0)

    asyncio.run(run())

    assert calls == 2


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


def test_point_cloud_api_keeps_generation_alive_after_waiter_cancellation() -> None:
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
        disconnected = asyncio.create_task(
            api.async_get("entry-1", 0, refresh=True)
        )
        await started.wait()
        disconnected.cancel()
        with pytest.raises(asyncio.CancelledError):
            await disconnected

        replacement = asyncio.create_task(
            api.async_get("entry-1", 0, refresh=True)
        )
        await asyncio.sleep(0)
        assert calls == 1
        release.set()
        assert await replacement == _download()

    asyncio.run(run())

    assert calls == 1


def test_point_cloud_api_limits_each_mower_to_one_generation() -> None:
    calls: list[int] = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def download(**kwargs: Any) -> DreameLawnMowerPointCloudDownload:
        calls.append(kwargs["map_index"])
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
        await started.wait()
        with pytest.raises(
            DreameLawnMowerPointCloudError,
            match="already in progress",
        ):
            await api.async_get("entry-1", 1, refresh=True)
        assert calls == [0]
        release.set()
        assert await first == _download(0)

    asyncio.run(run())

    assert calls == [0]


def test_point_cloud_api_discards_inflight_result_after_entry_reload() -> None:
    old_started = asyncio.Event()
    old_release = asyncio.Event()
    old_result = _download()
    new_result = _download()
    old_calls = 0
    new_calls = 0

    async def old_download(**kwargs: Any) -> DreameLawnMowerPointCloudDownload:
        nonlocal old_calls
        old_calls += 1
        old_started.set()
        await old_release.wait()
        return old_result

    async def new_download(**kwargs: Any) -> DreameLawnMowerPointCloudDownload:
        nonlocal new_calls
        new_calls += 1
        return new_result

    hass = SimpleNamespace(
        data={
            DOMAIN: {
                "entry-1": SimpleNamespace(
                    client=SimpleNamespace(
                        async_download_app_map_point_cloud=old_download,
                    )
                )
            }
        }
    )
    api = DreameLawnMowerPointCloudAPI(hass)

    async def run() -> None:
        old_request = asyncio.create_task(
            api.async_get("entry-1", 0, refresh=True)
        )
        await old_started.wait()
        api.purge_entry("entry-1")
        hass.data[DOMAIN]["entry-1"] = SimpleNamespace(
            client=SimpleNamespace(
                async_download_app_map_point_cloud=new_download,
            )
        )

        reloaded_request = asyncio.create_task(
            api.async_get("entry-1", 0, refresh=True)
        )
        await asyncio.sleep(0)
        assert new_calls == 0
        old_release.set()
        with pytest.raises(DreameLawnMowerPointCloudError, match="entry changed"):
            await old_request
        assert await reloaded_request is new_result

    asyncio.run(run())

    assert old_calls == 1
    assert new_calls == 1


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


def test_current_point_cloud_path_follows_selected_map_before_cache_refresh() -> None:
    path = current_point_cloud_api_path(
        "entry-1",
        {
            "current_map_index": 0,
            "maps": [
                {"idx": 0, "current": True},
                {"idx": 1, "current": False},
            ],
        },
        selected_map_index=1,
    )

    assert path == "/api/dreame_lawn_mower/point-cloud/entry-1/1"


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


def test_point_cloud_view_does_not_log_private_exception_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_detail = (
        "https://downloads.example.invalid/private-map.pcd?secret=do-not-log"
    )

    async def get(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError(private_detail)

    view = DreameLawnMowerPointCloudView(SimpleNamespace(async_get=get))

    async def run() -> None:
        with pytest.raises(web.HTTPBadGateway):
            await view.get(_FakeRequest(is_admin=True), "entry-1", "0")

    with caplog.at_level(logging.WARNING):
        asyncio.run(run())

    assert "point-cloud generation failure" in caplog.text
    assert private_detail not in caplog.text
    assert "do-not-log" not in caplog.text
