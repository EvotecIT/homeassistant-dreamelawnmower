"""Camera sizing contracts and bounded variant reuse."""

import asyncio
from io import BytesIO

from PIL import Image

from custom_components.dreame_lawn_mower.map_image_variants import (
    MapImageVariants,
    resize_map_jpeg,
)


def jpeg(color: str = "green") -> bytes:
    output = BytesIO()
    Image.new("RGB", (800, 400), color).save(output, format="JPEG")
    return output.getvalue()


def test_resize_preserves_aspect_and_never_upscales():
    source = jpeg()
    for width, height, expected in [
        (200, 200, (200, 100)),
        (None, 100, (200, 100)),
        (200, None, (200, 100)),
        (1600, 1000, (800, 400)),
    ]:
        result = resize_map_jpeg(source, width, height)
        with Image.open(BytesIO(result)) as image:
            assert image.size == expected
    assert resize_map_jpeg(source, 1600, 1000) is source


def test_variants_coalesce_invalidate_and_evict():
    async def exercise():
        cache = MapImageVariants(max_entries=2)
        source = jpeg()
        calls = 0

        async def execute(callback):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0)
            return callback()

        results = await asyncio.gather(
            *(cache.async_get(source, 200, None, execute) for _ in range(5))
        )
        assert calls == 1
        assert all(result is results[0] for result in results)
        await cache.async_get(source, 300, None, execute)
        await cache.async_get(source, 400, None, execute)
        await cache.async_get(source, 200, None, execute)
        assert calls == 4  # oldest variant was evicted
        await cache.async_get(jpeg("red"), 200, None, execute)
        assert calls == 5  # source change cannot reuse the previous map
        assert await cache.async_get(source, -1, 0, execute) is source
        assert calls == 5

    asyncio.run(exercise())


def test_oversized_variant_is_not_retained():
    async def exercise():
        cache = MapImageVariants(max_bytes=1)
        source = jpeg()
        calls = 0

        async def execute(callback):
            nonlocal calls
            calls += 1
            return callback()

        await cache.async_get(source, 200, None, execute)
        await cache.async_get(source, 200, None, execute)
        assert calls == 2

    asyncio.run(exercise())
