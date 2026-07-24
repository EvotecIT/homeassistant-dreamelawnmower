"""Contract checks for the shared mower-map visual language."""

from __future__ import annotations

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_visuals import (
    map_font,
    map_render_style,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.vector_map import (
    DreameLawnMowerVectorBoundary,
    DreameLawnMowerVectorMap,
    DreameLawnMowerVectorZone,
    render_vector_map_png,
)


def test_bundled_map_font_supports_unicode_labels() -> None:
    font = map_font(20)

    bounds = font.getbbox("Ogród zażółć gęślą jaźń")

    assert bounds[2] > bounds[0]
    assert bounds[3] > bounds[1]


def test_map_theme_changes_the_complete_render_without_geometry_changes() -> None:
    vector_map = DreameLawnMowerVectorMap(
        boundary=DreameLawnMowerVectorBoundary(0, 0, 100, 100),
        zones=(
            DreameLawnMowerVectorZone(
                zone_id=1,
                name="Ogród",
                points=((0, 0), (100, 0), (100, 100), (0, 100)),
            ),
        ),
    )

    emerald = render_vector_map_png(vector_map, style=map_render_style("emerald"))
    midnight = render_vector_map_png(vector_map, style=map_render_style("midnight"))

    assert emerald is not None
    assert midnight is not None
    assert emerald != midnight
