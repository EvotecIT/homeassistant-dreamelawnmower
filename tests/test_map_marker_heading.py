"""Custom image markers follow verified mower yaw through the map projection."""

import math
from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.legacy_map_visuals import (  # noqa: E501
    _legacy_renderer,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_projection import (  # noqa: E501
    vector_map_projection,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_types import (
    MapImageDimensions,
    Point,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_visuals import (
    draw_position_marker,
    map_render_style,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.vector_map import (
    DreameLawnMowerVectorBoundary,
    DreameLawnMowerVectorMap,
    render_vector_map_png,
)
from tests.test_vector_map import _batch_payload, _client, _runtime_track_packet


def marker_image():
    """A red nose and blue tail make the direction visible in actual PNG output."""
    marker = Image.new("RGBA", (24, 32))
    draw = ImageDraw.Draw(marker)
    draw.rectangle((5, 3, 18, 12), fill="red")
    draw.rectangle((5, 19, 18, 28), fill="blue")
    buffer = BytesIO()
    marker.save(buffer, format="PNG")
    return buffer.getvalue()


def nose_direction(png, position=None):
    with Image.open(BytesIO(png)) as source:
        image = source.convert("RGB")
    x, y = position or (image.width // 2, image.height // 2)
    crop = image.crop((x - 45, y - 45, x + 45, y + 45))
    red, blue = [], []
    for y in range(crop.height):
        for x in range(crop.width):
            r, g, b = crop.getpixel((x, y))
            if r > 245 and g < 10 and b < 10:
                red.append((x, y))
            if b > 245 and r < 10 and g < 10:
                blue.append((x, y))
    assert red and blue
    return tuple(
        sum(point[axis] for point in red) / len(red)
        - sum(point[axis] for point in blue) / len(blue)
        for axis in (0, 1)
    )


@pytest.mark.parametrize(
    "heading,rotation,expected",
    [
        (270, 0, (0, -1)),
        (180, 0, (1, 0)),
        (90, 0, (0, 1)),
        (0, 0, (-1, 0)),
        (270, 90, (1, 0)),
    ],
)
def test_custom_marker_rotates_with_native_heading_and_map(heading, rotation, expected):
    png = render_vector_map_png(
        DreameLawnMowerVectorMap(
            boundary=DreameLawnMowerVectorBoundary(0, 0, 1000, 1000)
        ),
        runtime_position=(500, 500),
        runtime_heading=heading,
        style=map_render_style(marker_image=marker_image(), rotation=rotation),
    )
    dx, dy = nose_direction(png)
    for actual, wanted in zip((dx, dy), expected, strict=True):
        assert abs(actual) < 1 if wanted == 0 else actual * wanted > 5


@pytest.mark.parametrize("heading,status", [(None, "current"), (90, "last_known")])
def test_unknown_and_last_known_heading_keep_original_image(heading, status):
    png = render_vector_map_png(
        DreameLawnMowerVectorMap(
            boundary=DreameLawnMowerVectorBoundary(0, 0, 1000, 1000)
        ),
        runtime_position=(500, 500),
        runtime_heading=heading,
        position_status=status,
        style=map_render_style(marker_image=marker_image()),
    )
    dx, dy = nose_direction(png)
    assert abs(dx) < 1 and dy < -5


def test_client_map_view_passes_validated_heading_to_custom_marker():
    client = _client()
    client._latest_snapshot = SimpleNamespace(available=True, activity="mowing")
    client._sync_get_vector_map_batch_data = lambda: _batch_payload()
    client._safe_map_diagnostics = lambda **kwargs: None
    client.update_runtime_live_tracking(
        _runtime_track_packet(
            candidate_runtime_track_segments=(),
            candidate_runtime_pose_x=50,
            candidate_runtime_pose_y=40,
            candidate_runtime_heading_deg=90,
            received_at=datetime.now(UTC).isoformat(),
        ),
        active=True,
        map_index=0,
    )
    view = client._sync_refresh_vector_map_view(
        current_map_index=0,
        style=map_render_style(marker_image=marker_image()),
    )
    with Image.open(BytesIO(view.image_png)) as image:
        position = (
            round(40 + (image.width - 80) * 70 / 130),
            round(40 + (image.height - 80) * 50 / 120),
        )
    dx, dy = nose_direction(view.image_png, position)
    assert abs(dx) < 1 and dy > 5


@pytest.mark.parametrize("heading", [0, 30, 45, 90, 135, 180, 200, 270, 315, None])
def test_legacy_custom_marker_matches_vector_heading(heading):
    style = map_render_style(marker_image=marker_image())
    renderer = _legacy_renderer(style=style, label_scale=1)
    legacy = renderer.render_mower(
        Point(100, 99, heading), 0, (200, 200),
        MapImageDimensions(0, 0, 200, 200, 1), 28, 0, 1,
    )
    vector = Image.new("RGBA", (200, 200))
    projected = vector_map_projection(0, 0, 200, 200)
    draw_position_marker(
        vector, ImageDraw.Draw(vector), (100, 100), style,
        heading=projected.heading(heading) if heading is not None else None,
    )
    directions = []
    for image in (legacy, vector):
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        dx, dy = nose_direction(buffer.getvalue())
        length = math.hypot(dx, dy)
        directions.append((dx / length, dy / length))
    assert directions[0] == pytest.approx(directions[1], abs=0.04)
    clockwise = projected.heading(heading) if heading is not None else 0
    expected = (math.sin(math.radians(clockwise)), -math.cos(math.radians(clockwise)))
    assert directions[0] == pytest.approx(expected, abs=0.04)


@pytest.mark.parametrize("renderer_kind", ["legacy", "vector"])
@pytest.mark.parametrize("heading", [270, 225])
def test_custom_marker_keeps_aspect_ratio_and_smooth_edges(renderer_kind, heading):
    # An opaque 1:2 body makes square distortion and NEAREST jaggies observable.
    buffer = BytesIO()
    Image.new("RGBA", (200, 400), "red").save(buffer, format="PNG")
    style = map_render_style(marker_image=buffer.getvalue())
    if renderer_kind == "legacy":
        image = _legacy_renderer(style=style, label_scale=1).render_mower(
            Point(100, 99, heading), 0, (200, 200),
            MapImageDimensions(0, 0, 200, 200, 1), 28, 0, 1,
        )
    else:
        image = Image.new("RGBA", (200, 200))
        draw_position_marker(
            image, ImageDraw.Draw(image), (100, 100), style,
            heading=(270 - heading) % 360,
        )
    alpha = image.getchannel("A")
    box = alpha.point(lambda value: 255 if value > 128 else 0).getbbox()
    assert box is not None
    if heading == 270:
        assert (box[2] - box[0], box[3] - box[1]) == (14, 28)
    else:
        histogram = alpha.histogram()
        assert sum(histogram[1:255]) > 0
        # Rotating the canvas must not shrink the mower's physical footprint.
        area = sum(value * count for value, count in enumerate(histogram)) / 255
        assert area == pytest.approx(14 * 28, rel=0.08)
