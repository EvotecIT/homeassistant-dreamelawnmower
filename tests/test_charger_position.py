"""The charging station comes from task summaries and is drawn on the map."""

from __future__ import annotations

import asyncio
import json
from io import BytesIO

from PIL import Image

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_projection import (  # noqa: E501
    vector_map_projection,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.vector_map import (
    parse_batch_vector_map,
    render_vector_map_png,
    with_charger,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.zone_mowing_history import (  # noqa: E501
    ChargerPosition,
    ZoneMowingHistory,
    async_get_zone_mowing_records,
    charger_position,
)

from .test_session_photos import _client, _Cloud, _record
from .test_vector_map import _batch_payload

HASH = "a" * 32
OTHER_HASH = "b" * 32
MON = "ali_dreame/2026/10/14/IQ000001/-1234_171000000.0529.json"
TUE = "ali_dreame/2026/10/15/IQ000001/-1234_120000000.0529.json"


def test_summary_dock_uses_the_scale_of_drawable_geometry() -> None:
    # Values from a LiDAX Ultra 1000 summary: (117, -1) at heading 30.
    position = charger_position(
        {"dock": [117, -1, 30, 0, -17, -8], "end": 1792022742, "md5": HASH}
    )

    assert position == ChargerPosition(1170, -10, 30, 1792022742, HASH)
    assert charger_position({"dock": [1.5, 2]}) is None
    assert charger_position({"dock": [True, 2]}) is None
    assert charger_position({"dock": [5]}) is None
    assert charger_position({}) is None
    assert charger_position(None) is None


def test_history_keeps_the_station_of_each_summary() -> None:
    cloud = _Cloud(
        {
            MON: json.dumps(
                {"mode": 107, "dock": [10, 20, 0], "end": 100, "md5": OTHER_HASH}
            ).encode(),
            TUE: json.dumps(
                {"mode": 107, "dock": [11, 21, 0], "end": 200, "md5": HASH}
            ).encode(),
        },
        [_record(107, MON, 1000), _record(107, TUE, 2000)],
    )

    history = asyncio.run(async_get_zone_mowing_records(_client(cloud)))

    assert set(history.chargers) == {MON, TUE}
    again = asyncio.run(async_get_zone_mowing_records(_client(cloud), known=history))
    assert again.chargers == history.chargers


def test_latest_station_prefers_the_current_map_revision() -> None:
    older = ChargerPosition(100, 200, 0, 100, HASH)
    newer = ChargerPosition(110, 210, 0, 200, OTHER_HASH)
    history = ZoneMowingHistory(chargers={MON: older, TUE: newer})

    assert history.latest_charger(HASH) == older
    assert history.latest_charger(None) == newer
    assert history.latest_charger("c" * 32) == newer
    assert ZoneMowingHistory().latest_charger(HASH) is None


def test_station_is_drawn_inside_the_frame_only() -> None:
    vector_map = parse_batch_vector_map(_batch_payload())
    boundary = vector_map.boundary

    inside = with_charger(vector_map, ChargerPosition(119, 50, 0, 1))
    assert inside.charger == (119, 50)
    # The bounds identify retained positions; attaching a station keeps them.
    assert inside.boundary == boundary
    # Slightly past the edge is clamped onto it.
    assert with_charger(vector_map, ChargerPosition(130, 50, 0, 1)).charger == (120, 50)
    # Far away belongs to another frame.
    assert with_charger(vector_map, ChargerPosition(99999, 50, 0, 1)).charger is None
    assert with_charger(vector_map, None) is vector_map

    plain = Image.open(BytesIO(render_vector_map_png(vector_map))).convert("RGBA")
    marked = Image.open(BytesIO(render_vector_map_png(inside))).convert("RGBA")
    projection = vector_map_projection(
        boundary.x1, boundary.y1, boundary.x2, boundary.y2
    )
    px, py = (round(value) for value in projection.point(119, 50))
    assert marked.getpixel((px, py)) != plain.getpixel((px, py))


def test_docked_mower_is_drawn_on_the_reported_station() -> None:
    from types import SimpleNamespace

    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.position_tracking import (  # noqa: E501
        MowerPosition,
        position_at_charger,
    )

    last_seen = MowerPosition(10, 20, 90.0, 100.0, 0, "last_known")
    docked = SimpleNamespace(available=True, activity="docked", docked=True)
    mowing = SimpleNamespace(available=True, activity="mowing", docked=False)

    at_station = position_at_charger(last_seen, docked, (119, 50), 0, 500.0)
    assert (at_station.x, at_station.y, at_station.status) == (119, 50, "known_dock")
    assert at_station.observed_at == 500.0
    assert at_station.details()["position_source"] == "reported_charging_station"
    assert position_at_charger(last_seen, mowing, (119, 50), 0, 500.0) is last_seen
    assert position_at_charger(last_seen, docked, None, 0, 500.0) is last_seen


def test_interactive_overlay_places_a_docked_mower_on_the_station() -> None:
    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_visuals import (  # noqa: E501
        map_render_style,
    )
    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.mowing_map import (  # noqa: E501
        build_mowing_map_scene,
        mowing_map_overlay,
    )

    vector_map = with_charger(
        parse_batch_vector_map(_batch_payload()), ChargerPosition(119, 50, 0, 1)
    )
    scene = build_mowing_map_scene(vector_map, style=map_render_style())
    assert scene.charger == (119, 50)

    overlay = mowing_map_overlay(
        scene, None, map_index=scene.map_index, active=False, docked=True
    )
    px, py = scene.projection.point(119, 50)
    assert overlay["position"]["x"] == px
    assert overlay["position"]["y"] == py
    assert overlay["position_status"] == "known_dock"
    assert overlay["position_source"] == "reported_charging_station"

    away = mowing_map_overlay(scene, None, map_index=scene.map_index, active=False)
    assert away["position"] is None
