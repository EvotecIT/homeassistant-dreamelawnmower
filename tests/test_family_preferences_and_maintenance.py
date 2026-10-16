"""App-derived family differences preserve wire values and maintenance slots."""

from types import SimpleNamespace

import pytest

from custom_components.dreame_lawn_mower.button import (
    DreameLawnMowerResetMaintenanceButton,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    app_read_transport,
    device_settings_read_plan,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.maintenance import (
    EDGE_TRIMMER_ITEM,
    maintenance_items_for_model,
)
from custom_components.dreame_lawn_mower.preference_select import (
    PREFERENCE_SELECTS,
    DreameLawnMowerPreferenceSelect,
)
from dreame_lawn_mower_client import (
    apply_mowing_preference_changes,
    decode_mowing_preference_payload,
    encode_mowing_preference_payload,
    maintenance_item_status,
    maintenance_status_from_cms,
    reset_cms_counter,
)

run_app_read = app_read_transport.run_app_read
read_maintenance = device_settings_read_plan.read_maintenance


def test_optional_edge_reset_button_requires_confirmed_installation():
    entity = object.__new__(DreameLawnMowerResetMaintenanceButton)
    entity._item = EDGE_TRIMMER_ITEM
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(available=True), maintenance_status=None
    )
    assert entity.available is False
    entity.coordinator.maintenance_status = maintenance_status_from_cms(
        [1, -1, 2, 3],
        model="dreame.mower.q2501c",
        edge_trimmer_mode=2,
    )
    assert entity.available is True


def _raw_preference(mode=2):
    return [8, 0, 0, 0, 40, 0, 0, 1, 0, 1, 0, 1, 1, 15, 20, 7, 1, 2, mode, 0, 3]


@pytest.mark.parametrize("mode", [0, 1, 2, 7])
def test_unrelated_preference_write_preserves_numeric_attachment_mode(mode):
    raw = _raw_preference(mode)
    updated, fields = apply_mowing_preference_changes(
        decode_mowing_preference_payload(raw),
        {"mowing_height_cm": 4.5},
        model="dreame.mower.q2501c",
    )
    assert fields == ["mowing_height_cm"]
    encoded = encode_mowing_preference_payload(updated)
    assert encoded[18] == mode
    assert encoded[:4] == raw[:4]
    assert encoded[5:] == raw[5:]


@pytest.mark.parametrize(("enabled", "wire_mode"), [(False, 1), (True, 2)])
def test_a3_attachment_boolean_control_maps_to_closed_and_edge_modes(
    enabled, wire_mode
):
    updated, _ = apply_mowing_preference_changes(
        decode_mowing_preference_payload(_raw_preference(2 if not enabled else 1)),
        {"edge_cutting_attachment": enabled},
        model="dreame.mower.q2501a",
    )
    assert encode_mowing_preference_payload(updated)[18] == wire_mode


@pytest.mark.parametrize("mode", [0, 7])
def test_absent_attachment_and_unsupported_modes_cannot_be_enabled(mode):
    with pytest.raises(ValueError, match="installed attachment"):
        apply_mowing_preference_changes(
            decode_mowing_preference_payload(_raw_preference(mode)),
            {"edge_cutting_attachment": True},
            model="dreame.mower.q2501a",
        )
    with pytest.raises(ValueError, match="not supported"):
        apply_mowing_preference_changes(
            decode_mowing_preference_payload(_raw_preference(1)),
            {"edge_cutting_attachment": 2},
            model="dreame.mower.g2408",
        )


@pytest.mark.parametrize("model", ["dreame.mower.q2501c", "dreame.mower.g2541g"])
def test_awd_rush_mode_is_decoded_encoded_and_offered_by_the_selector(model):
    preference = decode_mowing_preference_payload(_raw_preference())
    updated, _ = apply_mowing_preference_changes(
        preference, {"efficient_mode": 3}, model=model
    )
    assert updated["efficient_mode_name"] == "rush"
    assert encode_mowing_preference_payload(updated)[3] == 3
    entity = object.__new__(DreameLawnMowerPreferenceSelect)
    entity._descriptor = SimpleNamespace(model=model)
    entity._preference_description = next(
        item for item in PREFERENCE_SELECTS if item.key == "efficient_mode"
    )
    assert entity.options == ["Standard", "Efficient", "Rush"]


@pytest.mark.parametrize(
    ("model", "mode"),
    [("dreame.mower.g2408", 3), ("dreame.mower.future", 3), ("dreame.mower.q2501a", 2)],
)
def test_efficiency_mode_gaps_and_unverified_models_cannot_claim_rush(model, mode):
    with pytest.raises(ValueError, match="not supported"):
        apply_mowing_preference_changes(
            decode_mowing_preference_payload(_raw_preference()),
            {"efficient_mode": mode},
            model=model,
        )


