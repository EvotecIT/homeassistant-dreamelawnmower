"""Contract checks for the shared mower-map visual language."""

from __future__ import annotations

from io import BytesIO

from PIL import Image

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_visuals import (
    load_map_marker,
    map_font,
    map_render_style,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.vector_map import (
    DreameLawnMowerVectorBoundary,
    DreameLawnMowerVectorMap,
    DreameLawnMowerVectorMowPath,
    DreameLawnMowerVectorZone,
    render_vector_map_png,
)
from custom_components.dreame_lawn_mower.image import png_bytes_to_jpeg
from dreame_lawn_mower_client import render_app_map_payload_png


def _blue_layer_center(image_bytes: bytes) -> tuple[float, float]:
    """Return the normalized center of the blue spot-area layer."""
    with Image.open(BytesIO(image_bytes)) as image:
        rgb = image.convert("RGB")
        pixels = rgb.load()
        blue_pixels = [
            (x, y)
            for y in range(rgb.height)
            for x in range(rgb.width)
            for red, green, blue in (pixels[x, y],)
            if blue > red + 20 and blue > green + 20
        ]

    assert blue_pixels
    min_x = min(point[0] for point in blue_pixels)
    max_x = max(point[0] for point in blue_pixels)
    min_y = min(point[1] for point in blue_pixels)
    max_y = max(point[1] for point in blue_pixels)
    return (
        ((min_x + max_x) / 2) / (rgb.width - 1),
        ((min_y + max_y) / 2) / (rgb.height - 1),
    )


def test_bundled_map_font_supports_unicode_labels() -> None:
    font = map_font(20)

    bounds = font.getbbox("Ogród zażółć gęślą jaźń")

    assert bounds[2] > bounds[0]
    assert bounds[3] > bounds[1]


def test_app_and_vector_maps_follow_the_dreame_app_orientation() -> None:
    boundary = ((0, 0), (1000, 0), (1000, 1000), (0, 1000))
    source_upper_right_spot = (
        (750, 600),
        (900, 600),
        (900, 900),
        (750, 900),
    )
    app_png, _, _ = render_app_map_payload_png(
        {
            "map": [{"data": boundary}],
            "spot": [{"data": source_upper_right_spot}],
        }
    )
    vector_png = render_vector_map_png(
        DreameLawnMowerVectorMap(
            boundary=DreameLawnMowerVectorBoundary(0, 0, 1000, 1000),
            zones=(
                DreameLawnMowerVectorZone(zone_id=1, points=boundary),
            ),
            spot_areas=(
                DreameLawnMowerVectorZone(
                    zone_id=2,
                    points=source_upper_right_spot,
                ),
            ),
        )
    )

    assert vector_png is not None
    app_center = _blue_layer_center(app_png)
    vector_center = _blue_layer_center(vector_png)

    # Dreame's app presents this asymmetric source landmark in the bottom-left.
    # Keep that external reference explicit so matching two wrong renderers cannot pass.
    assert app_center[0] < 0.5
    assert app_center[1] > 0.5
    assert vector_center[0] < 0.5
    assert vector_center[1] > 0.5


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


def test_high_contrast_theme_preserves_accessibility_scale_defaults() -> None:
    style = map_render_style("high_contrast")

    assert style.stroke_scale == 1.35
    assert style.marker_scale == 1.2


def test_user_scales_compose_with_theme_accessibility_defaults() -> None:
    style = map_render_style(
        "high_contrast",
        stroke_scale=2.0,
        marker_scale=2.0,
    )

    assert style.stroke_scale == 2.7
    assert style.marker_scale == 2.4


def test_vector_layer_styles_offer_clean_and_diagnostic_presentations() -> None:
    vector_map = DreameLawnMowerVectorMap(
        boundary=DreameLawnMowerVectorBoundary(0, 0, 100, 100),
        zones=(
            DreameLawnMowerVectorZone(
                zone_id=1,
                name="Garden",
                points=((0, 0), (100, 0), (100, 100), (0, 100)),
            ),
        ),
        spot_areas=(
            DreameLawnMowerVectorZone(
                zone_id=9,
                points=((20, 20), (80, 20), (80, 80), (20, 80)),
            ),
        ),
        mow_paths=(
            DreameLawnMowerVectorMowPath(
                zone_id=1,
                segments=(((10, 50), (90, 50)),),
            ),
        ),
    )

    clean = render_vector_map_png(
        vector_map,
        style=map_render_style(
            spot_area_style="hidden",
            mowing_path_style="subtle",
        ),
    )
    outlined = render_vector_map_png(
        vector_map,
        style=map_render_style(
            spot_area_style="outline",
            mowing_path_style="hidden",
        ),
    )
    diagnostic = render_vector_map_png(
        vector_map,
        style=map_render_style(
            spot_area_style="filled",
            mowing_path_style="detailed",
        ),
    )

    assert clean is not None
    assert outlined is not None
    assert diagnostic is not None
    assert len({clean, outlined, diagnostic}) == 3


def test_invalid_vector_layer_styles_use_backward_compatible_defaults() -> None:
    style = map_render_style(
        spot_area_style="unsupported",
        mowing_path_style="unsupported",
    )

    assert style.spot_area_style == "filled"
    assert style.mowing_path_style == "detailed"


def test_subtle_mowing_path_remains_subtle_after_camera_jpeg_conversion() -> None:
    vector_map = DreameLawnMowerVectorMap(
        boundary=DreameLawnMowerVectorBoundary(0, 0, 100, 100),
        zones=(
            DreameLawnMowerVectorZone(
                zone_id=1,
                points=((0, 0), (100, 0), (100, 100), (0, 100)),
            ),
        ),
        mow_paths=(
            DreameLawnMowerVectorMowPath(
                zone_id=1,
                segments=(((10, 50), (90, 50)),),
            ),
        ),
    )

    rendered = {
        name: render_vector_map_png(
            vector_map,
            style=map_render_style(mowing_path_style=name),
        )
        for name in ("hidden", "subtle", "detailed")
    }
    assert all(image is not None for image in rendered.values())

    pixels: dict[str, tuple[int, int, int]] = {}
    for name, png in rendered.items():
        assert png is not None
        with Image.open(BytesIO(png_bytes_to_jpeg(png))) as image:
            pixels[name] = image.getpixel((image.width // 2, image.height // 2))

    hidden = pixels["hidden"]
    subtle_distance = sum(
        abs(channel - hidden[index])
        for index, channel in enumerate(pixels["subtle"])
    )
    detailed_distance = sum(
        abs(channel - hidden[index])
        for index, channel in enumerate(pixels["detailed"])
    )

    assert subtle_distance > 0
    assert detailed_distance > subtle_distance


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
