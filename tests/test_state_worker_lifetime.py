"""State workers retain ownership and preserve cancellation over late failures."""

import asyncio
from threading import Event

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    client_refresh,
)


@pytest.mark.parametrize("fails", [False, True])
def test_cancelled_state_worker_drains_before_returning(fails):
    started, release, finished, cancelled = Event(), Event(), Event(), Event()

    def operation():
        started.set()
        try:
            if not release.wait(5):
                raise TimeoutError("Test did not release the worker")
            if fails:
                raise RuntimeError("Late worker failure")
            return 42
        finally:
            finished.set()

    async def run():
        errors = []
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda _loop, context: errors.append(context))
        task = asyncio.create_task(
            client_refresh._run_state_worker(operation, cancelled)
        )
        try:
            async with asyncio.timeout(5):
                while not started.is_set():
                    await asyncio.sleep(0)
                task.cancel()
                while not cancelled.is_set():
                    await asyncio.sleep(0)
                assert not task.done()
                assert not finished.is_set()
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert finished.is_set()
                await asyncio.sleep(0)
                assert errors == []
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
