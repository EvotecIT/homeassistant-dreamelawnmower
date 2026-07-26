"""Regression checks for private Home Assistant point-cloud delivery."""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from homeassistant.exceptions import Unauthorized

from custom_components.dreame_lawn_mower.const import DOMAIN
from custom_components.dreame_lawn_mower.diagnostic_events import (
    DreameLawnMowerDiagnosticEventStore,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    DreameLawnMowerPointCloudError,
)
from custom_components.dreame_lawn_mower.performance import (
    DreameLawnMowerPerformanceTracker,
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


def _download(
    map_index: int = 0,
    *,
    source: str = "generated",
) -> DreameLawnMowerPointCloudDownload:
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
        source=source,
    )


def test_point_cloud_api_caches_recent_downloads() -> None:
    calls = 0
    options: list[dict[str, Any]] = []

    async def download(**kwargs: Any) -> DreameLawnMowerPointCloudDownload:
        nonlocal calls
        calls += 1
        options.append(kwargs)
        return _download(kwargs["map_index"])

    coordinator = SimpleNamespace(
        app_maps={
            "current_map_index": 0,
            "maps": [{"idx": 0}],
        },
        selected_map_index=0,
        client=SimpleNamespace(
            async_download_app_map_point_cloud=download,
        ),
        diagnostic_events=DreameLawnMowerDiagnosticEventStore(),
    )
    hass = SimpleNamespace(
        data={
            DOMAIN: {
                "entry-1": coordinator
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
    assert options == [{"map_index": 0, "allow_stored": True}]
    event = coordinator.diagnostic_events.as_list()[0]
    assert event["code"] == "point_cloud_completed"
    assert event["severity"] == "info"
    assert event["context"] == {
        "map_index": 0,
        "source": "generated",
        "point_count": 1,
        "total_bytes": 112,
        "data_encoding": "binary",
    }


def test_point_cloud_api_does_not_use_stored_object_for_inactive_map() -> None:
    options: list[dict[str, Any]] = []

    async def download(**kwargs: Any) -> DreameLawnMowerPointCloudDownload:
        options.append(kwargs)
        return _download(kwargs["map_index"])

    hass = SimpleNamespace(
        data={
            DOMAIN: {
                "entry-1": SimpleNamespace(
                    app_maps={
                        "current_map_index": 0,
                        "maps": [{"idx": 0}],
                    },
                    selected_map_index=0,
                    client=SimpleNamespace(
                        async_download_app_map_point_cloud=download,
                    ),
                )
            }
        }
    )
    api = DreameLawnMowerPointCloudAPI(hass)

    asyncio.run(api.async_get("entry-1", 1))

    assert options == [{"map_index": 1, "allow_stored": False}]


def test_point_cloud_api_does_not_use_stored_object_with_multiple_maps() -> None:
    options: list[dict[str, Any]] = []

    async def download(**kwargs: Any) -> DreameLawnMowerPointCloudDownload:
        options.append(kwargs)
        return _download(kwargs["map_index"])

    hass = SimpleNamespace(
        data={
            DOMAIN: {
                "entry-1": SimpleNamespace(
                    app_maps={
                        "current_map_index": 0,
                        "maps": [{"idx": 0}, {"idx": 1}],
                    },
                    selected_map_index=0,
                    client=SimpleNamespace(
                        async_download_app_map_point_cloud=download,
                    ),
                )
            }
        }
    )
    api = DreameLawnMowerPointCloudAPI(hass)

    asyncio.run(api.async_get("entry-1", 0))

    assert options == [{"map_index": 0, "allow_stored": False}]


def test_point_cloud_api_ignores_empty_trailing_map_slots() -> None:
    options: list[dict[str, Any]] = []

    async def download(**kwargs: Any) -> DreameLawnMowerPointCloudDownload:
        options.append(kwargs)
        return _download(kwargs["map_index"])

    hass = SimpleNamespace(
        data={
            DOMAIN: {
                "entry-1": SimpleNamespace(
                    app_maps={
                        "current_map_index": 0,
                        "available_map_count": 1,
                        "created_map_count": 1,
                        "map_count": 2,
                        "maps": [
                            {
                                "idx": 0,
                                "current": True,
                                "created": True,
                                "available": True,
                            },
                            {
                                "idx": 1,
                                "current": False,
                                "created": False,
                            },
                        ],
                    },
                    selected_map_index=0,
                    client=SimpleNamespace(
                        async_download_app_map_point_cloud=download,
                    ),
                )
            }
        }
    )
    api = DreameLawnMowerPointCloudAPI(hass)

    asyncio.run(api.async_get("entry-1", 0))

    assert options == [{"map_index": 0, "allow_stored": True}]


def test_point_cloud_api_refresh_forces_fresh_generation() -> None:
    options: list[dict[str, Any]] = []

    async def download(**kwargs: Any) -> DreameLawnMowerPointCloudDownload:
        options.append(kwargs)
        return _download(kwargs["map_index"])

    hass = SimpleNamespace(
        data={
            DOMAIN: {
                "entry-1": SimpleNamespace(
                    app_maps={
                        "current_map_index": 0,
                        "maps": [
                            {
                                "idx": 0,
                                "created": True,
                                "available": True,
                            }
                        ],
                    },
                    selected_map_index=0,
                    client=SimpleNamespace(
                        async_download_app_map_point_cloud=download,
                    ),
                )
            }
        }
    )
    api = DreameLawnMowerPointCloudAPI(hass)

    asyncio.run(api.async_get("entry-1", 0, refresh=True))

    assert options == [{"map_index": 0, "allow_stored": False}]


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


def test_point_cloud_api_refresh_does_not_join_stored_capable_request() -> None:
    options: list[dict[str, Any]] = []
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def download(**kwargs: Any) -> DreameLawnMowerPointCloudDownload:
        options.append(kwargs)
        if len(options) == 1:
            first_started.set()
            await release_first.wait()
            return _download(0, source="stored")
        return _download(1)

    hass = SimpleNamespace(
        data={
            DOMAIN: {
                "entry-1": SimpleNamespace(
                    app_maps={
                        "current_map_index": 0,
                        "maps": [{"idx": 0, "created": True}],
                    },
                    selected_map_index=0,
                    client=SimpleNamespace(
                        async_download_app_map_point_cloud=download,
                    ),
                )
            }
        }
    )
    api = DreameLawnMowerPointCloudAPI(hass)

    async def run() -> None:
        stored = asyncio.create_task(api.async_get("entry-1", 0))
        await first_started.wait()
        refreshed = asyncio.create_task(
            api.async_get("entry-1", 0, refresh=True)
        )
        await asyncio.sleep(0)
        assert len(options) == 1
        release_first.set()
        stored_result, refresh_result = await asyncio.gather(stored, refreshed)
        assert stored_result.map_index == 0
        assert refresh_result.map_index == 1

    asyncio.run(run())

    assert options == [
        {"map_index": 0, "allow_stored": True},
        {"map_index": 0, "allow_stored": False},
    ]


def test_point_cloud_api_refresh_joins_stored_fallback_generation() -> None:
    options: list[dict[str, Any]] = []
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def download(**kwargs: Any) -> DreameLawnMowerPointCloudDownload:
        options.append(kwargs)
        first_started.set()
        await release_first.wait()
        return _download(0, source="generated")

    hass = SimpleNamespace(
        data={
            DOMAIN: {
                "entry-1": SimpleNamespace(
                    app_maps={
                        "current_map_index": 0,
                        "maps": [{"idx": 0, "created": True}],
                    },
                    selected_map_index=0,
                    client=SimpleNamespace(
                        async_download_app_map_point_cloud=download,
                    ),
                )
            }
        }
    )
    api = DreameLawnMowerPointCloudAPI(hass)

    async def run() -> None:
        first = asyncio.create_task(api.async_get("entry-1", 0))
        await first_started.wait()
        refreshed = asyncio.create_task(
            api.async_get("entry-1", 0, refresh=True)
        )
        await asyncio.sleep(0)
        assert len(options) == 1
        release_first.set()
        first_result, refresh_result = await asyncio.gather(first, refreshed)
        assert refresh_result is first_result
        assert refresh_result.source == "generated"

    asyncio.run(run())

    assert options == [{"map_index": 0, "allow_stored": True}]


def test_point_cloud_api_cancelled_refresh_keeps_stored_work_inflight() -> None:
    calls = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def download(**kwargs: Any) -> DreameLawnMowerPointCloudDownload:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return _download(source="generated")

    hass = SimpleNamespace(
        data={
            DOMAIN: {
                "entry-1": SimpleNamespace(
                    app_maps={
                        "current_map_index": 0,
                        "maps": [{"idx": 0, "created": True}],
                    },
                    selected_map_index=0,
                    client=SimpleNamespace(
                        async_download_app_map_point_cloud=download,
                    ),
                )
            }
        }
    )
    api = DreameLawnMowerPointCloudAPI(hass)

    async def run() -> None:
        first = asyncio.create_task(api.async_get("entry-1", 0))
        await started.wait()
        cancelled_refresh = asyncio.create_task(
            api.async_get("entry-1", 0, refresh=True)
        )
        await asyncio.sleep(0)
        cancelled_refresh.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled_refresh

        replacement = asyncio.create_task(
            api.async_get("entry-1", 0, refresh=True)
        )
        await asyncio.sleep(0)
        assert calls == 1
        release.set()
        first_result, replacement_result = await asyncio.gather(
            first,
            replacement,
        )
        assert replacement_result is first_result

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


def test_point_cloud_api_records_safe_failure_and_timing() -> None:
    private_detail = "https://downloads.example.invalid/map?token=private"

    async def download(**kwargs: Any) -> DreameLawnMowerPointCloudDownload:
        raise DreameLawnMowerPointCloudError(
            private_detail,
            code="point_cloud_not_published",
            stage="generation",
            public_message=(
                "The mower did not publish a fresh 3D map within 45 seconds."
            ),
            timeout_seconds=45,
            retry_after_seconds=10,
        )

    coordinator = SimpleNamespace(
        client=SimpleNamespace(async_download_app_map_point_cloud=download),
        diagnostic_events=DreameLawnMowerDiagnosticEventStore(),
        performance=DreameLawnMowerPerformanceTracker(),
    )
    hass = SimpleNamespace(data={DOMAIN: {"entry-1": coordinator}})
    api = DreameLawnMowerPointCloudAPI(hass)

    async def run() -> None:
        with pytest.raises(DreameLawnMowerPointCloudError):
            await api.async_get("entry-1", 0)

    asyncio.run(run())

    event = coordinator.diagnostic_events.as_list()[0]
    assert event["code"] == "point_cloud_not_published"
    assert event["source"] == "point_cloud_api"
    assert event["context"]["stage"] == "generation"
    assert event["context"]["timeout_seconds"] == 45
    assert event["context"]["map_index"] == 0
    assert event["context"]["allow_stored"] is False
    assert event["context"]["vendor_error_code"] is None
    assert private_detail not in repr(event)
    performance = coordinator.performance.as_dict()
    assert performance["summary"]["point_cloud_generation"]["outcomes"] == {
        "point_cloud_not_published": 1
    }
    assert performance["latest_by_operation"]["point_cloud_generation"][
        "phases_ms"
    ].keys() == {"generate_download_validate"}


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


@pytest.mark.parametrize("map_index", ["invalid", "-1", "256"])
def test_point_cloud_view_returns_structured_invalid_request(
    map_index: str,
) -> None:
    view = DreameLawnMowerPointCloudView(SimpleNamespace())

    response = asyncio.run(
        view.get(_FakeRequest(is_admin=True), "entry-1", map_index)
    )
    payload = json.loads(response.text)

    assert response.status == 400
    assert response.content_type == "application/problem+json"
    assert payload["code"] == "point_cloud_invalid_request"
    assert payload["stage"] == "request"
    assert payload["retryable"] is False
    assert "map index" in payload["detail"]


def test_point_cloud_view_returns_actionable_problem_details() -> None:
    async def get(*args: Any, **kwargs: Any) -> None:
        raise DreameLawnMowerPointCloudError(
            "Internal point-cloud polling detail.",
            code="point_cloud_not_published",
            stage="generation",
            public_message=(
                "The mower did not publish a fresh 3D map within 45 seconds."
            ),
            timeout_seconds=45,
            retry_after_seconds=10,
        )

    view = DreameLawnMowerPointCloudView(SimpleNamespace(async_get=get))
    response = asyncio.run(
        view.get(_FakeRequest(is_admin=True), "entry-1", "0")
    )
    payload = json.loads(response.text)

    assert response.status == 504
    assert response.content_type == "application/problem+json"
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Retry-After"] == "10"
    assert response.headers["X-Dreame-Problem-Code"] == (
        "point_cloud_not_published"
    )
    assert payload == {
        "schema_version": 1,
        "title": "The mower did not publish a fresh 3D map",
        "status": 504,
        "detail": "The mower did not publish a fresh 3D map within 45 seconds.",
        "code": "point_cloud_not_published",
        "stage": "generation",
        "retryable": True,
        "retry_after_seconds": 10,
        "elapsed_ms": pytest.approx(0, abs=10),
        "timeout_seconds": 45,
    }


def test_point_cloud_view_does_not_log_private_exception_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_detail = (
        "https://downloads.example.invalid/private-map.pcd?secret=do-not-log"
    )

    async def get(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError(private_detail)

    view = DreameLawnMowerPointCloudView(SimpleNamespace(async_get=get))

    with caplog.at_level(logging.WARNING):
        response = asyncio.run(
            view.get(_FakeRequest(is_admin=True), "entry-1", "0")
        )

    assert response.status == 502
    assert json.loads(response.text)["code"] == "point_cloud_failed"
    assert "code=point_cloud_failed" in caplog.text
    assert private_detail not in caplog.text
    assert "do-not-log" not in caplog.text
    assert private_detail not in response.text
