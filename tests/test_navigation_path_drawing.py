"""Road presentation retains open geometry without implying cut coverage."""

from dataclasses import replace

from PIL import Image

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_drawing import (
    draw_navigation_path,
    render_legacy_navigation_paths,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_types import (
    MapImageDimensions,
    Wall,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_visuals import (
    map_render_style,
)


def test_road_is_open_with_rounded_caps_and_dashed_center() -> None:
    image = Image.new("RGBA", (120, 120))
    draw_navigation_path(image, [(20, 20), (100, 20), (100, 100)], map_render_style())
    assert image.getpixel((60, 60))[3] == 0  # no closing diagonal
    assert image.getpixel((14, 20))[3] > 0  # rounded start cap
    assert image.getpixel((60, 26))[3] > 0  # road body wider than old line
    assert image.getpixel((60, 30))[3] == 0
    assert image.getpixel((23, 20)) != image.getpixel((30, 20))


def test_dash_phase_survives_vertices_and_duplicate_points() -> None:
    whole = Image.new("RGBA", (140, 70))
    split = whole.copy()
    style = map_render_style()
    draw_navigation_path(whole, [(20, 30), (120, 30)], style)
    draw_navigation_path(split, [(20, 30), (51, 30), (51, 30), (120, 30)], style)
    assert whole.tobytes() == split.tobytes()


def test_short_and_empty_paths_are_safe_and_stroke_scale_is_respected() -> None:
    empty = Image.new("RGBA", (100, 100))
    draw_navigation_path(empty, [], map_render_style())
    draw_navigation_path(empty, [(20, 20)], map_render_style())
    assert empty.getbbox() is None
    draw_navigation_path(empty, [(20, 50), (80, 50)], map_render_style(stroke_scale=2))
    assert empty.getpixel((50, 63))[3] > 0
    assert empty.getpixel((50, 68))[3] == 0


def test_legacy_pathways_use_shared_style_without_joining_separate_records() -> None:
    dimensions = MapImageDimensions(0, 0, 100, 100, 1)
    paths = [Wall(20, 80, 40, 80), Wall(70, 30, 85, 30)]
    color = (60, 80, 100, 255)
    actual = render_legacy_navigation_paths(paths, color, (200, 200), dimensions, 1, 2)
    expected = Image.new("RGBA", (200, 200))
    style = replace(map_render_style(stroke_scale=2), navigation_path=color)
    for path in paths:
        point = path.to_img(dimensions)
        draw_navigation_path(
            expected,
            [(point.x0 * 2, point.y0 * 2), (point.x1 * 2, point.y1 * 2)],
            style,
        )
    assert actual.tobytes() == expected.tobytes()
    assert actual.getpixel((110, 90))[3] == 0
