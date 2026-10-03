"""Packed PREI revisions through client writes and HA reconciliation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    attempted_write_fields,
)
from custom_components.dreame_lawn_mower.preference_cache import (
    merge_mowing_preference_readbacks,
    mowing_preference_map_read_complete,
    reconcile_pending_preference_readbacks,
    retain_confirmed_preference_write,
)
from dreame_lawn_mower_client import (
    DreameLawnMowerClient,
    DreameLawnMowerConnectionError,
)
from dreame_lawn_mower_client.models import DreameLawnMowerDescriptor


class _PreferenceCloud:
    """Keep PRE and PREI wire representations separate at the network boundary."""

    logged_in = True

    def __init__(self, reported_version: int, version: int) -> None:
        self.reported_version = reported_version
        self.mode = 0
        self.preference = [version, 0, 0, 1, 45, 2, 90, 1, 0, 1, 1, 2, 1, 15, 20, 7, 1]
        self.writes: list[dict[str, object]] = []

    def call_app_action(self, payload, *, siid=2, aiid=50):
        assert (siid, aiid) == (2, 50)
        if payload["m"] == "s":
            if payload["t"] == "PREP":
                self.mode = payload["d"]["value"]
                self.preference[4] = 45
                self.preference[13] = 25
            else:
                assert payload["t"] == "PRE"
                assert payload["d"][0] == self.preference[0]
                self.preference = list(payload["d"])
            self.writes.append(payload)
            self.preference[0] = (
                (self.preference[0] + 1) & 0xFF
                if self.preference[0] <= 0xFF
                else self.preference[0] + 1
            )
            self.reported_version += 1
            data = {"r": 0}
        elif payload["t"] == "PREI":
            data = {"type": self.mode, "ver": [[0, self.reported_version]]}
        else:
            assert payload == {"m": "g", "t": "PRE", "d": {"idx": 0, "region": 0}}
            data = list(self.preference)
        return {"out": [{"m": "r", "r": 0, "d": data}]}


def _client(cloud: _PreferenceCloud) -> DreameLawnMowerClient:
    client = DreameLawnMowerClient(
        username="user@example.invalid",
        password="secret",
        country="eu",
        account_type="dreame",
        descriptor=DreameLawnMowerDescriptor(
            did="device-1",
            name="Mower",
            model="dreame.mower.g2568a",
            display_model="A2",
            account_type="dreame",
            country="eu",
        ),
    )
    client._sync_get_cloud_protocol = lambda: cloud
    return client


@pytest.mark.parametrize(
    ("reported_version", "version"),
    [(1769, 233), (1770, 234), (256, 0), (1791, 255), (300, 300)],
)
@pytest.mark.parametrize("execute", [False, True])
def test_preference_update_uses_pre_revision_and_preserves_raw_prei(
    reported_version: int,
    version: int,
    execute: bool,
) -> None:
    cloud = _PreferenceCloud(reported_version, version)
    client = _client(cloud)

    result = client._sync_plan_app_mowing_preference_update(
        map_index=0,
        area_id=0,
        changes={"mowing_height_cm": 5.0},
        execute=execute,
        confirm_write=execute,
    )

    assert result["payload"][0] == version
    assert result["payload"][4] == 50
    assert result["previous_preference"]["reported_version"] == reported_version
    assert result["previous_preference"]["version"] == version
    assert result["executed"] is execute
    assert result["request_verified"] is execute
    assert len(cloud.writes) == int(execute)
    if execute:
        preference = result["readback"]["preference"]
        assert preference["version"] == (
            (version + 1) & 0xFF if version <= 0xFF else version + 1
        )
        assert preference["reported_version"] == reported_version + 1
        assert preference["mowing_height_cm"] == 5.0


@pytest.mark.parametrize(
    ("reported_version", "version"),
    [(1770, 233), (256, 255), (233, 489)],
)
def test_mismatched_revision_is_rejected_before_write(
    reported_version: int,
    version: int,
) -> None:
    cloud = _PreferenceCloud(reported_version, version)
    client = _client(cloud)

    result = client._sync_get_mowing_preferences(map_indices=[0])

    assert result["maps"][0]["preferences"] == []
    assert f"PREI advertised version {reported_version}" in result["errors"][0]["error"]
    with pytest.raises(ValueError, match="area 0 was not found"):
        client._sync_plan_app_mowing_preference_update(
            map_index=0,
            area_id=0,
            changes={"mowing_height_cm": 5.0},
            execute=True,
            confirm_write=True,
        )
    assert cloud.writes == []


@pytest.mark.parametrize(("reported_version", "version"), [(1770, 234), (1791, 255)])
def test_packed_direct_read_overrides_batch_and_confirmation_converges(
    reported_version: int,
    version: int,
) -> None:
    cloud = _PreferenceCloud(reported_version, version)
    client = _client(cloud)
    direct = client._sync_get_mowing_preferences(map_indices=[0])
    batch = {
        "available": True,
        "maps": [
            {
                "idx": 0,
                "mode": 0,
                "mode_name": "global",
                "area_count": 1,
                "preferences": [
                    {
                        "map_index": 0,
                        "area_id": 0,
                        "version": version,
                        "reported_version": version,
                        "mowing_height_cm": 4.0,
                    }
                ],
            }
        ],
    }

    assert mowing_preference_map_read_complete(
        direct["maps"][0],
        require_version_evidence=True,
    )
    merged = merge_mowing_preference_readbacks(direct, batch)
    preference = merged["maps"][0]["preferences"][0]
    assert preference["mowing_height_cm"] == 4.5
    assert preference["reported_version"] == reported_version

    write = client._sync_plan_app_mowing_preference_update(
        map_index=0,
        area_id=0,
        changes={"mowing_height_cm": 5.0},
        execute=True,
        confirm_write=True,
    )
    pending = retain_confirmed_preference_write(
        write_result=write, pending=[], confirmed_at=datetime.now(UTC)
    )
    assert pending
    stale, unresolved = reconcile_pending_preference_readbacks(batch, pending)
    assert unresolved == pending
    assert stale["maps"][0]["preferences"][0]["mowing_height_cm"] == 5.0

    batch_preference = batch["maps"][0]["preferences"][0]
    confirmed_version = (version + 1) & 0xFF
    batch_preference.update(
        version=confirmed_version,
        reported_version=confirmed_version,
        mowing_height_cm=5.0,
    )
    converged, unresolved = reconcile_pending_preference_readbacks(batch, pending)
    assert unresolved == []
    assert converged is batch

    batch_preference.update(
        version=confirmed_version + 1,
        reported_version=confirmed_version + 1,
        mowing_height_cm=6.0,
    )
    conservative, unresolved = reconcile_pending_preference_readbacks(batch, pending)
    assert unresolved == pending
    assert conservative["maps"][0]["preferences"][0]["mowing_height_cm"] == 5.0

    batch_preference["reported_version"] = reported_version + 2
    superseded, unresolved = reconcile_pending_preference_readbacks(batch, pending)
    assert unresolved == []
    assert superseded["maps"][0]["preferences"][0]["mowing_height_cm"] == 6.0


@pytest.mark.parametrize("initial_height", [45, 50])
def test_combined_write_rereads_pre_after_mode_advances_revision(
    initial_height: int,
) -> None:
    cloud = _PreferenceCloud(1770, 234)
    cloud.preference[4] = initial_height
    client = _client(cloud)

    result = client._sync_plan_app_mowing_preference_update(
        map_index=0,
        area_id=0,
        changes={"preference_mode": "custom", "mowing_height_cm": 5.0},
        execute=True,
        confirm_write=True,
    )

    assert [request["t"] for request in cloud.writes] == ["PREP", "PRE"]
    assert cloud.writes[1]["d"][0] == 235
    assert cloud.writes[1]["d"][13] == 25
    assert result["payload"] == cloud.writes[1]["d"]
    assert result["previous_preference"]["version"] == 235
    assert result["readback"]["preference"]["version"] == 236
    assert result["readback"]["preference"]["reported_version"] == 1772
    assert result["readback"]["preference"]["mowing_height_cm"] == 5.0
    assert result["request_verified"] is True
    assert result["changed_fields"] == ["preference_mode", "mowing_height_cm"]
    pending = retain_confirmed_preference_write(
        [], result, confirmed_at=datetime.now(UTC)
    )
    height_confirmation = next(p for p in pending if p.field == "mowing_height_cm")
    assert height_confirmation.values == {"mowing_height_cm": 5.0}


@pytest.mark.parametrize("read_failure", [True, False])
def test_combined_write_stops_when_post_mode_snapshot_is_unconfirmed(
    read_failure: bool,
) -> None:
    cloud = _PreferenceCloud(1770, 234)
    client = _client(cloud)
    call_app_action = cloud.call_app_action

    def unconfirmed_mode(payload, **kwargs):
        if cloud.writes and payload["m"] == "g" and payload["t"] == "PREI":
            if read_failure:
                raise DreameLawnMowerConnectionError("read unavailable")
            cloud.mode = 0
        return call_app_action(payload, **kwargs)

    cloud.call_app_action = unconfirmed_mode
    with pytest.raises((ValueError, DreameLawnMowerConnectionError)) as exc_info:
        client._sync_plan_app_mowing_preference_update(
            map_index=0,
            area_id=0,
            changes={"preference_mode": "custom", "mowing_height_cm": 5.0},
            execute=True,
            confirm_write=True,
        )

    assert [request["t"] for request in cloud.writes] == ["PREP"]
    assert attempted_write_fields(exc_info.value) == ("preference_mode",)
