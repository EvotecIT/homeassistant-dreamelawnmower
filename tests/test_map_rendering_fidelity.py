"""Observable rendering contracts for native mower geometry."""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    map_geometry,
    map_visuals,
    vector_map,
)
from dreame_lawn_mower_client import render_app_map_payload_png


@pytest.mark.parametrize("renderer", ["app", "vector"])
def test_rotation_moves_geometry_but_keeps_labels_upright(renderer) -> None:
    points = ((0, 0), (200, 0), (200, 100), (0, 100))
    label = "Long lawn label"
    text_sizes = []
    canvas_sizes = []
    for rotation in (0, 90, 180, 270):
        style = map_visuals.map_render_style("mint", rotation=rotation)
        if renderer == "app":
            png, _, _ = render_app_map_payload_png(
                {"map": [{"id": 1, "name": label, "data": points}]}, style=style
            )
        else:
            png = vector_map.render_vector_map_png(
                vector_map.DreameLawnMowerVectorMap(
                    boundary=vector_map.DreameLawnMowerVectorBoundary(0, 0, 200, 100),
                    zones=(
                        vector_map.DreameLawnMowerVectorZone(
                            zone_id=1,
                            points=points,
                            name=label,
                        ),
                    ),
                ),
                style=style,
            )
        with Image.open(BytesIO(png)) as image:
            canvas_sizes.append(image.size)
            # Mint geometry is light; this isolates the actual rendered text.
            text = image.convert("L").point(lambda value: 255 if value < 90 else 0)
            bounds = text.getbbox()
            assert bounds is not None
            width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
            assert width > height * 4
            text_sizes.append((width, height))
    assert max(w for w, _ in text_sizes) - min(w for w, _ in text_sizes) <= 2
    assert max(h for _, h in text_sizes) - min(h for _, h in text_sizes) <= 2
    assert canvas_sizes[0] == canvas_sizes[2]
    assert canvas_sizes[1] == canvas_sizes[3] == canvas_sizes[0][::-1]


def test_concave_lawn_label_is_inside_grass_not_the_empty_middle() -> None:
    lawn = (
        (0, 0),
        (100, 0),
        (100, 100),
        (0, 100),
        (0, 80),
        (70, 80),
        (70, 20),
        (0, 20),
    )
    point = map_geometry.polygon_label_point(lawn)
    assert map_geometry._signed_boundary_distance(point, lawn) > 10
    assert not (point[0] < 70 and 20 < point[1] < 80)


def test_decorative_stripes_stay_inside_the_lawn() -> None:
    payload = {"map": [{"id": 1, "data": [[0, 0], [100, 0], [0, 100]]}]}
    png, width, height = render_app_map_payload_png(
        payload, style=map_visuals.map_render_style("mint")
    )
    with Image.open(BytesIO(png)) as image:
        background = map_visuals.map_render_style("mint").background[:3]
        assert image.getpixel((48, height - 49)) == background
        # Sample across an interior scanline away from the text and outline.
        samples = [image.getpixel((x, 120)) for x in range(width // 2, width - 80)]
        assert len(set(samples)) > 1


def test_app_trajectory_respects_historical_path_visibility() -> None:
    payload = {
        "map": [{"data": [[0, 0], [100, 0], [100, 100], [0, 100]]}],
        "trajectory": [[[10, 20], [90, 20]]],
    }
    colors = {}
    for choice in ("hidden", "subtle", "detailed"):
        png, width, _ = render_app_map_payload_png(
            payload, style=map_visuals.map_render_style(mowing_path_style=choice)
        )
        scale = (width - 96) / 100
        with Image.open(BytesIO(png)) as image:
            colors[choice] = image.getpixel((round(width / 2), round(20 * scale + 48)))
    def distance(choice):
        return sum(
            abs(a - b) for a, b in zip(colors[choice], colors["hidden"], strict=True)
        )
    assert 0 < distance("subtle") < distance("detailed")
