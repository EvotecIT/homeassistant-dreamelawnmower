"""Whole-zone rotation chosen from the least-recently-mowed overview."""

from __future__ import annotations

from datetime import date

from custom_components.dreame_lawn_mower.zone_rotation import due_zone_ids

TODAY = date(2026, 10, 26)


def zone(zone_id, completed=None, area=50.0):
    return {"zone_id": zone_id, "last_completed_at": completed, "area_m2": area}


def test_unknown_and_rested_zones_are_due_in_overview_order():
    overview = [
        zone(3),
        zone(1, "2026-10-23T15:00:00+00:00"),
        zone(2, "2026-10-25T15:00:00+00:00"),
    ]
    assert due_zone_ids(overview, rest_days=1, max_area=0, today=TODAY) == [3, 1]
    assert due_zone_ids(overview, rest_days=0, max_area=0, today=TODAY) == [3, 1, 2]
    assert due_zone_ids(overview, rest_days=3, max_area=0, today=TODAY) == [3]


def test_zone_mowed_today_is_never_due_again_the_same_day():
    overview = [zone(1, "2026-10-26T14:00:00+00:00")]
    assert due_zone_ids(overview, rest_days=0, max_area=0, today=TODAY) == []


def test_area_limit_keeps_whole_zones_that_fit_and_skips_larger_ones():
    overview = [zone(1, area=200), zone(2, area=150), zone(3, area=90), zone(4)]
    assert due_zone_ids(overview, rest_days=1, max_area=300, today=TODAY) == [1, 3]


def test_first_due_zone_is_kept_even_when_larger_than_the_limit():
    overview = [zone(1, area=400), zone(2, area=20)]
    assert due_zone_ids(overview, rest_days=1, max_area=300, today=TODAY) == [1]


def test_unknown_area_cannot_be_added_under_a_limit():
    overview = [zone(1, area=100), zone(2, area=None), zone(3, area=True)]
    assert due_zone_ids(overview, rest_days=1, max_area=300, today=TODAY) == [1]
    assert due_zone_ids(overview, rest_days=1, max_area=0, today=TODAY) == [1, 2, 3]
    unknown_first = [zone(1, area=None), zone(2, area=300)]
    assert due_zone_ids(unknown_first, rest_days=1, max_area=300, today=TODAY) == [1]


def test_malformed_entries_are_ignored_and_bad_dates_count_as_unknown():
    overview = [
        {"zone_id": "1", "last_completed_at": None, "area_m2": 10},
        zone(2, "not a date"),
        zone(3, "2026-10-20T10:00:00"),
    ]
    assert due_zone_ids(overview, rest_days=1, max_area=0, today=TODAY) == [2, 3]


def test_a_zone_can_rest_less_than_the_default():
    from custom_components.dreame_lawn_mower.zone_rotation import rotation_zones

    yesterday = "2026-10-25T15:00:00+00:00"
    overview = [zone(1, yesterday), zone(6, yesterday)]
    assert due_zone_ids(
        overview, rest_days=1, max_area=0, today=TODAY, zone_rest_days_by_id={6: 0}
    ) == [6]
    table = rotation_zones(overview, rest_days=1, overrides={6: 0})
    assert [
        (z["zone_id"], z["rest_days"], z["custom_rest_days"], z["due_on"])
        for z in table
    ] == [(1, 1, False, "2026-10-27"), (6, 0, True, "2026-10-26")]


def test_most_overdue_first_then_the_shorter_rest_on_the_same_due_date():
    overview = [
        zone(1, "2026-10-24T15:00:00+00:00", area=200),  # every other day: due today
        zone(6, "2026-10-25T15:00:00+00:00", area=150),  # daily: due today
        zone(3, "2026-10-23T15:00:00+00:00", area=100),  # every other day: overdue
    ]
    assert due_zone_ids(
        overview, rest_days=1, max_area=0, today=TODAY, zone_rest_days_by_id={6: 0}
    ) == [3, 6, 1]
    # Under an area limit the overdue zone and the daily zone fit first.
    assert due_zone_ids(
        overview, rest_days=1, max_area=260, today=TODAY, zone_rest_days_by_id={6: 0}
    ) == [3, 6]


def test_one_rest_value_for_every_zone_keeps_the_overview_order():
    overview = [
        zone(4),
        zone(2, "2026-10-20T08:00:00+00:00"),
        zone(5, "2026-10-20T19:00:00+00:00"),
        zone(1, "2026-10-21T10:00:00+00:00"),
    ]
    assert due_zone_ids(overview, rest_days=1, max_area=0, today=TODAY) == [4, 2, 5, 1]
    assert due_zone_ids(
        overview, rest_days=1, max_area=0, today=TODAY, zone_rest_days_by_id={}
    ) == [4, 2, 5, 1]
