"""Native names improve labels without changing selected-map membership."""

from copy import deepcopy

import pytest

from custom_components.dreame_lawn_mower.control_options import current_zone_entries


def _maps():
    return {
        "map_list_valid": True,
        "current_map_index": 0,
        "maps": [
            {
                "idx": idx,
                "created": True,
                "available": True,
                "hash_match": True,
                "summary": {
                    "zones": [
                        {"zone_id": 1, "name": "Courtyard" if idx == 0 else "Orchard"},
                        {"zone_id": 3, "name": None},
                    ]
                },
            }
            for idx in (0, 1)
        ],
    }


def _vector():
    return {
        "maps": [
            {
                "map_index": idx,
                "zones": [
                    {"zone_id": 1, "name": "Stale cloud name"},
                    {"zone_id": 3, "name": "Removed name"},
                ],
            }
            for idx in (0, 1)
        ]
    }


def test_native_labels_follow_selected_map_and_clear_removed_name():
    entries = current_zone_entries(None, _maps(), _vector(), selected_map_index=1)
    assert [(entry["map_index"], entry["label"]) for entry in entries] == [
        (1, "Orchard (#1)"),
        (1, "Zone #3"),
    ]


@pytest.mark.parametrize(
    "invalid",
    [
        "inventory",
        "hash",
        "uncreated",
        "unavailable",
        "duplicate_map",
        "duplicate_zone",
        "wrong_map",
    ],
)
def test_unverified_or_ambiguous_native_labels_keep_existing_fallback(invalid):
    maps = _maps()
    entry = maps["maps"][0]
    if invalid == "inventory":
        maps["map_list_valid"] = False
    elif invalid == "hash":
        entry["hash_match"] = False
    elif invalid == "uncreated":
        entry["created"] = False
    elif invalid == "unavailable":
        entry["available"] = False
    elif invalid == "duplicate_map":
        maps["maps"].append(deepcopy(entry))
    elif invalid == "duplicate_zone":
        entry["summary"]["zones"].append({"zone_id": 1, "name": "Conflict"})
    elif invalid == "wrong_map":
        entry["idx"] = 2
    entries = current_zone_entries(None, maps, _vector())
    assert [entry["label"] for entry in entries] == [
        "Stale cloud name (#1)",
        "Removed name (#3)",
    ]


def test_zone_areas_come_from_the_same_verified_native_map():
    maps = _maps()
    maps["maps"][1]["summary"]["zones"][0]["area_m2"] = 112.5
    maps["maps"][1]["summary"]["zones"][1]["area_m2"] = 64
    entries = current_zone_entries(None, maps, _vector(), selected_map_index=1)
    assert [(entry["area_id"], entry["area_m2"]) for entry in entries] == [
        (1, 112.5),
        (3, 64.0),
    ]
    maps["maps"][1]["hash_match"] = False
    entries = current_zone_entries(None, maps, _vector(), selected_map_index=1)
    assert [entry["area_m2"] for entry in entries] == [None, None]


@pytest.mark.parametrize("area", [None, -1, float("nan"), True, "40"])
def test_invalid_zone_area_is_left_unknown(area):
    maps = _maps()
    maps["maps"][0]["summary"]["zones"][0]["area_m2"] = area
    entries = current_zone_entries(None, maps, _vector(), selected_map_index=0)
    assert entries[0]["area_m2"] is None
