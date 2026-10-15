"""Contract tests for the vendor real-scene map background."""

from __future__ import annotations

import asyncio
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from custom_components.dreame_lawn_mower.const import CONF_MAP_BACKGROUND
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    public_download,
    real_scene,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_projection import (  # noqa: E501
    vector_map_projection,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_visuals import (
    map_render_style,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.real_scene import (
    paint_real_scene,
    parse_real_scene_name,
    real_scene_from_png,
)
from custom_components.dreame_lawn_mower.map_presentation import map_style
from dreame_lawn_mower_client import render_app_map_payload_png

NAME = (
    "ali_dreame/2026/10/15/IQ000001/-1234_150138416."
    "2026-10-15-11-01-36_275_202_3cm_processed.png"
)


def _png(width: int, height: int, marks: dict[tuple[int, int], tuple]) -> bytes:
    image = Image.new("RGBA", (width, height), (0, 0, 255, 255))
    for (u, v), color in marks.items():
        for du in range(-2, 3):
            for dv in range(-2, 3):
                image.putpixel((u + du, v + dv), color)
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def test_object_name_gives_origin_and_ground_resolution() -> None:
    assert parse_real_scene_name(NAME) == (275.0, 202.0, 30.0)
    assert parse_real_scene_name("x/IQ1/a_10_20_5mm.png") == (10.0, 20.0, 5.0)
    assert parse_real_scene_name("x/IQ1/a_10_20_0.1m.png") == (10.0, 20.0, 100.0)
    assert parse_real_scene_name("x/IQ1/a_10_20_processed.png") is None
    assert parse_real_scene_name("x/IQ1/a_mid_20_3cm.png") is None
    assert parse_real_scene_name("x/IQ1/a_10_20_0cm.png") is None


def test_only_png_objects_with_placement_become_scenes() -> None:
    png = _png(8, 8, {})
    scene = real_scene_from_png(1, NAME, png)
    assert scene is not None
    assert (scene.map_index, scene.origin_x, scene.millimetres_per_pixel) == (
        1,
        275.0,
        30.0,
    )
    assert real_scene_from_png(1, NAME, b"\xff\xd8not a png") is None
    assert real_scene_from_png(1, "x/IQ1/a.png", png) is None


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_photo_pixel_lands_under_the_same_map_point(rotation: int) -> None:
    # Photo pixel (u, v) is map millimetres ((u - 20) * 30, (40 - v) * 30).
    scene = real_scene_from_png(
        0,
        "x/IQ1/a_20_40_3cm.png",
        _png(60, 80, {(50, 10): (255, 0, 0, 255), (10, 70): (0, 255, 0, 255)}),
    )
    assert scene is not None
    projection = vector_map_projection(-1000, -1500, 1500, 1500, rotation=rotation)
    canvas = Image.new("RGBA", (projection.width, projection.height), (0, 0, 0, 255))

    assert paint_real_scene(canvas, scene, projection.point_exact, opacity=1.0)

    red = canvas.getpixel(projection.point((50 - 20) * 30, (40 - 10) * 30))
    green = canvas.getpixel(projection.point((10 - 20) * 30, (40 - 70) * 30))
    assert red[0] > 200 and red[1] < 60
    assert green[1] > 200 and green[0] < 60


def test_app_map_renderer_draws_the_photo_under_the_lawn() -> None:
    lawn_cm = [[-60, -60], [60, -60], [60, 60], [-60, 60]]
    payload = {"map": [{"id": 1, "type": 0, "data": lawn_cm}]}
    scene = real_scene_from_png(0, "x/IQ1/a_50_50_3cm.png", _png(100, 100, {}))
    standard, *_ = render_app_map_payload_png(payload, style=map_render_style())
    photo, *_ = render_app_map_payload_png(
        payload, style=map_render_style(real_scene=scene)
    )

    def lawn(png: bytes) -> tuple[int, ...]:
        # Inside the lawn, away from the centred zone label.
        with Image.open(BytesIO(png)) as image:
            return image.convert("RGB").getpixel(
                (image.width * 3 // 10, image.height * 3 // 10)
            )

    assert lawn(photo)[2] > lawn(standard)[2] + 60


def test_real_scene_style_follows_option_and_map_index() -> None:
    scene = real_scene_from_png(1, NAME, _png(8, 8, {}))
    enabled = {CONF_MAP_BACKGROUND: "real_scene"}

    assert map_style({}, 1, real_scene=scene).real_scene is None
    assert map_style(enabled, 0, real_scene=scene).real_scene is None
    style = map_style(enabled, 1, real_scene=scene)
    assert style.real_scene is scene
    assert style.zone_pattern == "solid"
    assert all(color[3] <= 56 for color in style.zone_fills)


class _Cloud:
    def __init__(self, files: dict[str, bytes | None]) -> None:
        self.files = files
        self.downloads: list[str] = []

    async def async_get_interim_file_url(self, did, model, name, **kwargs):
        return f"https://signed/{name}" if name in self.files else None

    async def async_get_public_file(self, url, **kwargs):
        name = url.removeprefix("https://signed/")
        self.downloads.append(name)
        if self.files[name] is None:
            raise public_download.PublicDownloadError(
                "missing", reason="http_status", status=404
            )
        return self.files[name]


def _client(cloud: _Cloud, monkeypatch, names: list[str]) -> SimpleNamespace:
    actions: list[dict] = []

    async def read_app_action(client, action, *, deadline):
        actions.append(action)
        return {"m": "r", "r": 0, "d": {"name": names}}

    async def cloud_read(read):
        return await read(cloud)

    monkeypatch.setattr(real_scene, "async_read_app_action", read_app_action)
    return SimpleNamespace(
        _descriptor=SimpleNamespace(did="did", model="mova.mower.g2529c"),
        _async_cloud_read=cloud_read,
        actions=actions,
    )


def test_fetch_downloads_only_when_the_object_name_changes(monkeypatch) -> None:
    cloud = _Cloud({NAME: _png(8, 8, {})})
    client = _client(cloud, monkeypatch, [NAME, ""])

    first = asyncio.run(real_scene.async_fetch_real_scene(client, 0))
    again = asyncio.run(real_scene.async_fetch_real_scene(client, 0, current=first))

    assert first is not None and again is first
    assert cloud.downloads == [NAME]
    assert client.actions[0] == {"m": "g", "t": "OBJ", "d": {"type": "ARmap"}}
    assert asyncio.run(real_scene.async_fetch_real_scene(client, 1)) is None
    assert asyncio.run(real_scene.async_fetch_real_scene(client, 5)) is None


def test_fetch_treats_a_deleted_object_as_no_photo(monkeypatch) -> None:
    cloud = _Cloud({NAME: None})
    client = _client(cloud, monkeypatch, [NAME])

    assert asyncio.run(real_scene.async_fetch_real_scene(client, 0)) is None