@pytest.mark.parametrize("model", ["dreame.mower.q2501c", "dreame.mower.g2541g"])
def test_awd_maintenance_keeps_original_slots_and_uses_fifty_hour_interval(model):
    status = maintenance_status_from_cms([100, -1, 3000, 200, 99], model=model)
    assert [item["key"] for item in status["items"]] == ["blade", "robot"]
    robot = maintenance_item_status(status, "robot")
    assert robot["index"] == 2
    assert robot["total_hours"] == 50
    assert robot["due"] is True
    assert status["extra_values"] == [200, 99]
    assert [item.key for item in maintenance_items_for_model(model)] == [
        "blade",
        "robot",
    ]
    with pytest.raises(ValueError, match="not supported"):
        reset_cms_counter(status["raw_cms"], "brush", model=model)


def test_unknown_maintenance_slots_and_attachment_installation_remain_unproven():
    status = maintenance_status_from_cms(
        [100, -1, 100, 600, 9], model="dreame.mower.q2501b"
    )
    assert status["candidate_counters"][0] == {
        "index": 3,
        "key": "edge_trimmer",
        "used_minutes": 600,
        "total_minutes": 6000,
        "installation_confirmed": False,
        "reason": "requires_edge_cutting_attachment_readback",
    }
    assert "edge_trimmer" not in {item["key"] for item in status["items"]}


@pytest.mark.parametrize(
    "model",
    [
        "mova.mower.g2420a",
        "mova.mower.g2420b",
        "mova.mower.g2566a",
        "dreame.mower.g2420z",
        "dreame.mower.g2566b",
    ],
)
def test_two_eye_profiles_omit_brush_without_changing_robot_slot_or_interval(model):
    items = maintenance_items_for_model(model, include_optional=True)
    assert [(item.key, item.index, item.total_minutes) for item in items] == [
        ("blade", 0, 6000),
        ("robot", 2, 3600),
    ]
    status = maintenance_status_from_cms([100, 200, 1800, 77], model=model)
    assert maintenance_item_status(status, "brush") is None
    assert maintenance_item_status(status, "robot")["remaining_percent"] == 50
    with pytest.raises(ValueError, match="not supported"):
        reset_cms_counter(status["raw_cms"], "brush", model=model)
    assert reset_cms_counter(status["raw_cms"], "robot", model=model) == [
        100,
        200,
        0,
        77,
    ]


def test_negative_counter_is_unavailable_and_cannot_be_reset_as_fresh():
    status = maintenance_status_from_cms([0, -1, 0])
    assert maintenance_item_status(status, "brush") is None
    with pytest.raises(ValueError, match="unavailable"):
        reset_cms_counter([0, -1, 0, 42], "brush")
    assert reset_cms_counter([5, -1, 0, 42], "blade") == [0, -1, 0, 42]


@pytest.mark.parametrize("mode", [1, 2])
def test_fresh_attachment_read_exposes_edge_counter_and_preserves_other_slots(
    mode,
):
    calls = []

    def read(action, **kwargs):
        calls.append(action)
        assert action["m"] == "g"
        command = action["t"]
        data = {
            "CMS": {"value": [100, -1, 100, 600, 99]},
            "MAPL": [[0, 1, 1, 1, 0]],
            "PREI": {"type": 0, "ver": [[0, 8]]},
            "PRE": _raw_preference(mode),
        }[command]
        return {"m": "r", "r": 0, "d": data}

    status = run_app_read(read_maintenance(model="dreame.mower.q2501c"), read)
    edge = maintenance_item_status(status, "edge_trimmer")
    assert edge["index"] == 3
    assert edge["total_minutes"] == 6000
    assert edge["remaining_percent"] == 90
    assert status["edge_trimmer_mode"] == mode
    assert not status["candidate_counters"]
    assert [action["t"] for action in calls] == ["CMS", "MAPL", "PREI", "PRE"]
    assert reset_cms_counter(
        status["raw_cms"],
        "edge_trimmer",
        model=status["model"],
        edge_trimmer_mode=status["edge_trimmer_mode"],
    ) == [100, -1, 100, 0, 99]


@pytest.mark.parametrize("problem", ["absent", "stale_revision", "wrong_map"])
def test_unproven_attachment_context_cannot_reset_edge_counter(problem):
    def read(action, **kwargs):
        raw = _raw_preference(0 if problem == "absent" else 2)
        if problem == "stale_revision":
            raw[0] = 7
        if problem == "wrong_map":
            raw[1] = 1
        data = {
            "CMS": {"value": [100, -1, 100, 600]},
            "MAPL": [[0, 1, 1, 1, 0]],
            "PREI": {"type": 0, "ver": [[0, 8]]},
            "PRE": raw,
        }[action["t"]]
        return {"m": "r", "r": 0, "d": data}

    status = run_app_read(read_maintenance(model="dreame.mower.q2501c"), read)
    assert status["available"] is True
    assert maintenance_item_status(status, "edge_trimmer") is None
    with pytest.raises(ValueError, match="installation is not confirmed"):
        reset_cms_counter(
            status["raw_cms"],
            "edge_trimmer",
            model=status["model"],
            edge_trimmer_mode=status.get("edge_trimmer_mode"),
        )
