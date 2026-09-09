"""Identity retirement reaches real map readers before suspended work resumes."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_visuals import (
    map_render_style,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.mowing_map import (
    build_mowing_map_scene,
)
from tests.test_mowing_map import garden, telemetry
from tests.test_runtime_map_identity import _coordinator
from tests.test_vector_map import _batch_payload, _client


def _seeded_client():
    client = _client()
    now = datetime.now(UTC)
    client._latest_snapshot = SimpleNamespace(
        available=True, activity="mowing", mowing_session_active=True
    )
    client.runtime_map_identity_expires_at = now + timedelta(seconds=60)
    client.update_runtime_live_tracking(
        replace(telemetry(), received_at=now.isoformat()), active=True, map_index=2
    )
    scene = build_mowing_map_scene(garden(), style=map_render_style())
    assert client.mowing_map_runtime_overlay(scene)["position_status"] == "current"
    return client, scene


@pytest.mark.parametrize("reason", ["mqtt_expiry", "map_change", "failure", "cancel"])
def test_invalidation_retires_overlay_before_pending_read_completes(reason):
    async def scenario():
        client, scene = _seeded_client()
        coordinator = _coordinator()
        coordinator.client = client
        coordinator.data = client._latest_snapshot
        coordinator._runtime_map_identity_verified = True
        coordinator._runtime_active_map_index = 2
        coordinator._runtime_map_index_refreshed_at = datetime.now(UTC)
        coordinator.async_set_updated_data = Mock()
        coordinator._schedule_metadata_refresh = Mock()
        started, release = asyncio.Event(), asyncio.Event()

        async def pending_blob(**kwargs):
            started.set()
            await release.wait()
            return None

        client.async_get_runtime_status_blob = pending_blob
        client.async_get_cached_snapshot = AsyncMock(return_value=coordinator.data)
        client.async_get_bluetooth_connected = AsyncMock(return_value=False)
        if reason == "mqtt_expiry":
            coordinator._runtime_map_index_refreshed_at -= timedelta(seconds=61)
            task = asyncio.create_task(coordinator._async_process_client_update())
            await started.wait()
        elif reason == "cancel":
            client.async_get_current_app_map_index = pending_blob
            task = asyncio.create_task(
                coordinator._async_refresh_runtime_map_index(force=True)
            )
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            client.async_get_current_app_map_index = AsyncMock(return_value=3)
            if reason == "failure":
                client.async_get_current_app_map_index.side_effect = TimeoutError()
            await coordinator._async_refresh_runtime_map_index(force=True)
            task = None
        try:
            overlay = client.mowing_map_runtime_overlay(scene)
            assert overlay["position_status"] == "last_known"
            assert overlay["position"] is not None
            assert overlay["trail"] == []
            assert client._runtime_live_map_index is None
        finally:
            release.set()
            if task is not None and not task.cancelled():
                await task

    asyncio.run(scenario())


def test_overlay_and_packets_enforce_deadline_without_coordinator_callbacks():
    client, scene = _seeded_client()
    deadline = client.runtime_map_identity_expires_at
    with patch(f"{client._expire_runtime_live_tracking.__module__}.datetime") as clock:
        clock.now.return_value = deadline + timedelta(seconds=1)
        overlay = client.mowing_map_runtime_overlay(scene)
        assert overlay["position_status"] == "last_known"
        assert overlay["trail"] == []
        client.update_runtime_live_tracking(
            replace(telemetry(), hex="late-packet"), active=True, map_index=2
        )
        assert client._runtime_live_map_index is None
        assert client._latest_runtime_status_blob is None


@pytest.mark.parametrize("active", [False, True])
def test_revocation_requires_new_identity_before_accepting_poses(active):
    client, scene = _seeded_client()
    client.runtime_map_identity_expires_at = None
    packet_time = datetime.now(UTC) + timedelta(seconds=1)
    client.update_runtime_live_tracking(
        replace(telemetry(), received_at=packet_time.isoformat(), hex="revoked"),
        active=active, map_index=2,
    )
    assert client.mowing_map_runtime_overlay(scene)["position_status"] == "last_known"
    assert client._runtime_live_map_index is None
    client.runtime_map_identity_expires_at = packet_time + timedelta(seconds=60)
    client.update_runtime_live_tracking(replace(
        telemetry(), received_at=(packet_time + timedelta(seconds=1)).isoformat(),
        hex="fresh-verified",
    ), active=True, map_index=2)
    assert client.mowing_map_runtime_overlay(scene)["position_status"] == "current"


def test_vector_camera_enforces_deadline_without_coordinator_callbacks():
    client = _client()
    now = datetime.now(UTC)
    client._latest_snapshot = SimpleNamespace(available=True, activity="mowing")
    client._sync_get_vector_map_batch_data = lambda: _batch_payload()
    client._safe_map_diagnostics = lambda **kwargs: None
    client.runtime_map_identity_expires_at = now + timedelta(seconds=60)
    client.update_runtime_live_tracking(replace(
        telemetry(), received_at=now.isoformat(),
        candidate_runtime_pose_x=50, candidate_runtime_pose_y=50,
    ), active=True, map_index=0)
    current = client._sync_refresh_vector_map_view(current_map_index=0)
    assert current.details["position_status"] == "current"
    with patch(f"{client._expire_runtime_live_tracking.__module__}.datetime") as clock:
        clock.now.return_value = now + timedelta(seconds=61)
        expired = client._sync_refresh_vector_map_view(current_map_index=0)
    assert expired.details["position_status"] == "last_known"
    assert "runtime_track_point_count" not in expired.details
