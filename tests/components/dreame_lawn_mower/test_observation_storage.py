"""Real HA storage artifacts across new owners, shutdown, and entry removal."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.const import (
    EVENT_HOMEASSISTANT_FINAL_WRITE,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.util.file import AtomicWriter

from custom_components.dreame_lawn_mower import async_remove_entry
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    position_tracking,
    session_timing,
)
from custom_components.dreame_lawn_mower.observation_checkpoint import (
    MAX_CHECKPOINT_BYTES,
    ObservationCheckpoint,
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
    try:
        await first_store.async_close()
        path = tmp_path / ".storage" / first_store._key
        raw = await first_hass.async_add_executor_job(path.read_bytes)
        assert len(raw) <= MAX_CHECKPOINT_BYTES
        envelope = json.loads(raw)
        assert envelope["data"]["timing"]["current"]["seconds"] == 60
        assert envelope["version"] == 1

        await second_store.async_load()
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
