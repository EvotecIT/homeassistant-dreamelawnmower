"""Contract checks for the shared mower-map visual language."""

from __future__ import annotations

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_visuals import (
    load_map_marker,
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


def test_custom_map_marker_is_limited_to_config_www(tmp_path) -> None:
    www = tmp_path / "www"
    marker = www / "mower" / "marker.png"
    marker.parent.mkdir(parents=True)
    marker.write_bytes(b"safe-marker")
    unsupported = marker.with_suffix(".svg")
    unsupported.write_text("<svg />", encoding="utf-8")
    oversized = marker.with_name("oversized.png")
    oversized.write_bytes(b"x" * ((1024 * 1024) + 1))

    assert load_map_marker(www, "/local/mower/marker.png") == b"safe-marker"
    assert load_map_marker(www, "../secret.png") is None
    assert load_map_marker(www, "mower/marker.svg") is None
    assert load_map_marker(www, "mower/oversized.png") is None
