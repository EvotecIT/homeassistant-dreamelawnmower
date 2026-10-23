"""One stored calibration turns validated map positions into GPS."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import voluptuous as vol

from custom_components.dreame_lawn_mower import _async_update_listener
from custom_components.dreame_lawn_mower.config_flow import (
    DreameLawnMowerOptionsFlow,
)
from custom_components.dreame_lawn_mower.const import (
    CONF_GEOREFERENCE,
    CONF_SCAN_INTERVAL,
    PLATFORMS,
)
from custom_components.dreame_lawn_mower.device_tracker import (
    LOCATION_ACCURACY_METERS,
    DreameLawnMowerGpsTracker,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_inventory import (  # noqa: E501
    selected_map_revision,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.position_tracking import (  # noqa: E501
    MowerPosition,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.zone_mowing_history import (  # noqa: E501
    ChargerPosition,
    ZoneMowingHistory,
)
from custom_components.dreame_lawn_mower.georeference import (
    SET_GEOREFERENCE_SCHEMA,
    Georeference,
    entry_georeference,
    store_georeference,
)
from custom_components.dreame_lawn_mower.lawn_mower import DreameLawnMower
from custom_components.dreame_lawn_mower.map_preview import CONF_MAP_RESTART_PREVIEW
from custom_components.dreame_lawn_mower.option_updates import EntryUpdateSnapshot
from tests.runtime_fixtures import runtime_hass

from .test_app_action_live_map import native_maps, publish, tracked_client
from .test_vector_map import _client
from .test_zone_mowing_history import HASH, _maps

# Calibration measured on a real lawn: the station at native (1160, -20).
REFERENCE = {
    "anchor_x": 1160,
    "anchor_y": -20,
    "latitude": 45.317421,
    "longitude": -73.837131,
    "heading": 227.33,
    "scale": 0.001,
}


def _stored(reference=None, *, revision=HASH, index=0):
    return {
        "maps": {str(index): {**(reference or REFERENCE), "map_revision": revision}}
    }


def _reference() -> Georeference:
    return Georeference.parse(REFERENCE)


def test_reference_point_converts_to_its_measured_gps_position() -> None:
    latitude, longitude = _reference().to_gps(1160 - 6836, -20 - 20145)

    assert latitude == pytest.approx(45.3174986, abs=1e-6)
    assert longitude == pytest.approx(-73.836883, abs=1e-6)


def test_anchor_lands_exactly_on_its_gps_position() -> None:
    assert _reference().to_gps(1160, -20) == (45.317421, -73.837131)


@pytest.mark.parametrize(
    "point",
    [
        (1160, -20),
        (-5676, -20165),
        (25000, 4000),
        (-12000.5, 830.25),
    ],
)
def test_gps_to_native_inverts_native_to_gps(point) -> None:
    georeference = _reference()
    latitude, longitude = georeference.to_gps(*point)
    x, y = georeference.to_native(latitude, longitude)

    assert x == pytest.approx(point[0], abs=1e-6)
    assert y == pytest.approx(point[1], abs=1e-6)


def test_heading_rotates_the_native_y_axis_clockwise_from_north() -> None:
    north_up = Georeference.parse({**REFERENCE, "heading": 0})
    east_up = Georeference.parse({**REFERENCE, "heading": 90})

    latitude, longitude = north_up.to_gps(1160, -20 + 10_000)
    assert latitude > REFERENCE["latitude"]
    assert longitude == pytest.approx(REFERENCE["longitude"])
    latitude, longitude = east_up.to_gps(1160, -20 + 10_000)
    assert latitude == pytest.approx(REFERENCE["latitude"])
    assert longitude > REFERENCE["longitude"]


def test_parse_normalizes_heading_and_defaults_scale() -> None:
    without_scale = {key: value for key, value in REFERENCE.items() if key != "scale"}

    assert Georeference.parse({**without_scale, "heading": -132.67}).heading == (
        pytest.approx(227.33)
    )
    assert Georeference.parse({**without_scale, "heading": 360}).heading == 0
    assert Georeference.parse(without_scale).scale == 0.001
    assert Georeference.parse(REFERENCE).as_dict() == {
        **REFERENCE,
        "anchor_x": 1160.0,
        "anchor_y": -20.0,
    }


@pytest.mark.parametrize(
    "change",
    [
        {"latitude": 90.5},
        {"latitude": -91},
        {"longitude": 180.1},
        {"longitude": -181},
        {"scale": 0},
        {"scale": -0.001},
        {"heading": float("nan")},
        {"anchor_x": float("inf")},
        {"anchor_y": True},
        {"latitude": "north"},
        {"heading": None},
    ],
)
def test_invalid_calibrations_are_rejected_by_parse_and_schema(change) -> None:
    value = {**REFERENCE, **change}

    with pytest.raises(ValueError):
        Georeference.parse(value)
    assert Georeference.from_mapping(value) is None
    with pytest.raises(vol.Invalid):
        vol.Schema(SET_GEOREFERENCE_SCHEMA)(value)


def test_action_schema_applies_the_stored_calibration_rules() -> None:
    without_scale = {key: value for key, value in REFERENCE.items() if key != "scale"}

    validated = vol.Schema(SET_GEOREFERENCE_SCHEMA)(
        {**without_scale, "heading": 587.33}
    )

    assert validated["heading"] == pytest.approx(227.33)
    assert validated["scale"] == 0.001
    # The action stores exactly what the stored-option reader accepts.
    assert Georeference.parse(validated).as_dict() == validated


def test_entry_option_is_the_only_calibration_reader() -> None:
    for value in ({}, {"latitude": 1}, REFERENCE):
        assert (
            entry_georeference(
                SimpleNamespace(options={CONF_GEOREFERENCE: value}), _maps(), 0
            )
            is None
        )
    assert (
        entry_georeference(
            SimpleNamespace(options={CONF_GEOREFERENCE: _stored()}), _maps(), 0
        )
        == _reference()
    )


def _entry(options=None):
    return SimpleNamespace(
        entry_id="mower", data={"did": "a2"}, options=dict(options or {})
    )


def _listener_hass(entry, coordinator):
    hass = runtime_hass(
        coordinators={entry.entry_id: coordinator},
        config_entries=SimpleNamespace(async_reload=AsyncMock()),
    )

    def update_entry(target, *, options):
        target.options = options
        asyncio.run(_async_update_listener(hass, target))

    hass.config_entries.async_update_entry = update_entry
    return hass


@pytest.mark.parametrize("initial", [{}, {CONF_GEOREFERENCE: REFERENCE}])
def test_saving_or_clearing_calibration_keeps_the_mower_connected(initial) -> None:
    entry = _entry({"map_theme": "emerald", **initial})
    coordinator = SimpleNamespace(
        applied_entry_update=EntryUpdateSnapshot.capture(entry),
        async_update_listeners=Mock(),
    )
    hass = _listener_hass(entry, coordinator)

    store_georeference(
        hass,
        entry,
        Georeference.parse({**REFERENCE, "heading": 12.5}),
        app_maps=_maps(),
        map_index=0,
    )
    store_georeference(hass, entry, None, app_maps=_maps(), map_index=0)

    hass.config_entries.async_reload.assert_not_called()
    assert coordinator.async_update_listeners.call_count == 2
    assert entry.options == {"map_theme": "emerald"}


def test_calibration_with_a_connection_change_still_reloads() -> None:
    entry = _entry()
    coordinator = SimpleNamespace(
        applied_entry_update=EntryUpdateSnapshot.capture(entry),
        async_update_listeners=Mock(),
    )
    hass = _listener_hass(entry, coordinator)
    entry.options = {CONF_GEOREFERENCE: REFERENCE, "video_transport": "cloud"}

    asyncio.run(_async_update_listener(hass, entry))

    hass.config_entries.async_reload.assert_awaited_once_with("mower")


def test_mower_actions_store_and_clear_the_entry_calibration() -> None:
    entry = _entry({"map_theme": "emerald"})
    updates = []
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_update_entry=lambda target, *, options: (
                updates.append(options),
                setattr(target, "options", options),
            )
        )
    )
    mower = SimpleNamespace(
        hass=hass, coordinator=SimpleNamespace(entry=entry, app_maps=_maps())
    )

    asyncio.run(DreameLawnMower.async_set_georeference(mower, **REFERENCE))
    assert entry.options[CONF_GEOREFERENCE] == _stored(_reference().as_dict())
    asyncio.run(DreameLawnMower.async_clear_georeference(mower))
    asyncio.run(DreameLawnMower.async_clear_georeference(mower))

    assert entry.options == {"map_theme": "emerald"}
    assert len(updates) == 2  # Clearing an absent calibration writes nothing.


def test_options_form_keeps_the_calibration_it_does_not_show() -> None:
    flow = DreameLawnMowerOptionsFlow(
        SimpleNamespace(options={CONF_GEOREFERENCE: REFERENCE})
    )

    result = asyncio.run(flow.async_step_settings({CONF_SCAN_INTERVAL: 45}))

    assert result["data"] == {CONF_SCAN_INTERVAL: 45, CONF_GEOREFERENCE: REFERENCE}


@pytest.mark.parametrize("cleared", [False, True])
def test_open_options_form_preserves_later_calibration_actions(cleared):
    entry = _entry({CONF_GEOREFERENCE: _stored()})
    flow = DreameLawnMowerOptionsFlow(entry)
    entry.options = {} if cleared else {
        CONF_GEOREFERENCE: _stored({**REFERENCE, "heading": 10})
    }
    result = asyncio.run(flow.async_step_settings({CONF_SCAN_INTERVAL: 45}))
    assert result["data"] == {CONF_SCAN_INTERVAL: 45, **entry.options}


@pytest.mark.parametrize("cleared", [False, True])
def test_options_preview_cleanup_preserves_overlapping_calibration_actions(
    monkeypatch, cleared
):
    entry = _entry({CONF_GEOREFERENCE: _stored(), CONF_MAP_RESTART_PREVIEW: True})
    flow = DreameLawnMowerOptionsFlow(entry)
    flow.hass = SimpleNamespace()

    async def remove_preview(hass, target):
        # The storage boundary suspends while an alignment action completes.
        await asyncio.sleep(0)
        target.options = {} if cleared else {
            CONF_GEOREFERENCE: _stored({**REFERENCE, "heading": 10})
        }

    monkeypatch.setattr(
        "custom_components.dreame_lawn_mower.config_flow.async_remove_restart_preview",
        remove_preview,
    )
    result = asyncio.run(flow.async_step_settings({CONF_SCAN_INTERVAL: 45}))
    assert result["data"] == {CONF_SCAN_INTERVAL: 45, **entry.options}


def test_device_tracker_platform_is_loaded() -> None:
    assert "device_tracker" in [str(platform) for platform in PLATFORMS]


def _tracker(position, options):
    options = dict(options)
    if options.get(CONF_GEOREFERENCE) == REFERENCE:
        options[CONF_GEOREFERENCE] = _stored()
    coordinator = SimpleNamespace(
        client=SimpleNamespace(
            descriptor=SimpleNamespace(unique_id="mower"),
            current_map_position=Mock(return_value=position),
        ),
        entry=SimpleNamespace(options=options),
        app_maps=_maps(),
        data=SimpleNamespace(available=True),
        last_update_success=True,
    )
    return DreameLawnMowerGpsTracker(coordinator), coordinator


@pytest.mark.parametrize("status", ["current", "known_dock"])
def test_tracker_publishes_gps_for_current_evidence(status) -> None:
    observed = datetime(2026, 10, 16, 14, 0, tzinfo=UTC)
    position = MowerPosition(-5676, -20165, None, observed.timestamp(), 0, status)
    tracker, _ = _tracker(position, {CONF_GEOREFERENCE: REFERENCE})

    assert tracker.unique_id == "mower_gps_position"
    assert tracker.translation_key == "gps_position"
    assert tracker.source_type == "gps"
    assert tracker.location_accuracy == LOCATION_ACCURACY_METERS
    assert tracker.latitude == pytest.approx(45.3174986, abs=1e-6)
    assert tracker.longitude == pytest.approx(-73.836883, abs=1e-6)
    assert tracker.extra_state_attributes == {
        "position_status": status,
        "position_observed_at": observed.isoformat(),
    }


def test_tracker_never_presents_a_last_known_pose_as_current() -> None:
    position = MowerPosition(-5676, -20165, None, 1.0, 0, "last_known")
    tracker, _ = _tracker(position, {CONF_GEOREFERENCE: REFERENCE})

    assert tracker.latitude is None and tracker.longitude is None
    assert tracker.state is None
    assert tracker.extra_state_attributes == {
        "position_status": "last_known",
        "position_observed_at": None,
    }


def test_tracker_without_calibration_or_position_has_no_coordinates() -> None:
    position = MowerPosition(-5676, -20165, None, 1.0, 0, "current")
    uncalibrated, _ = _tracker(position, {})
    assert uncalibrated.latitude is None and uncalibrated.longitude is None
    assert uncalibrated.extra_state_attributes["position_status"] == "current"

    missing, _ = _tracker(None, {CONF_GEOREFERENCE: REFERENCE})
    assert missing.latitude is None
    assert missing.extra_state_attributes["position_status"] == "unavailable"


def test_tracker_follows_coordinator_updates_and_new_calibrations() -> None:
    tracker, coordinator = _tracker(None, {})
    tracker.async_write_ha_state = Mock()
    coordinator.client.current_map_position.return_value = MowerPosition(
        1160, -20, None, 1.0, 0, "current"
    )
    coordinator.entry.options = {CONF_GEOREFERENCE: _stored()}

    tracker._handle_coordinator_update()

    assert (tracker.latitude, tracker.longitude) == (45.317421, -73.837131)
    tracker.async_write_ha_state.assert_called_once_with()


def test_live_position_needs_no_map_read_and_matches_the_camera_judge() -> None:
    client = tracked_client()
    # No map frame has been observed yet: nothing to validate against.
    assert client.current_map_position() is None

    # The coordinator's periodic details read observes the frame once.
    details = client._sync_get_vector_map_details()
    position = client.current_map_position()
    assert position.status == details["position_status"] == "current"
    assert (position.x, position.y) == (details["position_x"], details["position_y"])

    # Later telemetry moves the tracker without any further map read.
    publish(
        client, hex="moved", candidate_runtime_pose_x=50, candidate_runtime_pose_y=20
    )
    moved = client.current_map_position()
    assert (moved.x, moved.y, moved.status) == (50, 20, "current")
    details = client._sync_get_vector_map_details()
    assert (details["position_x"], details["position_y"]) == (50, 20)
    assert details["position_status"] == "current"

    # Outside the frame both readers fall back to labelled history.
    publish(
        client,
        hex="outside",
        candidate_runtime_pose_x=50_000,
        candidate_runtime_pose_y=20,
    )
    outside = client.current_map_position()
    details = client._sync_get_vector_map_details()
    assert outside.status == details["position_status"] == "last_known"


def test_docked_live_position_uses_the_station_like_the_camera() -> None:
    client = tracked_client()
    client._charger_history = _station_history()
    client._sync_get_vector_map_details()
    client._latest_snapshot = SimpleNamespace(
        available=True, activity="docked", docked=True
    )

    position = client.current_map_position()
    details = client._sync_get_vector_map_details()

    assert (position.x, position.y, position.status) == (40, 30, "known_dock")
    assert (details["position_x"], details["position_y"]) == (40, 30)
    assert details["position_status"] == "known_dock"


def test_map_switch_retires_the_frame_until_it_is_observed_again() -> None:
    client = tracked_client()
    client._sync_get_vector_map_details()
    assert client.current_map_position() is not None

    client.invalidate_runtime_map_geometry()

    assert client.current_map_position() is None


def _station_history() -> ZoneMowingHistory:
    """A station reported on the revision the native fixture map carries."""
    revision = selected_map_revision(native_maps(), 0)
    assert revision is not None
    return ZoneMowingHistory(
        chargers={"summary": ChargerPosition(40, 30, 0, 1, revision)}
    )


def _docked_client(*, verified: bool):
    """Docked since startup: no telemetry, station reported by a summary."""
    client = _client()
    client._sync_get_vector_map_batch_data = lambda: {}
    client._sync_get_current_app_map_index = lambda: 0
    client._sync_get_app_maps = lambda **kwargs: native_maps()
    client._safe_map_diagnostics = lambda **kwargs: None
    if verified:
        client.update_runtime_map_identity(
            0, expires_at=datetime.now(UTC) + timedelta(minutes=5)
        )
    else:
        client.runtime_map_identity_expires_at = None
    client._latest_snapshot = SimpleNamespace(
        available=True,
        activity="docked",
        state="charging_completed",
        charging=False,
        docked=True,
    )
    client._charger_history = _station_history()
    return client


def test_docked_since_startup_without_telemetry_sits_on_the_station() -> None:
    """Reported at home: docked since restart, no telemetry, station known."""
    client = _docked_client(verified=True)
    assert client.current_map_position() is None

    details = client._sync_get_vector_map_details()
    position = client.current_map_position()

    assert details["position_status"] == "known_dock"
    assert (details["position_x"], details["position_y"]) == (40, 30)
    assert position is not None
    assert (position.x, position.y, position.status) == (40, 30, "known_dock")
    assert position.details()["position_source"] == details["position_source"]


def test_missing_live_lease_still_uses_a_verified_station_for_all_readers() -> None:
    """A sleeping mower has static map evidence without live-session admission."""
    client = _docked_client(verified=False)

    details = client._sync_get_vector_map_details()
    position = client.current_map_position()

    assert details["position_status"] == "known_dock"
    assert (position.status if position else "unavailable") == (
        details["position_status"]
    )


def test_docked_reader_never_places_a_station_from_a_superseded_frame() -> None:
    client = _docked_client(verified=True)
    client._sync_get_vector_map_details()
    assert client.current_map_position() is not None

    # Evidence bound to another geometry retires the remembered frame.
    client._position_tracker._geometry = "f" * 64
    assert client.current_map_position() is None
    client._position_tracker._geometry = None
    client.invalidate_runtime_map_geometry()
    assert client.current_map_position() is None


def test_tracker_writes_only_when_location_status_or_availability_change(
    monkeypatch,
) -> None:
    from homeassistant.helpers.entity import Entity

    tracker, coordinator = _tracker(None, {CONF_GEOREFERENCE: REFERENCE})
    writes = []
    # Stand in for HA's state machine, keeping the tracker's own bookkeeping.
    monkeypatch.setattr(
        Entity,
        "async_write_ha_state",
        lambda self: writes.append(
            (self.latitude, self.extra_state_attributes["position_observed_at"])
        ),
    )
    reader = coordinator.client.current_map_position

    def dock(at):
        reader.return_value = MowerPosition(1160, -20, None, at, 0, "known_dock")
        tracker._handle_coordinator_update()

    dock(1000.0)
    first = writes[-1]
    for later in (1030.0, 1060.0, 1090.0):
        dock(later)
    # Docked, the station is re-asserted every poll: one write, not four.
    assert writes == [first]
    assert first[1] == datetime.fromtimestamp(1000.0, UTC).isoformat()

    reader.return_value = MowerPosition(5000, 2000, None, 1100.0, 0, "current")
    tracker._handle_coordinator_update()
    assert len(writes) == 2
    reader.return_value = MowerPosition(5000, 2000, None, 1100.0, 0, "last_known")
    tracker._handle_coordinator_update()
    assert len(writes) == 3 and writes[-1][0] is None

    coordinator.last_update_success = False
    tracker._handle_coordinator_update()
    assert len(writes) == 4
    tracker._handle_coordinator_update()
    assert len(writes) == 4


@pytest.mark.parametrize("changed", ["slot", "revision"])
def test_georeference_cannot_transfer_to_another_map_frame(changed):
    index = 1 if changed == "slot" else 0
    calibration = _stored()
    position = MowerPosition(1160, -20, None, 1.0, index, "current")
    tracker, coordinator = _tracker(position, {CONF_GEOREFERENCE: calibration})
    maps = _maps()
    maps["current_map_index"] = index
    if changed == "revision":
        maps["maps"][0].update(md5="c" * 32, info={"hash": "c" * 32})
    coordinator.app_maps = maps
    tracker._update_position()
    assert tracker.latitude is None and tracker.longitude is None


def test_each_verified_map_retains_its_own_calibration():
    entry = _entry()
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_update_entry=lambda target, *, options: setattr(
                target, "options", options
            )
        )
    )
    maps = _maps()
    first = _reference()
    second = Georeference.parse({**REFERENCE, "heading": 10})
    store_georeference(hass, entry, first, app_maps=maps, map_index=0)
    maps["current_map_index"] = 1
    store_georeference(hass, entry, second, app_maps=maps, map_index=1)
    assert entry_georeference(entry, maps, 1) == second
    maps["current_map_index"] = 0
    assert entry_georeference(entry, maps, 0) == first
    store_georeference(hass, entry, None, app_maps=maps, map_index=0)
    assert entry_georeference(entry, maps, 0) is None
    maps["current_map_index"] = 1
    assert entry_georeference(entry, maps, 1) == second


@pytest.mark.parametrize("clear", [False, True])
@pytest.mark.parametrize("changed", ["slot", "revision", "unverified", "switching"])
def test_stale_alignment_cannot_save_or_clear_a_different_map(clear, changed):
    from homeassistant.exceptions import HomeAssistantError

    entry = _entry({CONF_GEOREFERENCE: _stored()})
    original = entry.options.copy()
    update = Mock()
    maps = _maps()
    if changed == "slot":
        maps["current_map_index"] = 1
    elif changed == "revision":
        maps["maps"][0].update(md5="c" * 32, info={"hash": "c" * 32})
    elif changed == "unverified":
        maps["map_list_valid"] = False
    mower = SimpleNamespace(
        hass=SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=update)),
        coordinator=SimpleNamespace(entry=entry, app_maps=maps),
    )
    if changed == "switching":
        mower.coordinator._map_switch_inventory_pending_event = 1
    fields = {"map_index": 0, "map_revision": HASH}
    action = (
        DreameLawnMower.async_clear_georeference
        if clear
        else (DreameLawnMower.async_set_georeference)
    )
    with pytest.raises(HomeAssistantError, match="map|Map"):
        asyncio.run(action(mower, **({} if clear else REFERENCE), **fields))
    assert entry.options == original
    update.assert_not_called()
