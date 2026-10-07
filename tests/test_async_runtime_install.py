"""The async installer retains staging and lock ownership through cancellation."""

import asyncio
import hashlib
import threading

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    xp2p_host_runtime,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    xp2p_runtime_async as native,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    xp2p_runtime_bootstrap as bootstrap,
)

from .test_async_cloud_session import server


@pytest.mark.parametrize("mode", ["success", "http_error", "cancel"])
def test_installer_download_and_staging_lifetime(monkeypatch, tmp_path, mode):
    content = b"verified runtime asset"
    digest = hashlib.sha256(content).hexdigest()
    root = tmp_path / "runtime"
    worker_finished = threading.Event()

    def validated(path, _architecture, **kwargs):
        asset = path / "asset"
        if not asset.exists() or asset.read_bytes() != content:
            return None
        return xp2p_host_runtime.DreameLawnMowerXp2pHostAssets(
            worker_path=asset,
            linker_path=asset,
            library_path=asset,
            library_search_paths=(path,),
        )

    monkeypatch.setattr(bootstrap, "_validated_assets", validated)
    monkeypatch.setattr(bootstrap, "_with_startup_probe", lambda assets: assets)

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        requests = []

        async def handler(request):
            requests.append(request.path)
            assert "Authorization" not in request.headers
            assert "Cookie" not in request.headers
            started.set()
            await release.wait()
            return web.Response(
                body=content, status=503 if mode == "http_error" else 200
            )

        async with (
            server(monkeypatch, handler) as url,
            ClientSession(
                headers={"Authorization": "private", "Cookie": "private=1"},
            ) as session,
        ):

            def install(target, architecture, *, http_client, timeout, **kwargs):
                try:
                    data = bootstrap._download_verified(
                        http_client,
                        url,
                        digest,
                        timeout=timeout,
                        label="test asset",
                    )
                    (target / "asset").write_bytes(data)
                finally:
                    worker_finished.set()

            monkeypatch.setattr(bootstrap, "_install_runtime", install)
            task = asyncio.create_task(
                native.async_ensure_xp2p_host_runtime(
                    root,
                    session,
                    machine="aarch64",
                    page_size=4096,
                    timeout=3,
                )
            )
            try:
                await asyncio.wait_for(started.wait(), 2)
                if mode == "cancel":
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await asyncio.wait_for(task, 2)
                elif mode == "http_error":
                    release.set()
                    with pytest.raises(Exception, match="HTTP 503"):
                        await task
                else:
                    release.set()
                    assets = await task
                    assert assets.worker_path.read_bytes() == content
                    cached = await native.async_ensure_xp2p_host_runtime(
                        root,
                        session,
                        machine="aarch64",
                        page_size=4096,
                    )
                    assert cached == assets
                    assert requests == ["/"]
                assert worker_finished.is_set()
                if mode != "success":
                    assert list(root.iterdir()) == []
                assert not session.closed
                assert not session.connector.closed
            finally:
                release.set()

    asyncio.run(scenario())


def test_cancelled_installer_stops_waiting_for_other_install(monkeypatch, tmp_path):
    entered = threading.Event()
    original = bootstrap._check_install_active

    def check(cancelled):
        entered.set()
        original(cancelled)

    monkeypatch.setattr(bootstrap, "_check_install_active", check)

    async def scenario():
        async with ClientSession() as session:
            bootstrap._INSTALL_LOCK.acquire()
            try:
                task = asyncio.create_task(
                    native.async_ensure_xp2p_host_runtime(
                        tmp_path / "runtime",
                        session,
                        machine="aarch64",
                        page_size=4096,
                    )
                )
                assert await asyncio.to_thread(entered.wait, 2)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 2)
                assert not (tmp_path / "runtime").exists()
                assert not session.closed
            finally:
                bootstrap._INSTALL_LOCK.release()

    asyncio.run(scenario())


def test_cancelled_installer_does_not_wait_for_an_occupied_executor(
    monkeypatch, tmp_path
):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import suppress

    async def scenario():
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        occupied, queued = asyncio.Event(), asyncio.Event()
        release, installer_started = threading.Event(), threading.Event()

        def occupy():
            loop.call_soon_threadsafe(occupied.set)
            release.wait(3)

        blocker = loop.run_in_executor(None, occupy)
        await occupied.wait()
        original_submit = loop.run_in_executor

        def submit(executor, function, *args):
            result = original_submit(executor, function, *args)
            queued.set()
            return result

        monkeypatch.setattr(loop, "run_in_executor", submit)

        def install(*args, **kwargs):
            installer_started.set()
            raise AssertionError("A cancelled queued installer must not start")

        monkeypatch.setattr(native, "ensure_xp2p_host_runtime", install)
        async with ClientSession() as session:
            task = asyncio.create_task(
                native.async_ensure_xp2p_host_runtime(tmp_path / "runtime", session)
            )
            try:
                await asyncio.wait_for(queued.wait(), 1)
                task.cancel()
                await asyncio.wait({task}, timeout=0.3)
                assert task.done(), "Cancellation waited for an unrelated worker"
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert not session.closed
            finally:
                release.set()
                await blocker
                with suppress(asyncio.CancelledError, AssertionError):
                    await task
            assert not installer_started.is_set()
            assert not (tmp_path / "runtime").exists()

    asyncio.run(scenario())


