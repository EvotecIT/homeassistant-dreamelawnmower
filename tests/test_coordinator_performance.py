"""Coordinator startup and performance telemetry contracts."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

import custom_components.dreame_lawn_mower as integration_module
import custom_components.dreame_lawn_mower.coordinator as coordinator_module
from custom_components.dreame_lawn_mower import async_setup_entry
from custom_components.dreame_lawn_mower.const import (
    CONF_ACCOUNT_TYPE,
    CONF_COUNTRY,
    CONF_DID,
    CONF_MODEL,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
)
from custom_components.dreame_lawn_mower.coordinator import (
    DEVICE_SNAPSHOT_GENERATION_HISTORY,
    METADATA_REFRESH_CONCURRENCY,
    DreameLawnMowerCoordinator,
)
from custom_components.dreame_lawn_mower.performance import (
    DreameLawnMowerPerformanceTracker,
)


def test_coordinator_registers_its_config_entry_with_home_assistant() -> None:
    entry = SimpleNamespace(
        data={
            CONF_DID: "device-id",
            CONF_NAME: "Test mower",
            CONF_MODEL: "dreame.mower.test",
            CONF_ACCOUNT_TYPE: "dreame",
            CONF_COUNTRY: "eu",
            CONF_USERNAME: "user@example.invalid",
            CONF_PASSWORD: "secret",
        },
        options={},
    )
    client = Mock()

    with (
        patch.object(
            coordinator_module,
            "DreameLawnMowerClient",
            return_value=client,
        ),
        patch.object(
            DataUpdateCoordinator,
            "__init__",
            return_value=None,
        ) as coordinator_init,
    ):
        coordinator = DreameLawnMowerCoordinator(Mock(), entry)

    assert coordinator.entry is entry
    assert coordinator_init.call_args.kwargs["config_entry"] is entry
    client.set_update_callback.assert_called_once_with(
        coordinator._handle_client_update
    )


def test_newer_video_safety_snapshot_wins_over_slow_foreground_publication() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._device_snapshot_generation = 0
    coordinator._published_device_snapshot_generation = 0
    coordinator._device_snapshot_generations = {}
    foreground_snapshot = SimpleNamespace(state="docked")
    video_snapshot = SimpleNamespace(state="idle")

    coordinator._record_device_snapshot(foreground_snapshot)
    coordinator._record_device_snapshot(video_snapshot)

    with patch.object(
        DataUpdateCoordinator,
        "async_set_updated_data",
    ) as publish:
        coordinator.async_set_updated_data(foreground_snapshot)
        coordinator.async_set_updated_data(video_snapshot)

    publish.assert_called_once_with(video_snapshot)
    assert coordinator._published_device_snapshot_generation == 2


def test_video_safety_refresh_retries_snapshot_that_is_already_stale() -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        stale_snapshot = SimpleNamespace(state="docked")
        current_snapshot = SimpleNamespace(state="idle")
        coordinator._device_refresh_lock = asyncio.Lock()
        coordinator._device_snapshot_generation = 0
        coordinator._published_device_snapshot_generation = 0
        coordinator._device_snapshot_generations = {}
        coordinator.client = SimpleNamespace(
            async_refresh=AsyncMock(
                side_effect=AssertionError(
                    "Video safety must not use the ordinary cached refresh."
                )
            ),
            async_refresh_authoritative_snapshot=AsyncMock(
                side_effect=(stale_snapshot, current_snapshot)
            ),
        )
        coordinator._device_snapshot_is_stale = Mock(side_effect=(True, False))
        coordinator.async_set_updated_data = Mock()

        result = await coordinator.async_refresh_video_safety_state()

        assert result is current_snapshot
        assert (
            coordinator.client.async_refresh_authoritative_snapshot.await_count
            == 2
        )
        coordinator.client.async_refresh.assert_not_awaited()
        coordinator.async_set_updated_data.assert_called_once_with(current_snapshot)

    asyncio.run(scenario())


def test_newer_video_safety_snapshot_blocks_foreground_runtime_side_effects() -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        foreground_snapshot = SimpleNamespace(
            available=True,
            mowing_session_active=True,
            activity="mowing",
        )
        video_snapshots = [
            SimpleNamespace(
                available=True,
                mowing_session_active=False,
                activity="idle",
            )
            for _ in range(DEVICE_SNAPSHOT_GENERATION_HISTORY + 2)
        ]
        snapshots = iter((foreground_snapshot, *video_snapshots))
        runtime_started = asyncio.Event()
        release_runtime = asyncio.Event()
        retained_runtime = SimpleNamespace(source="newer-state")

        async def refresh_snapshot() -> object:
            return next(snapshots)

        async def refresh_app_maps(*, force: bool) -> dict[str, object]:
            assert force is True
            coordinator.app_maps_refreshed_at = object()
            coordinator.app_maps_refresh_succeeded = True
            return coordinator.app_maps

        async def refresh_runtime(*, refresh: bool, include_cloud: bool) -> object:
            assert refresh is False
            assert include_cloud is True
            runtime_started.set()
            await release_runtime.wait()
            return SimpleNamespace(source="stale-foreground")

        coordinator.performance = DreameLawnMowerPerformanceTracker()
        coordinator._foreground_refresh_count = 0
        coordinator._device_refresh_lock = asyncio.Lock()
        coordinator._device_snapshot_generation = 0
        coordinator._published_device_snapshot_generation = 0
        coordinator._device_snapshot_generations = {}
        coordinator._retained_device_snapshot_ids = set()
        coordinator._runtime_map_identity_verified = False
        coordinator.app_maps = {"current_map_index": 2}
        coordinator.app_maps_refreshed_at = object()
        coordinator.app_maps_refresh_succeeded = False
        coordinator.selected_map_index = 2
        coordinator.runtime_status_blob = retained_runtime
        coordinator.runtime_telemetry_cache = SimpleNamespace(update=Mock())
        coordinator.client = SimpleNamespace(
            async_refresh=refresh_snapshot,
            async_refresh_authoritative_snapshot=refresh_snapshot,
            async_get_runtime_status_blob=refresh_runtime,
            update_runtime_live_tracking=Mock(),
        )
        coordinator.async_refresh_app_maps = refresh_app_maps
        coordinator._schedule_metadata_refresh = Mock()
        coordinator._log_performance_sample = Mock()

        with patch.object(
            DataUpdateCoordinator,
            "async_set_updated_data",
            side_effect=lambda data: setattr(coordinator, "data", data),
        ) as publish:
            foreground_task = asyncio.create_task(coordinator._async_update_data())
            await asyncio.wait_for(runtime_started.wait(), timeout=1)
            current = None
            for video_snapshot in video_snapshots:
                current = await coordinator.async_refresh_video_safety_state()
                assert current is video_snapshot
            release_runtime.set()
            foreground_result = await foreground_task

        assert current is video_snapshots[-1]
        assert foreground_result is video_snapshots[-1]
        assert publish.call_count == len(video_snapshots)
        publish.assert_called_with(video_snapshots[-1])
        assert coordinator.runtime_status_blob is retained_runtime
        assert coordinator._runtime_map_identity_verified is False
        coordinator.runtime_telemetry_cache.update.assert_not_called()
        coordinator.client.update_runtime_live_tracking.assert_not_called()
        coordinator._schedule_metadata_refresh.assert_not_called()

    asyncio.run(scenario())


def test_first_refresh_does_not_wait_for_optional_metadata() -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        snapshot = SimpleNamespace(
            available=True,
            mowing_session_active=False,
            activity="idle",
        )
        metadata_started = asyncio.Event()
        release_metadata = asyncio.Event()

        async def metadata_refresh(*_args, **_kwargs) -> None:
            metadata_started.set()
            await release_metadata.wait()

        coordinator.performance = DreameLawnMowerPerformanceTracker()
        coordinator._foreground_refresh_count = 0
        coordinator._metadata_refresh_task = None
        coordinator._runtime_map_identity_verified = False
        coordinator._shutting_down = False
        coordinator.hass = SimpleNamespace(
            async_create_task=lambda coroutine, _name: asyncio.create_task(coroutine)
        )
        coordinator.client = SimpleNamespace(
            async_refresh=AsyncMock(return_value=snapshot),
            update_runtime_live_tracking=Mock(),
        )
        coordinator._async_refresh_metadata = metadata_refresh

        result = await coordinator._async_update_data()

        assert result is snapshot
        await asyncio.wait_for(metadata_started.wait(), timeout=1)
        assert coordinator._metadata_refresh_task is not None
        assert not coordinator._metadata_refresh_task.done()
        sample = coordinator.performance.as_dict()["samples"][-1]
        assert sample["operation"] == "foreground_refresh"
        assert set(sample["phases_ms"]) == {"snapshot"}

        release_metadata.set()
        await coordinator._metadata_refresh_task

    asyncio.run(scenario())


def test_metadata_hydration_serializes_shared_vendor_protocol_calls() -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        app_maps_started = asyncio.Event()
        schedules_started = asyncio.Event()
        batch_started = asyncio.Event()
        release_app_maps = asyncio.Event()
        release_schedules = asyncio.Event()

        async def refresh_app_maps(*, force: bool) -> dict[str, object]:
            assert force is False
            app_maps_started.set()
            await release_app_maps.wait()
            return {"current_map_index": 0}

        async def refresh_schedules(*, force: bool) -> dict[str, object]:
            assert force is False
            schedules_started.set()
            await release_schedules.wait()
            return {"schedules": []}

        async def refresh_batch(*, force: bool) -> dict[str, object]:
            assert force is False
            batch_started.set()
            return {}

        async def complete(*_args, **_kwargs) -> None:
            return None

        coordinator.performance = DreameLawnMowerPerformanceTracker()
        coordinator._metadata_refresh_count = 0
        coordinator._metadata_refresh_task = None
        coordinator._metadata_refresh_semaphore = asyncio.Semaphore(
            METADATA_REFRESH_CONCURRENCY
        )
        coordinator._shutting_down = False
        coordinator.async_update_listeners = Mock()
        coordinator.async_refresh_app_maps = refresh_app_maps
        coordinator.async_refresh_schedules = refresh_schedules
        coordinator.async_refresh_batch_device_data = refresh_batch
        coordinator._async_refresh_runtime_status = complete
        coordinator._async_refresh_bluetooth_state = complete
        coordinator.async_refresh_firmware_update_support = complete
        coordinator.async_refresh_app_map_objects = complete
        coordinator.async_refresh_vector_map_details = complete
        coordinator.async_refresh_weather_protection = complete
        coordinator.async_refresh_maintenance_status = complete
        coordinator.async_refresh_voice_settings = complete
        task = asyncio.create_task(
            coordinator._async_refresh_metadata(
                refresh_map_and_runtime=True,
            )
        )
        coordinator._metadata_refresh_task = task
        await asyncio.wait_for(app_maps_started.wait(), timeout=1)
        assert not schedules_started.is_set()
        assert not batch_started.is_set()

        release_app_maps.set()
        await asyncio.wait_for(schedules_started.wait(), timeout=1)
        assert not batch_started.is_set()

        release_schedules.set()
        await asyncio.wait_for(batch_started.wait(), timeout=1)
        await task

        coordinator.async_update_listeners.assert_called_once_with()
        sample = coordinator.performance.as_dict()["samples"][-1]
        assert sample["operation"] == "metadata_refresh"
        assert sample["outcome"] == "completed"
        assert "app_maps" in sample["phases_ms"]
        assert "schedules" in sample["phases_ms"]
        assert "batch_device_data" in sample["phases_ms"]

    asyncio.run(scenario())


def test_active_session_reuses_verified_map_identity_between_polls() -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        coordinator.performance = DreameLawnMowerPerformanceTracker()
        coordinator._runtime_map_identity_verified = True
        coordinator.app_maps = {"current_map_index": 2}
        coordinator.app_maps_refreshed_at = object()
        coordinator.app_maps_refresh_succeeded = True
        coordinator.selected_map_index = 2
        coordinator.runtime_status_blob = None
        coordinator.runtime_telemetry_cache = SimpleNamespace(update=Mock())
        status_blob = SimpleNamespace()
        snapshot = SimpleNamespace(
            available=True,
            mowing_session_active=True,
            activity="mowing",
        )
        coordinator.client = SimpleNamespace(
            async_get_runtime_status_blob=AsyncMock(return_value=status_blob),
            update_runtime_live_tracking=Mock(),
        )
        coordinator.async_refresh_app_maps = AsyncMock(
            return_value=coordinator.app_maps
        )
        cycle = coordinator.performance.start("test_active_runtime")

        await coordinator._async_refresh_active_runtime(cycle, snapshot)
        cycle.finish()

        coordinator.async_refresh_app_maps.assert_awaited_once_with(force=False)
        coordinator.client.update_runtime_live_tracking.assert_called_once_with(
            status_blob,
            active=True,
            map_index=2,
        )

    asyncio.run(scenario())


def test_failed_due_map_refresh_does_not_stamp_runtime_with_stale_identity() -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        coordinator.performance = DreameLawnMowerPerformanceTracker()
        coordinator._runtime_map_identity_verified = True
        coordinator.app_maps = {"current_map_index": 2}
        coordinator.app_maps_refreshed_at = object()
        coordinator.app_maps_refresh_succeeded = False
        coordinator.selected_map_index = 2
        coordinator.runtime_status_blob = None
        coordinator.runtime_telemetry_cache = SimpleNamespace(update=Mock())
        status_blob = SimpleNamespace()
        snapshot = SimpleNamespace(
            available=True,
            mowing_session_active=True,
            activity="mowing",
        )
        coordinator.client = SimpleNamespace(
            async_get_runtime_status_blob=AsyncMock(return_value=status_blob),
            update_runtime_live_tracking=Mock(),
        )
        coordinator.async_refresh_app_maps = AsyncMock(
            return_value=coordinator.app_maps
        )
        cycle = coordinator.performance.start("test_active_runtime")

        await coordinator._async_refresh_active_runtime(cycle, snapshot)
        cycle.finish()

        coordinator.client.update_runtime_live_tracking.assert_called_once_with(
            status_blob,
            active=True,
            map_index=None,
        )
        assert coordinator._runtime_map_identity_verified is False

    asyncio.run(scenario())


def test_schedule_refresh_prefers_fast_batch_payload() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    default_schedule = {"idx": -1, "available": True, "plans": []}
    coordinator.schedules = {
        "source": "app_action_schedule",
        "schedules": [
            default_schedule,
            {"idx": 1, "available": True, "version": 4, "plans": []},
            {"idx": 2, "available": True, "version": 6, "plans": []},
        ],
    }
    coordinator.schedules_refreshed_at = None
    coordinator.app_maps = {
        "current_map_index": 2,
        "maps": [
            {"idx": 1, "created": True},
            {"idx": 2, "created": True},
        ],
    }
    coordinator.selected_map_index = 2
    batch_payload = {
        "source": "batch_device_data_schedule",
        "available": True,
        "current_task": None,
        "schedules": [
            {
                "idx": 2,
                "available": True,
                "version": 6,
                "plans": [],
            }
        ],
        "errors": [],
    }
    coordinator.client = SimpleNamespace(
        async_get_batch_schedules=AsyncMock(return_value=batch_payload),
        async_get_app_schedules=AsyncMock(),
    )

    result = asyncio.run(coordinator.async_refresh_schedules())

    assert result is coordinator.schedules
    assert result["source"] == "app_action_schedule_with_batch_refresh"
    assert result["schedules"][0] is default_schedule
    assert result["schedules"][1]["version"] == 4
    assert result["schedules"][2]["version"] == 6
    assert result["active_schedule_version"] == 6
    coordinator.client.async_get_batch_schedules.assert_awaited_once_with(
        include_raw=False,
        map_index_hint=2,
    )
    coordinator.client.async_get_app_schedules.assert_not_awaited()


def test_initial_single_map_schedule_refresh_keeps_default_app_schedule() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator.schedules = None
    coordinator.schedules_refreshed_at = None
    coordinator.app_maps = {
        "current_map_index": 2,
        "maps": [{"idx": 2, "created": True}],
    }
    app_payload = {
        "source": "app_action_schedule",
        "schedules": [
            {"idx": -1, "version": 10, "plans": []},
            {"idx": 2, "version": 11, "plans": []},
        ],
    }
    batch_payload = {
        "source": "batch_device_data_schedule",
        "available": False,
        "active_schedule_version": 10,
        "current_task": None,
        "schedules": [
            {
                "idx": 2,
                "available": False,
                "version": 10,
                "plans": [],
            }
        ],
        "errors": [],
    }
    coordinator.client = SimpleNamespace(
        async_get_batch_schedules=AsyncMock(return_value=batch_payload),
        async_get_app_schedules=AsyncMock(return_value=app_payload),
    )

    result = asyncio.run(coordinator.async_refresh_schedules())

    assert result["active_schedule_version"] == 10
    assert [schedule["idx"] for schedule in result["schedules"]] == [-1, 2]
    coordinator.client.async_get_batch_schedules.assert_awaited_once_with(
        include_raw=False,
        map_index_hint=2,
    )
    coordinator.client.async_get_app_schedules.assert_awaited_once_with(
        include_current_task=False,
        map_indices=[-1, 2],
    )


def test_schedule_refresh_recovers_active_map_from_batch_after_action_failure() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator.schedules = None
    coordinator.schedules_refreshed_at = None
    coordinator.selected_map_index = 1
    coordinator.app_maps = {
        "current_map_index": 1,
        "maps": [
            {"idx": 0, "created": True},
            {"idx": 1, "created": True},
        ],
    }
    failed_app_payload = {
        "source": "app_action_schedule",
        "available": False,
        "current_task": None,
        "schedules": [
            {"idx": -1, "available": False, "error": "timed out"},
            {"idx": 0, "available": False, "error": "timed out"},
            {"idx": 1, "available": False, "error": "timed out"},
        ],
        "errors": [
            {"idx": -1, "stage": "schedule", "error": "timed out"},
            {"idx": 0, "stage": "schedule", "error": "timed out"},
            {"idx": 1, "stage": "schedule", "error": "timed out"},
        ],
    }
    active_plan = {"plan_id": 7, "enabled": True, "weeks": []}
    batch_payload = {
        "source": "batch_device_data_schedule",
        "available": True,
        "current_task": None,
        "schedules": [
            {
                "idx": 1,
                "available": True,
                "version": 12,
                "plan_count": 1,
                "enabled_plan_count": 1,
                "plans": [active_plan],
            }
        ],
        "errors": [],
    }
    coordinator.client = SimpleNamespace(
        async_get_batch_schedules=AsyncMock(return_value=batch_payload),
        async_get_app_schedules=AsyncMock(return_value=failed_app_payload),
    )

    result = asyncio.run(coordinator.async_refresh_schedules())

    assert result is coordinator.schedules
    assert result["source"] == "app_action_schedule_with_batch_refresh"
    assert result["available"] is True
    assert result["active_schedule_version"] == 12
    assert result["schedules"][3]["idx"] is None
    assert result["schedules"][3]["writable"] is False
    assert result["schedules"][3]["plans"] == [active_plan]
    assert coordinator.schedules_refreshed_at is not None
    coordinator.client.async_get_app_schedules.assert_awaited_once_with(
        include_current_task=False,
        map_indices=[-1, 0, 1],
    )
    coordinator.client.async_get_batch_schedules.assert_awaited_once_with(
        include_raw=False,
        map_index_hint=1,
    )


def test_schedule_refresh_keeps_complete_app_payload_for_multiple_maps() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator.schedules = None
    coordinator.schedules_refreshed_at = None
    coordinator.app_maps = {
        "current_map_index": 1,
        "maps": [
            {"idx": 1, "created": True},
            {"idx": 2, "created": True},
        ],
    }
    app_payload = {
        "source": "app_action_schedule",
        "schedules": [
            {"idx": -1, "version": 10, "plans": []},
            {"idx": 1, "version": 11, "plans": []},
            {"idx": 2, "version": 12, "plans": []},
        ],
    }
    batch_payload = {
        "source": "batch_device_data_schedule",
        "available": False,
        "active_schedule_version": 11,
        "current_task": None,
        "schedules": [
            {
                "idx": 1,
                "available": False,
                "version": 11,
                "plans": [],
            }
        ],
        "errors": [],
    }
    coordinator.client = SimpleNamespace(
        async_get_batch_schedules=AsyncMock(return_value=batch_payload),
        async_get_app_schedules=AsyncMock(return_value=app_payload),
    )

    result = asyncio.run(coordinator.async_refresh_schedules())

    assert [schedule["idx"] for schedule in result["schedules"]] == [-1, 1, 2]
    assert result["active_schedule_version"] == 11
    coordinator.client.async_get_batch_schedules.assert_awaited_once_with(
        include_raw=False,
        map_index_hint=1,
    )
    coordinator.client.async_get_app_schedules.assert_awaited_once_with(
        include_current_task=False,
        map_indices=[-1, 1, 2],
    )


def test_schedule_refresh_merges_successful_slots_when_another_slot_fails() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator.schedules = {
        "source": "app_action_schedule",
        "schedules": [
            {"idx": -1, "version": 1, "plans": [{"plan_id": 1}]},
            {"idx": 0, "version": 2, "plans": [{"plan_id": 2}]},
            {"idx": 1, "version": 3, "plans": [{"plan_id": 3}]},
        ],
        "errors": [],
    }
    coordinator.schedules_refreshed_at = None
    coordinator.selected_map_index = 0
    coordinator.app_maps = {
        "current_map_index": 0,
        "maps": [
            {"idx": 0, "created": True},
            {"idx": 1, "created": True},
        ],
    }
    incoming = {
        "source": "app_action_schedule",
        "schedules": [
            {"idx": -1, "version": 4, "plans": [{"plan_id": 4}]},
            {"idx": 0, "available": False, "error": "timed out"},
            {"idx": 1, "version": 5, "plans": [{"plan_id": 5}]},
        ],
        "errors": [{"idx": 0, "stage": "schedule", "error": "timed out"}],
    }
    batch_payload = {
        "source": "batch_device_data_schedule",
        "available": True,
        "active_schedule_version": 5,
        "current_task": None,
        "schedules": [
            {
                "idx": 0,
                "available": True,
                "version": 5,
                "plans": [{"plan_id": 5}],
            }
        ],
        "errors": [],
    }
    coordinator.client = SimpleNamespace(
        async_get_app_schedules=AsyncMock(return_value=incoming),
        async_get_batch_schedules=AsyncMock(return_value=batch_payload),
    )

    result = asyncio.run(coordinator.async_refresh_schedules(force=True))

    assert result["schedules"][0]["version"] == 4
    assert result["schedules"][1]["version"] == 2
    assert result["schedules"][2]["version"] == 5
    assert result["active_schedule_version"] == 5
    assert coordinator.schedules_refreshed_at is not None


def test_schedule_refresh_promotes_batch_fallback_when_slot_becomes_known() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator.schedules = {
        "source": "app_action_schedule_with_batch_refresh",
        "schedules": [
            {"idx": -1, "available": False, "error": "timed out"},
            {
                "idx": None,
                "label": "active_schedule",
                "writable": False,
                "available": True,
                "version": 12,
                "plans": [{"plan_id": 7}],
            },
        ],
        "active_schedule_version": 12,
        "errors": [],
    }
    coordinator.schedules_refreshed_at = None
    coordinator.selected_map_index = 0
    coordinator.app_maps = {
        "current_map_index": 0,
        "maps": [{"idx": 0, "created": True}],
    }
    incoming = {
        "source": "app_action_schedule",
        "schedules": [
            {"idx": -1, "available": False, "error": "timed out"},
            {
                "idx": 0,
                "available": True,
                "version": 12,
                "plans": [{"plan_id": 7}],
            },
        ],
        "errors": [{"idx": -1, "stage": "schedule", "error": "timed out"}],
    }
    coordinator.client = SimpleNamespace(
        async_get_app_schedules=AsyncMock(return_value=incoming),
        async_get_batch_schedules=AsyncMock(side_effect=TimeoutError),
    )

    result = asyncio.run(coordinator.async_refresh_schedules(force=True))

    matching = [
        schedule
        for schedule in result["schedules"]
        if schedule.get("version") == 12
    ]
    assert len(matching) == 1
    assert matching[0]["idx"] == 0
    assert "writable" not in matching[0]


def test_schedule_refresh_does_not_mark_all_failed_reads_fresh() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator.schedules = None
    coordinator.schedules_refreshed_at = None
    coordinator.selected_map_index = 0
    coordinator.app_maps = {
        "current_map_index": 0,
        "maps": [{"idx": 0, "created": True}],
    }
    failed_app_payload = {
        "source": "app_action_schedule",
        "available": False,
        "schedules": [
            {"idx": -1, "available": False, "error": "timed out"},
            {"idx": 0, "available": False, "error": "timed out"},
        ],
        "errors": [
            {"idx": -1, "stage": "schedule", "error": "timed out"},
            {"idx": 0, "stage": "schedule", "error": "timed out"},
        ],
    }
    failed_batch_payload = {
        "source": "batch_device_data_schedule",
        "available": False,
        "schedules": [],
        "errors": [{"stage": "schedule", "error": "missing schedule"}],
    }
    coordinator.client = SimpleNamespace(
        async_get_app_schedules=AsyncMock(return_value=failed_app_payload),
        async_get_batch_schedules=AsyncMock(return_value=failed_batch_payload),
    )

    result = asyncio.run(coordinator.async_refresh_schedules())

    assert result is not None
    assert coordinator.schedules_refreshed_at is None


def test_schedule_refresh_does_not_mark_retained_cache_fresh_after_failures() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator.schedules = {
        "source": "app_action_schedule",
        "available": True,
        "schedules": [
            {
                "idx": -1,
                "available": True,
                "version": 1,
                "plans": [{"plan_id": 1}],
            }
        ],
        "errors": [],
    }
    coordinator.schedules_refreshed_at = None
    coordinator.selected_map_index = None
    coordinator.app_maps = {"maps": []}
    failed_app_payload = {
        "source": "app_action_schedule",
        "available": False,
        "schedules": [
            {"idx": -1, "available": False, "error": "timed out"},
        ],
        "errors": [
            {"idx": -1, "stage": "schedule", "error": "timed out"},
        ],
    }
    coordinator.client = SimpleNamespace(
        async_get_app_schedules=AsyncMock(return_value=failed_app_payload),
        async_get_batch_schedules=AsyncMock(side_effect=TimeoutError),
    )

    result = asyncio.run(coordinator.async_refresh_schedules(force=True))

    assert result["schedules"][0]["version"] == 1
    assert coordinator.schedules_refreshed_at is None


def test_batch_metadata_reuses_fresh_schedule_fetch() -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        coordinator.schedules = {
            "source": "batch_device_data_schedule",
            "available": True,
            "schedules": [{"idx": 0, "plans": []}],
            "errors": [],
        }
        coordinator.schedules_refreshed_at = datetime.now(UTC)
        coordinator.app_maps = {"current_map_index": 0}
        coordinator.client = SimpleNamespace(
            async_get_batch_schedules=AsyncMock(),
            async_get_batch_mowing_preferences=AsyncMock(
                return_value={"maps": []}
            ),
            async_get_batch_ota_info=AsyncMock(return_value={"available": True}),
        )

        schedule, preferences, ota = await coordinator._async_fetch_batch_device_data()

        assert schedule is coordinator.schedules
        assert preferences == {"maps": []}
        assert ota == {"available": True}
        coordinator.client.async_get_batch_schedules.assert_not_awaited()

    asyncio.run(scenario())


def test_performance_tracker_keeps_phase_and_aggregate_timings() -> None:
    values = iter((0.0, 1.0, 3.0, 5.0))
    tracker = DreameLawnMowerPerformanceTracker(
        limit=3,
        clock=lambda: next(values),
    )

    async def scenario() -> None:
        cycle = tracker.start("setup")
        assert await cycle.measure("first_refresh", lambda: _result("ready")) == "ready"
        cycle.finish()

    asyncio.run(scenario())

    diagnostics = tracker.as_dict()
    assert diagnostics["sample_limit"] == 3
    assert diagnostics["summary"]["setup"] == {
        "count": 1,
        "latest_ms": 5000.0,
        "average_ms": 5000.0,
        "maximum_ms": 5000.0,
        "outcomes": {"completed": 1},
    }
    assert diagnostics["samples"][0]["phases_ms"] == {"first_refresh": 2000.0}


def test_performance_tracker_retains_latest_setup_after_recent_buffer_rolls() -> None:
    values = iter((0.0, 1.0, 2.0, 3.0, 4.0, 5.0))
    tracker = DreameLawnMowerPerformanceTracker(
        limit=2,
        clock=lambda: next(values),
    )

    tracker.start("setup").finish()
    tracker.start("foreground_refresh").finish()
    tracker.start("foreground_refresh").finish()

    diagnostics = tracker.as_dict()
    assert [sample["operation"] for sample in diagnostics["samples"]] == [
        "foreground_refresh",
        "foreground_refresh",
    ]
    assert diagnostics["latest_by_operation"]["setup"]["total_ms"] == 1000.0
    assert "captured_at" not in diagnostics["latest_by_operation"]["setup"]
    assert diagnostics["summary"]["foreground_refresh"] == {
        "count": 2,
        "latest_ms": 1000.0,
        "average_ms": 1000.0,
        "maximum_ms": 1000.0,
        "outcomes": {"completed": 2},
    }


def test_cancelled_foreground_refresh_records_cancelled_outcome() -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        coordinator.performance = DreameLawnMowerPerformanceTracker()
        coordinator._foreground_refresh_count = 0
        refresh_started = asyncio.Event()

        async def refresh() -> None:
            refresh_started.set()
            await asyncio.Event().wait()

        coordinator.client = SimpleNamespace(async_refresh=refresh)
        task = asyncio.create_task(coordinator._async_update_data())
        await asyncio.wait_for(refresh_started.wait(), timeout=1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        sample = coordinator.performance.as_dict()["samples"][-1]
        assert sample["operation"] == "foreground_refresh"
        assert sample["outcome"] == "cancelled"

    asyncio.run(scenario())


def test_shutdown_drains_metadata_before_closing_shared_client() -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        metadata_started = asyncio.Event()
        metadata_finished = asyncio.Event()
        close_calls: list[str] = []

        async def metadata() -> None:
            metadata_started.set()
            try:
                await metadata_finished.wait()
            except asyncio.CancelledError:
                await metadata_finished.wait()
                raise

        async def close() -> None:
            close_calls.append("close")

        coordinator._shutting_down = False
        coordinator._client_update_pending = False
        coordinator._client_update_task = None
        coordinator._metadata_refresh_task = asyncio.create_task(metadata())
        coordinator._metadata_shutdown_close_task = None
        coordinator.hass = SimpleNamespace(
            async_create_task=lambda coroutine, _name: asyncio.create_task(coroutine)
        )
        coordinator.client = SimpleNamespace(
            set_update_callback=Mock(),
            async_close=close,
        )
        await metadata_started.wait()

        shutdown = asyncio.create_task(coordinator.async_shutdown())
        await asyncio.sleep(0)
        assert close_calls == []

        metadata_finished.set()
        await shutdown

        assert close_calls == ["close"]

    asyncio.run(scenario())


def test_shutdown_defers_client_close_after_metadata_grace_expires() -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        metadata_started = asyncio.Event()
        metadata_finished = asyncio.Event()
        close_calls: list[str] = []

        async def metadata() -> None:
            metadata_started.set()
            try:
                await metadata_finished.wait()
            except asyncio.CancelledError:
                await metadata_finished.wait()
                raise

        async def close() -> None:
            close_calls.append("close")

        coordinator._shutting_down = False
        coordinator._client_update_pending = False
        coordinator._client_update_task = None
        coordinator._metadata_refresh_task = asyncio.create_task(metadata())
        coordinator._metadata_shutdown_close_task = None
        coordinator.hass = SimpleNamespace(
            async_create_task=lambda coroutine, _name: asyncio.create_task(coroutine)
        )
        coordinator.client = SimpleNamespace(
            set_update_callback=Mock(),
            async_close=close,
        )
        await metadata_started.wait()

        with patch(
            "custom_components.dreame_lawn_mower.coordinator_refresh."
            "METADATA_SHUTDOWN_GRACE_SECONDS",
            0.01,
        ):
            await asyncio.wait_for(coordinator.async_shutdown(), timeout=1)

        assert close_calls == []
        cleanup = coordinator._metadata_shutdown_close_task
        assert cleanup is not None
        assert not cleanup.done()

        metadata_finished.set()
        await asyncio.wait_for(cleanup, timeout=1)

        assert close_calls == ["close"]

    asyncio.run(scenario())


def test_failed_platform_setup_removes_coordinator_and_drains_resources() -> None:
    async def scenario() -> None:
        performance = DreameLawnMowerPerformanceTracker()
        coordinator = SimpleNamespace(
            performance=performance,
            client=SimpleNamespace(descriptor=SimpleNamespace(did="device-1")),
            async_config_entry_first_refresh=AsyncMock(),
            async_shutdown=AsyncMock(),
            _metadata_refresh_task=object(),
        )
        cache = SimpleNamespace(async_load=AsyncMock())
        hass = SimpleNamespace(
            data={},
            config_entries=SimpleNamespace(
                async_forward_entry_setups=AsyncMock(
                    side_effect=RuntimeError("platform failed")
                )
            ),
        )
        entry = SimpleNamespace(
            entry_id="entry-1",
            options={},
        )

        with (
            patch.object(
                integration_module,
                "DreameLawnMowerCoordinator",
                return_value=coordinator,
            ),
            patch.object(
                integration_module,
                "DreameLawnMowerVideoLanCache",
                return_value=cache,
            ),
            patch.object(
                integration_module,
                "DreameLawnMowerVideoProvisioningCache",
                return_value=cache,
            ),
            patch.object(integration_module, "async_setup_point_cloud_api"),
            patch.object(
                integration_module,
                "async_setup_services",
                new=AsyncMock(),
            ),
        ):
            try:
                await async_setup_entry(hass, entry)
            except RuntimeError as err:
                assert str(err) == "platform failed"
            else:
                raise AssertionError("setup failure was not propagated")

        coordinator.async_shutdown.assert_awaited_once_with()
        assert "entry-1" not in hass.data[DOMAIN]
        sample = performance.as_dict()["latest_by_operation"]["setup"]
        assert sample["outcome"] == "RuntimeError"

    asyncio.run(scenario())


def test_initial_connection_failure_keeps_complete_platform_setup_pending() -> None:
    async def scenario() -> None:
        performance = DreameLawnMowerPerformanceTracker()
        coordinator = SimpleNamespace(
            performance=performance,
            client=SimpleNamespace(descriptor=SimpleNamespace(did="device-1")),
            async_config_entry_first_refresh=AsyncMock(
                side_effect=ConfigEntryNotReady("offline")
            ),
            async_shutdown=AsyncMock(),
            _metadata_refresh_task=None,
        )
        cache = SimpleNamespace(
            async_load=AsyncMock(),
            inputs=object(),
            endpoint=object(),
            device_config=object(),
        )
        forward = AsyncMock()
        hass = SimpleNamespace(
            data={},
            config_entries=SimpleNamespace(async_forward_entry_setups=forward),
        )
        entry = SimpleNamespace(entry_id="entry-1", options={})

        with (
            patch.object(
                integration_module,
                "DreameLawnMowerCoordinator",
                return_value=coordinator,
            ),
            patch.object(
                integration_module,
                "DreameLawnMowerVideoLanCache",
                return_value=cache,
            ),
            patch.object(
                integration_module,
                "DreameLawnMowerVideoProvisioningCache",
                return_value=cache,
            ),
        ):
            try:
                await async_setup_entry(hass, entry)
            except ConfigEntryNotReady:
                pass
            else:
                raise AssertionError("initial connectivity failure was hidden")

        forward.assert_not_awaited()
        coordinator.async_shutdown.assert_awaited_once_with()
        assert entry.entry_id not in hass.data.get(DOMAIN, {})

    asyncio.run(scenario())


async def _result(value: str) -> str:
    return value
