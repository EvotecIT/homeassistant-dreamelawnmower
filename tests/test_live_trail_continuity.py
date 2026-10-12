"""Current-session trails survive verification gaps without borrowing new scope."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.app_protocol import (
    decode_mower_status_blob,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_visuals import (
    map_render_style,
)

from .test_app_action_live_map import (
    a1_pro_packet,
    native_maps,
    publish,
    tracked_client,
)
from .test_runtime_map_identity import _coordinator


def _scene(client):
    return client._sync_get_mowing_map_scene(
        map_index=0, style=map_render_style(), label_scale=1,
    )


def _expire(client):
    client.runtime_map_identity_expires_at = datetime.now(UTC) - timedelta(seconds=1)


def _renew(client, index=0):
    client.update_runtime_map_identity(
        index, expires_at=datetime.now(UTC) + timedelta(seconds=60),
    )


def _next_packet(client, **changes):
    return publish(client, hex="next-verified", candidate_runtime_track_segments=(
        ((200, 100), (300, 100)),
    ), **changes)


@pytest.mark.parametrize("gap", ["expiry", "revocation", "unscoped"])
@pytest.mark.parametrize("reader", ["camera", "cached_scene"])
def test_same_session_resumes_its_trail_after_map_verification_gap(gap, reader):
    client = tracked_client()
    scene = _scene(client)
    assert len(client.mowing_map_runtime_overlay(scene)["trail"][0]) == 3
    if gap == "expiry":
        _expire(client)
    elif gap == "revocation":
        client.runtime_map_identity_expires_at = None
    else:
        client.update_runtime_live_tracking(None, active=True, map_index=None)
    hidden = client.mowing_map_runtime_overlay(scene)
    assert hidden["position_status"] == "last_known"
    assert hidden["trail"] == []
    _renew(client)
    assert client.mowing_map_runtime_overlay(scene)["position_status"] == "last_known"
    _next_packet(client)
    if reader == "camera":
        details = client._sync_get_vector_map_details()
        assert details["position_status"] == "current"
        assert details["runtime_track_point_count"] == 5
    else:
        overlay = client.mowing_map_runtime_overlay(scene)
        assert overlay["position_status"] == "current"
        assert sum(map(len, overlay["trail"])) == 5


def test_isolated_runtime_points_cannot_evict_drawable_session_trail():
    client = tracked_client()
    scene = _scene(client)
    # This is the shape in the reporter's fresh packet: two separate points,
    # neither a drawable line. Do not join across their explicit trace break.
    for index in range(40):
        publish(client, hex=f"isolated-{index}", candidate_runtime_track_segments=(
            ((150, 100),), ((170, 100),),
        ))
    details = client._sync_get_vector_map_details()
    assert details["runtime_track_point_count"] == 3
    overlay = client.mowing_map_runtime_overlay(scene)
    assert len(overlay["trail"]) == 1
    assert len(overlay["trail"][0]) == 3


@pytest.mark.parametrize("boundary", [
    "map", "task", "geometry", "inactive", "recovery_timeout",
])
def test_recovery_never_attaches_prior_trail_to_a_different_context(boundary):
    client = tracked_client()
    scene = _scene(client)
    _expire(client)
    if boundary == "map":
        _renew(client, 1)
    elif boundary == "inactive":
        client.update_runtime_live_tracking(None, active=False)
    elif boundary == "geometry":
        maps = native_maps()
        maps["maps"][0]["payload"]["map"][0]["data"][1][0] += 10
        client._sync_get_app_maps = lambda **kwargs: maps
        # The new geometry must be observed after identity renewal below.
    _renew(client)
    if boundary == "geometry":
        scene = _scene(client)
    if boundary == "recovery_timeout":
        # A long outage does not keep extending a recovery window on each read.
        owner = import_module(client.update_runtime_live_tracking.__module__)
        with patch.object(owner, "datetime") as clock:
            clock.now.return_value = datetime.now(UTC) + timedelta(seconds=91)
            client.update_runtime_map_identity(
                0, expires_at=clock.now.return_value + timedelta(seconds=60),
            )
            _next_packet(client)
    else:
        _next_packet(client, **(
            {"candidate_runtime_task_id": 101} if boundary == "task" else {}
        ))
    overlay = client.mowing_map_runtime_overlay(scene)
    assert sum(map(len, overlay["trail"])) == 2


def test_cached_unscoped_packet_does_not_restore_a_suspended_trail():
    client = tracked_client()
    scene = _scene(client)
    _expire(client)
    packet = _next_packet(client)
    _renew(client)
    client.update_runtime_live_tracking(packet, active=True, map_index=0)
    overlay = client.mowing_map_runtime_overlay(scene)
    assert overlay["position_status"] == "last_known"
    assert overlay["trail"] == []
    publish(client, hex="fresh-after-unscoped", candidate_runtime_track_segments=(
        ((300, 100), (400, 100)),
    ))
    assert sum(map(len, client.mowing_map_runtime_overlay(scene)["trail"])) == 5


def test_rejected_cached_packet_cannot_bypass_trail_recovery_timeout():
    client = tracked_client()
    scene = _scene(client)
    _expire(client)
    packet = _next_packet(client)
    _renew(client)
    client.update_runtime_live_tracking(packet, active=True, map_index=0)
    assert client.mowing_map_runtime_overlay(scene)["trail"] == []
    client.update_runtime_map_identity(
        0, expires_at=datetime.now(UTC) + timedelta(seconds=180),
    )
    # Replaying an event consumed without a verified map cannot count as fresh
    # scoped evidence, even if genuinely new telemetry arrives much later.
    owner = import_module(client.update_runtime_live_tracking.__module__)
    with patch.object(owner, "datetime") as clock:
        clock.now.return_value = datetime.now(UTC) + timedelta(seconds=91)
        publish(client, hex="fresh-after-long-gap", candidate_runtime_track_segments=(
            ((300, 100), (400, 100)),
        ))
    assert sum(map(len, client.mowing_map_runtime_overlay(scene)["trail"])) == 2


@pytest.mark.parametrize("reader", ["renewal", "coordinator"])
def test_lazy_expiry_does_not_restart_an_already_elapsed_recovery_window(reader):
    client = tracked_client()
    scene = _scene(client)
    client._runtime_map_identity_expires_at = datetime.now(UTC) - timedelta(seconds=120)
    if reader == "coordinator":
        host = _coordinator()
        host.client = client
        host._runtime_map_identity_verified = True
        host._runtime_map_index_refreshed_at = (
            datetime.now(UTC) - timedelta(seconds=180)
        )
        host._expire_runtime_map_identity()
    _renew(client)
    _next_packet(client)
    assert sum(map(len, client.mowing_map_runtime_overlay(scene)["trail"])) == 2


@pytest.mark.parametrize("packet", ["short13", "short22", "invalid", "missing"])
def test_incomplete_task_evidence_waits_for_fresh_same_task_confirmation(packet):
    client = tracked_client()
    scene = _scene(client)
    original_trail = client.mowing_map_runtime_overlay(scene)["trail"]
    _expire(client)
    _renew(client)
    raw = [0xCE] + [0] * (20 if packet == "short22" else 11) + [0xCE]
    if packet == "invalid":
        raw[-1] = 0
    blob = replace(
        decode_mower_status_blob(raw), received_at=datetime.now(UTC).isoformat(),
    )
    if packet == "missing":
        blob = None
    client.update_runtime_live_tracking(blob, active=True, map_index=0)
    # A normal camera observation must not treat missing task evidence as a
    # different task, whether a short pose, malformed frame or absent callback.
    client._sync_get_vector_map_details()
    _next_packet(client)
    recovered = client.mowing_map_runtime_overlay(scene)["trail"]
    assert recovered[:len(original_trail)] == original_trail
    assert len(recovered[-1]) == 2


def test_fresh_unchanged_packet_recovers_cached_scene_without_duplicate_points():
    client = tracked_client()
    scene = _scene(client)
    _expire(client)
    _renew(client)
    client.update_runtime_live_tracking(
        replace(a1_pro_packet(), received_at=datetime.now(UTC).isoformat()),
        active=True, map_index=0,
    )
    overlay = client.mowing_map_runtime_overlay(scene)
    assert overlay["position_status"] == "current"
    assert sum(map(len, overlay["trail"])) == 3


def test_native_camera_and_scene_share_recovered_session_trail(monkeypatch):
    client = tracked_client()
    _scene(client)
    _expire(client)
    _renew(client)
    _next_packet(client)

    async def batch(*args):
        return {}

    async def app_maps(**kwargs):
        return native_maps()

    async def cloud_read(read):
        return await read(SimpleNamespace(async_get_batch_device_datas=batch))

    async def state_read(_client, read, **kwargs):
        return await asyncio.to_thread(read, None)

    client._async_cloud_read = cloud_read
    client.async_get_app_maps = app_maps
    owner = import_module(
        f"{client.async_refresh_vector_map_view.__module__.rsplit('.', 1)[0]}"
        ".client_vector_reads"
    )
    monkeypatch.setattr(owner, "async_read_device_state", state_read)

    async def read():
        view = await client.async_refresh_vector_map_view(current_map_index=0)
        scene = await client.async_get_mowing_map_scene(
            map_index=0, style=map_render_style(), label_scale=1,
        )
        assert view.details["runtime_track_point_count"] == 5
        assert sum(map(len, client.mowing_map_runtime_overlay(scene)["trail"])) == 5

    asyncio.run(read())
