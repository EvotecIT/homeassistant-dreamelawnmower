"""Real HA storage artifacts across new owners, shutdown, and entry removal."""

import asyncio
import json
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import (
    EVENT_HOMEASSISTANT_FINAL_WRITE,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.util.file import AtomicWriter

from custom_components.dreame_lawn_mower import (
    _async_cleanup_failed_setup,
    async_remove_entry,
    async_unload_entry,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    position_tracking,
    session_timing,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerCameraStreamRuntimeInputs,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.xp2p_config import (
    DreameLawnMowerXp2pDeviceConfig,
)
from custom_components.dreame_lawn_mower.observation_checkpoint import (
    MAX_CHECKPOINT_BYTES,
    ObservationCheckpoint,
)
from custom_components.dreame_lawn_mower.video_lan_cache import (
    DreameLawnMowerVideoLanCache,
)
from custom_components.dreame_lawn_mower.video_provisioning_cache import (
    DreameLawnMowerVideoProvisioningCache,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations():
    """Do not request the shared hass fixture, which mocks all storage IO.

    These tests construct isolated HA instances and invoke the integration's
    storage owner directly; no integration discovery or cloud setup is needed.
    """


def coordinator():
    return SimpleNamespace(
        client=SimpleNamespace(
            descriptor=SimpleNamespace(unique_id="mower-test"),
            _position_tracker=position_tracking.MowerPositionTracker(),
        ),
        observed_mowing_timer=session_timing.ObservedMowingTimer(),
    )


async def test_actual_checkpoint_file_survives_new_ha_owner_and_is_removed(tmp_path):
    """Use actual disk IO, not the normal in-memory hass_storage fixture."""
    first_hass = HomeAssistant(str(tmp_path))
    second_hass = HomeAssistant(str(tmp_path))
    first, second = coordinator(), coordinator()
    now = datetime.now(UTC) - timedelta(minutes=10)
    first.observed_mowing_timer.observe(
        generation=1,
        session_active=True,
        mowing=True,
        identity=77,
        now=now,
        monotonic=0,
    )
    first.observed_mowing_timer.observe(
        generation=1,
        session_active=True,
        mowing=True,
        identity=77,
        now=now + timedelta(seconds=60),
        monotonic=60,
    )
    first_store = ObservationCheckpoint(first_hass, "test-entry", first)
    second_store = ObservationCheckpoint(second_hass, "test-entry", second)
    first.mower_condition_history.observe(
        SimpleNamespace(
            available=True,
            activity="error",
            error_code=23,
            error_display="Emergency stop",
        ),
        observed_at=now,
    )
    first.scheduled_run_history.record(
        "edge",
        "skipped",
        "rain_delay_active",
        map_index=2,
        targets=[[7, 0]],
        observed_at=now,
    )
    try:
        await first_store.async_close()
        path = tmp_path / ".storage" / first_store._key
        raw = await first_hass.async_add_executor_job(path.read_bytes)
        assert len(raw) <= MAX_CHECKPOINT_BYTES
        envelope = json.loads(raw)
        assert envelope["data"]["timing"]["current"]["seconds"] == 60
        assert envelope["version"] == 1

        await second_store.async_load()
        assert second.mower_condition_history.latest()["active"] is None
        assert second.mower_condition_history.latest()["message"] == "Emergency stop"
        assert second.last_scheduled_run["reason"] == "rain_delay_active"
        assert second.last_scheduled_run["map_index"] == 2
        assert second.last_scheduled_run["target_ids"] == [[7, 0]]
        assert second.last_scheduled_run["observed_at"] == now.isoformat()
        second.observed_mowing_timer.observe(
            generation=5,
            session_active=True,
            mowing=True,
            identity=77,
            identity_observed_at=datetime.now(UTC).timestamp(),
            now=datetime.now(UTC),
            monotonic=0,
        )
        assert second.observed_mowing_timer.minutes == 1
        assert second.observed_mowing_timer.partial
        await second_store.async_close()
        files = await first_hass.async_add_executor_job(
            lambda: list(path.parent.iterdir())
        )
        assert [item.name for item in files] == [first_store._key]
        await async_remove_entry(second_hass, SimpleNamespace(entry_id="test-entry"))
        assert not await second_hass.async_add_executor_job(path.exists)
    finally:
        await first_store.async_close()
        await second_store.async_close()
        await first_hass.async_stop()
        await second_hass.async_stop()


async def test_invalid_checkpoint_cannot_prevent_a_new_observation(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    owner = coordinator()
    checkpoint = ObservationCheckpoint(hass, "test-entry", owner)
    try:
        await checkpoint._store.async_save({"unexpected": "old or invalid schema"})
        await checkpoint.async_load()
        owner.observed_mowing_timer.observe(
            generation=1,
            session_active=True,
            mowing=True,
            monotonic=0,
        )
        assert owner.observed_mowing_timer.minutes == 0
        assert owner.observed_mowing_timer.partial
        await checkpoint.async_close()
        record = await checkpoint._store.async_load()
        assert record["timing"]["current"]["seconds"] == 0
    finally:
        await checkpoint.async_close()
        await hass.async_stop()


async def test_orderly_ha_stop_flushes_progress_before_the_periodic_save(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    owner = coordinator()
    checkpoint = ObservationCheckpoint(hass, "test-entry", owner)
    now = datetime.now(UTC) - timedelta(minutes=5)

    def observe(second):
        owner.observed_mowing_timer.observe(
            generation=1,
            session_active=True,
            mowing=True,
            identity=77,
            now=now + timedelta(seconds=second),
            monotonic=second,
        )

    async def stop(_):
        await checkpoint.async_close()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop)
    try:
        await hass.async_start()
        observe(0)
        observe(30)
        await checkpoint.async_flush()
        observe(60)
        checkpoint.async_schedule_save()
        await hass.async_stop()
        path = tmp_path / ".storage" / checkpoint._key
        raw = await hass.async_add_executor_job(path.read_bytes)
        assert json.loads(raw)["data"]["timing"]["current"]["seconds"] == 60
        assert len(raw) <= MAX_CHECKPOINT_BYTES
    finally:
        await checkpoint.async_close()
        await hass.async_stop()


async def test_failed_atomic_replace_preserves_the_previous_checkpoint(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    owner = coordinator()
    checkpoint = ObservationCheckpoint(hass, "test-entry", owner)
    now = datetime.now(UTC) - timedelta(minutes=5)
    try:
        for second in (0, 30):
            owner.observed_mowing_timer.observe(
                generation=1,
                session_active=True,
                mowing=True,
                now=now + timedelta(seconds=second),
                monotonic=second,
            )
        await checkpoint.async_flush()
        path = tmp_path / ".storage" / checkpoint._key
        original = await hass.async_add_executor_job(path.read_bytes)
        owner.observed_mowing_timer.observe(
            generation=1,
            session_active=True,
            mowing=True,
            now=now + timedelta(seconds=60),
            monotonic=60,
        )
        with patch.object(AtomicWriter, "commit", side_effect=OSError("disk full")):
            await checkpoint.async_flush()
        assert await hass.async_add_executor_job(path.read_bytes) == original
        await checkpoint.async_flush()
        recovered = await hass.async_add_executor_job(path.read_bytes)
        assert json.loads(recovered)["data"]["timing"]["current"]["seconds"] == 60
        files = await hass.async_add_executor_job(lambda: list(path.parent.iterdir()))
        assert [item.name for item in files] == [checkpoint._key]
        assert owner.observed_mowing_timer.minutes == 1
    finally:
        await checkpoint.async_close()
        await hass.async_stop()


@pytest.mark.parametrize("retained_owner", [False, True])
@pytest.mark.parametrize("stopping", [False, True])
async def test_entry_removal_deletes_video_files_and_preserves_other_mower(
    tmp_path,
    retained_owner,
    stopping,
):
    """Entry deletion removes private video data without touching another entry."""

    hass = HomeAssistant(str(tmp_path))
    inputs = DreameLawnMowerCameraStreamRuntimeInputs(
        source="cloud",
        did="test-mower",
        product_id="test-product",
        device_name="test-device",
        p2p_info="synthetic-p2p",
        secret_id="synthetic-id",
        secret_key="synthetic-key",
    )
    config = DreameLawnMowerXp2pDeviceConfig()
    owners = {}
    if stopping:
        hass.set_state(CoreState.stopping)
    try:
        for entry_id in ("removed", "retained"):
            lan = DreameLawnMowerVideoLanCache(hass, entry_id=entry_id, did=inputs.did)
            provisioning = DreameLawnMowerVideoProvisioningCache(
                hass,
                entry_id=entry_id,
                did=inputs.did,
            )
            await lan.async_save_identity(inputs)
            await provisioning.async_save(inputs, config)
            owners[entry_id] = SimpleNamespace(
                video_lan_cache=lan,
                video_provisioning_cache=provisioning,
            )
        paths = {
            entry_id: [
                tmp_path / ".storage" / f"dreame_lawn_mower.{kind}.{entry_id}"
                for kind in ("video_lan", "video_provisioning")
            ]
            for entry_id in owners
        }
        before = await hass.async_add_executor_job(
            lambda: {
                path: path.read_bytes() for group in paths.values() for path in group
            }
        )
        entry = SimpleNamespace(entry_id="removed")
        if retained_owner:
            entry.runtime_data = owners["removed"]
        await async_remove_entry(hass, entry)
        if retained_owner:
            await owners["removed"].video_lan_cache.async_save_identity(inputs)
            await owners["removed"].video_provisioning_cache.async_save(inputs, config)
        await async_remove_entry(hass, entry)
        hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
        await hass.async_block_till_done()
        assert await hass.async_add_executor_job(
            lambda: all(not path.exists() for path in paths["removed"])
        )
        assert await hass.async_add_executor_job(
            lambda: all(path.read_bytes() == before[path] for path in paths["retained"])
        )
    finally:
        hass.set_state(CoreState.not_running)
        await hass.async_stop()


@pytest.mark.parametrize("kind", ["video_lan", "video_provisioning"])
@pytest.mark.parametrize("cancel_write", [False, True])
async def test_video_removal_drains_an_inflight_disk_write(
    tmp_path, kind, cancel_write,
):
    """A write already in progress cannot recreate a deleted cache file."""

    hass = HomeAssistant(str(tmp_path))
    cache_type = (
        DreameLawnMowerVideoLanCache
        if kind == "video_lan"
        else DreameLawnMowerVideoProvisioningCache
    )
    cache = cache_type(hass, entry_id="removed", did="test-mower")
    inputs = DreameLawnMowerCameraStreamRuntimeInputs(
        source="cloud",
        did="test-mower",
        product_id="test-product",
        device_name="test-device",
        p2p_info="synthetic-p2p",
        secret_id="synthetic-id",
        secret_key="synthetic-key",
    )
    entered, release = threading.Event(), threading.Event()
    write_method = (
        "_write_prepared_data"
        if getattr(cache._store, "_serialize_in_event_loop", False)
        else "_write_data"
    )
    original_write = getattr(cache._store, write_method)

    def delayed_write(*args):
        entered.set()
        assert release.wait(10), "Timed out waiting to release the disk writer"
        return original_write(*args)

    tasks = []
    try:
        with patch.object(cache._store, write_method, side_effect=delayed_write):
            operation = (
                cache.async_save_identity(inputs)
                if kind == "video_lan"
                else cache.async_save(inputs, DreameLawnMowerXp2pDeviceConfig())
            )
            tasks.append(asyncio.create_task(operation))
            assert await hass.async_add_executor_job(entered.wait, 5)
            if cancel_write:
                tasks[0].cancel()
            entry = SimpleNamespace(entry_id="removed")
            entry.runtime_data = SimpleNamespace(**{f"{kind}_cache": cache})
            tasks.append(asyncio.create_task(async_remove_entry(hass, entry)))
            # Wait until removal reaches this owner's write lock.
            for _ in range(100):
                if cache._removed:
                    break
                await asyncio.sleep(0.01)
            assert cache._removed
            assert not tasks[-1].done()
            if cancel_write:
                tasks[0].cancel()
                await asyncio.sleep(0)
                assert not tasks[0].done()
            release.set()
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), 5,
            )
            if cancel_write:
                assert isinstance(results[0], asyncio.CancelledError)
            else:
                assert results[0] is None
            assert results[1] is None
        path = tmp_path / ".storage" / f"dreame_lawn_mower.{kind}.removed"
        assert not await hass.async_add_executor_job(path.exists)
        assert cache.inputs is None
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await hass.async_stop()


async def test_shutdown_then_fallback_removal_cannot_recreate_checkpoint(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    checkpoint = ObservationCheckpoint(hass, "test-entry", coordinator())
    path = tmp_path / ".storage" / checkpoint._key
    try:
        hass.set_state(CoreState.stopping)
        await checkpoint.async_close()
        # Unload has released the coordinator; removal uses a new Store owner.
        await async_remove_entry(hass, SimpleNamespace(entry_id="test-entry"))
        hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
        await hass.async_block_till_done()
        assert not await hass.async_add_executor_job(path.exists)
        await checkpoint.async_close()
        hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
        await hass.async_block_till_done()
        assert not await hass.async_add_executor_job(path.exists)
    finally:
        hass.set_state(CoreState.not_running)
        await hass.async_stop()


async def test_retained_owner_cannot_recreate_checkpoint_after_entry_removal(tmp_path):
    """A removed entry can retain runtime data when platform unloading failed."""
    hass = HomeAssistant(str(tmp_path))
    owner = coordinator()
    checkpoint = ObservationCheckpoint(hass, "test-entry", owner)
    owner.observation_checkpoint = checkpoint
    entry = SimpleNamespace(entry_id="test-entry", runtime_data=owner)
    path = tmp_path / ".storage" / checkpoint._key
    try:
        await checkpoint.async_flush()
        assert await hass.async_add_executor_job(path.exists)
        checkpoint.async_schedule_save()
        await async_remove_entry(hass, entry)
        checkpoint.async_schedule_save()
        await checkpoint.async_flush()
        await checkpoint.async_close()
        await hass.async_block_till_done()
        assert not await hass.async_add_executor_job(path.exists)
    finally:
        await checkpoint.async_close()
        await hass.async_stop()



@pytest.mark.parametrize("cleanup", ["unload", "failed_setup"])
async def test_released_video_owners_cannot_recreate_removed_files(tmp_path, cleanup):
    """Released owners retain reload data but cannot write after entry removal."""
    hass = HomeAssistant(str(tmp_path))
    entry = SimpleNamespace(entry_id="removed", domain="dreame_lawn_mower")
    lan = DreameLawnMowerVideoLanCache(hass, entry_id=entry.entry_id, did="test")
    provisioning = DreameLawnMowerVideoProvisioningCache(
        hass, entry_id=entry.entry_id, did="test",
    )
    inputs = DreameLawnMowerCameraStreamRuntimeInputs(
        source="cloud", did="test", product_id="product", device_name="device",
        p2p_info="synthetic-p2p", secret_id="synthetic-id", secret_key="synthetic-key",
    )
    config = DreameLawnMowerXp2pDeviceConfig()
    owner = SimpleNamespace(
        video_lan_cache=lan, video_provisioning_cache=provisioning,
        loaded_platforms=(), async_shutdown=AsyncMock(),
    )
    entry.runtime_data = owner
    paths = [
        tmp_path / ".storage" / f"dreame_lawn_mower.{kind}.{entry.entry_id}"
        for kind in ("video_lan", "video_provisioning")
    ]
    try:
        await lan.async_save_identity(inputs)
        await provisioning.async_save(inputs, config)
        if cleanup == "unload":
            with (
                patch.object(hass, "config_entries", SimpleNamespace(
                    async_unload_platforms=AsyncMock(return_value=True),
                    async_entries=lambda domain: [entry],
                    async_get_entry=lambda entry_id: entry,
                )),
            ):
                assert await async_unload_entry(hass, entry)
        else:
            with patch.object(hass, "config_entries", SimpleNamespace(
                async_entries=lambda domain: [entry],
                async_get_entry=lambda entry_id: entry,
            )):
                await _async_cleanup_failed_setup(hass, entry, owner)
        assert not hasattr(entry, "runtime_data")
        assert await hass.async_add_executor_job(lambda: all(p.exists() for p in paths))
        await async_remove_entry(hass, entry)
        await lan.async_save_identity(inputs)
        await provisioning.async_save(inputs, config)
        assert await hass.async_add_executor_job(
            lambda: all(not p.exists() for p in paths)
        )
    finally:
        await hass.async_stop()