def test_async_runtime_preparation_injects_session_and_reuses_assets(
    monkeypatch, tmp_path
):
    from unittest.mock import AsyncMock, Mock

    from custom_components.dreame_lawn_mower import video_camera

    from .test_video_camera import _uninitialized_entity

    async def scenario():
        entity = _uninitialized_entity()
        entity._prepared_runtime = None
        entity._entry.options.clear()
        entity._runtime_prepare_task = None
        session, assets, runtime = object(), object(), object()
        prepare = AsyncMock(return_value=assets)
        monkeypatch.setattr(
            video_camera, "async_ensure_xp2p_host_runtime", prepare
        )
        monkeypatch.setattr(
            video_camera, "async_get_clientsession", lambda hass: session
        )
        monkeypatch.setattr(
            video_camera.video_helpers,
            "managed_runtime_supported",
            lambda: True,
        )
        create = Mock(return_value=runtime)
        entity._create_runtime = create

        async def executor(function, *args):
            return function(*args)

        from types import SimpleNamespace

        entity.hass = SimpleNamespace(
            async_add_executor_job=executor,
            config=SimpleNamespace(path=lambda *parts: str(tmp_path.joinpath(*parts))),
        )
        assert await entity._async_get_runtime() is runtime
        prepare.assert_awaited_once_with(
            tmp_path / ".storage" / "dreame_lawn_mower" / "xp2p-runtime", session
        )
        create.assert_called_once_with(assets)

    asyncio.run(scenario())


def test_native_installer_adapter_preserves_selective_zip_reads(monkeypatch):
    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
        android_build_artifact,
    )

    from .test_android_build_artifact import _build_zip

    content = _build_zip()
    monkeypatch.setattr(native, "LARGE_PAGE_ANDROID_BUILD_ARTIFACT_SIZE", len(content))

    async def scenario():
        async def handler(request):
            start, end = (int(v) for v in request.headers["Range"][6:].split("-"))
            return web.Response(
                status=206,
                body=content[start : end + 1],
                headers={"Content-Range": f"bytes {start}-{end}/{len(content)}"},
            )

        async with server(monkeypatch, handler) as url, ClientSession() as session:
            http = native._RuntimeHttp(session)
            result = await asyncio.to_thread(
                android_build_artifact.read_android_build_zip_entries,
                url,
                ["SYSTEM/lib64/liblog.so"],
                expected_size=len(content),
                http_client=http,
                timeout=3,
            )
            assert result == {"SYSTEM/lib64/liblog.so": b"android-log"}
            assert not http.tasks
            assert not session.closed

    asyncio.run(scenario())


def test_cancelled_managed_preparation_drains_final_construction(monkeypatch, tmp_path):
    from contextlib import suppress
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from custom_components.dreame_lawn_mower import video_camera

    from .test_video_camera import _uninitialized_entity

    async def scenario():
        started, release = threading.Event(), threading.Event()
        entity = _uninitialized_entity()
        entity._entry.options.clear()
        entity._runtime_prepare_task = None
        assets, runtime = object(), object()
        monkeypatch.setattr(
            video_camera,
            "async_ensure_xp2p_host_runtime",
            AsyncMock(return_value=assets),
        )
        monkeypatch.setattr(
            video_camera, "async_get_clientsession", lambda hass: object()
        )
        monkeypatch.setattr(
            video_camera.video_helpers,
            "managed_runtime_supported",
            lambda: True,
        )

        async def executor(function, *args):
            return await asyncio.to_thread(function, *args)

        entity.hass = SimpleNamespace(
            async_add_executor_job=executor,
            config=SimpleNamespace(path=lambda *parts: str(tmp_path.joinpath(*parts))),
        )

        def create(received):
            assert received is assets
            started.set()
            assert release.wait(3)
            entity._prepared_runtime = runtime
            return runtime

        entity._create_runtime = create
        task = asyncio.create_task(entity._async_get_runtime())
        try:
            assert await asyncio.to_thread(started.wait, 2)
            task.cancel()
            await asyncio.sleep(0.02)
            task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done(), (
                "Cancellation returned while construction could publish state"
            )
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert entity._prepared_runtime is runtime
        finally:
            release.set()
            with suppress(asyncio.CancelledError):
                await task

    asyncio.run(scenario())
