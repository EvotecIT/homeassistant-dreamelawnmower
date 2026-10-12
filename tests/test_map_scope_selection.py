"""Host identity and geometry selection must preserve one coordinate owner."""

import asyncio
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from .test_app_action_live_map import (
    map_render_style,
    native_maps,
    publish,
    tracked_client,
)
from .test_app_map_read_consistency import _native_reads
from .test_runtime_map_identity import _coordinator
from .test_vector_map import _batch_payload


def history_batch():
    batch = _batch_payload()
    raw = "".join(batch[f"MAP.{index}"] for index in range(3))
    record = json.loads(json.loads(raw[:int(batch["MAP.info"])])[0])
    for field in ("mowingAreas", "paths", "spotAreas"):
        record.pop(field, None)
    return {
        "MAP.0": json.dumps([json.dumps(record)]),
        **{key: value for key, value in batch.items() if key.startswith("M_PATH")},
    }


@pytest.mark.parametrize("identity", [1, None, "expired"])
def test_mapl_switch_fences_cached_old_geometry_before_telemetry(identity):
    async def scenario():
        client = tracked_client()
        scene = client._sync_get_mowing_map_scene(
            map_index=0, style=map_render_style(), label_scale=1,
        )
        assert client.mowing_map_runtime_overlay(scene)["position_status"] == "current"
        coordinator = _coordinator()
        coordinator.client = client
        client.async_get_current_app_map_index = AsyncMock(return_value=identity)
        if identity == "expired":
            client.runtime_map_identity_expires_at = (
                datetime.now(UTC) - timedelta(seconds=1)
            )
        else:
            await coordinator._async_refresh_runtime_map_index(force=True)
        # MAPL has completed but the subsequent telemetry transport has not.
        client.update_runtime_live_tracking(None, active=False, map_index=None)
        changed_old = deepcopy(native_maps())
        changed_old["maps"][0]["payload"]["map"][0]["data"][1][0] = 1100
        client._sync_get_app_maps = lambda **kwargs: changed_old
        old = client._sync_refresh_vector_map_view(current_map_index=0)
        assert old.details.get("runtime_position_valid", False) is False
        retained = client.mowing_map_runtime_overlay(scene)
        assert retained["position_status"] == "last_known"
        assert retained["position"] is not None
        assert retained["trail"] == []

    asyncio.run(scenario())


@pytest.mark.parametrize("native", [False, True])
def test_history_only_batch_uses_the_same_app_frame_in_all_views(monkeypatch, native):
    client = tracked_client()
    batch = history_batch()
    client._sync_get_vector_map_batch_data = lambda: batch
    _native_reads(monkeypatch, client, lambda: batch, native_maps)

    async def scenario():
        async def camera():
            return await client.async_refresh_vector_map_view() if native else (
                client._sync_refresh_vector_map_view()
            )

        first = await camera()
        assert first.source == "app_action_map"
        scene = await client.async_get_mowing_map_scene(
            map_index=0, style=map_render_style(), label_scale=1,
        ) if native else client._sync_get_mowing_map_scene(
            map_index=0, style=map_render_style(), label_scale=1,
        )
        publish(client, hex="same-frame-next-packet")
        second = await camera()
        details = await client.async_get_vector_map_details() if native else (
            client._sync_get_vector_map_details()
        )
        assert second.details["runtime_track_point_count"] == 6
        assert details["runtime_track_point_count"] == 6
        assert len(client.mowing_map_runtime_overlay(scene)["trail"]) == 2
        assert second.details["runtime_position_valid"] is True

    asyncio.run(scenario())


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("fails", [False, True])
def test_history_only_batch_remains_viewable_when_app_geometry_is_missing(
    monkeypatch, native, fails,
):
    client = tracked_client()
    client._sync_get_vector_map_batch_data = history_batch

    def app_maps(**kwargs):
        if fails:
            raise TimeoutError("Optional app map unavailable")
        return {}

    client._sync_get_app_maps = app_maps
    _native_reads(monkeypatch, client, history_batch, app_maps)
    result = asyncio.run(client.async_refresh_vector_map_view()) if native else (
        client._sync_refresh_vector_map_view()
    )
    assert result.source == "batch_vector_map"
    assert result.summary.available
    assert result.details["mow_path_point_count"] > 0
    assert result.image_png is not None
    details = asyncio.run(client.async_get_vector_map_details()) if native else (
        client._sync_get_vector_map_details()
    )
    assert details["available"]
    assert details["mow_path_point_count"] > 0
