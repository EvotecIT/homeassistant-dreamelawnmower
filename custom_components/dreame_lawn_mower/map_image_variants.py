"""Bounded, demand-sized JPEG variants for map camera responses."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from functools import partial
from io import BytesIO

from PIL import Image


def resize_map_jpeg(image: bytes, width: int | None, height: int | None) -> bytes:
    """Fit inside the requested bounds without cropping or enlarging the map."""
    with Image.open(BytesIO(image)) as source:
        bounds = (width or source.width, height or source.height)
        if source.width <= bounds[0] and source.height <= bounds[1]:
            return image
        resized = source.convert("RGB")
        resized.thumbnail(bounds, Image.Resampling.LANCZOS)
        output = BytesIO()
        resized.save(output, format="JPEG", quality=90)
        return output.getvalue()


class MapImageVariants:
    """Reuse resized images while bounding retained entries and encoded bytes."""

    def __init__(self, *, max_entries: int = 8, max_bytes: int = 4 * 1024 * 1024):
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._source: bytes | None = None
        self._variants: OrderedDict[tuple[int | None, int | None], bytes] = (
            OrderedDict()
        )
        self._bytes = 0
        self._lock = asyncio.Lock()

    async def async_get(
        self,
        image: bytes,
        width: int | None,
        height: int | None,
        execute: Callable[[Callable[[], bytes]], Awaitable[bytes]],
    ) -> bytes:
        """Coalesce simultaneous requests and resize outside the event loop."""
        width = width if isinstance(width, int) and width > 0 else None
        height = height if isinstance(height, int) and height > 0 else None
        if width is None and height is None:
            return image
        key = (width, height)
        async with self._lock:
            if image is not self._source:
                self._source = image
                self._variants.clear()
                self._bytes = 0
            if key in self._variants:
                self._variants.move_to_end(key)
                return self._variants[key]
            result = await execute(partial(resize_map_jpeg, image, width, height))
            if len(result) <= self._max_bytes and self._max_entries > 0:
                self._variants[key] = result
                self._bytes += len(result)
                while (
                    len(self._variants) > self._max_entries
                    or self._bytes > self._max_bytes
                ):
                    _, expired = self._variants.popitem(last=False)
                    self._bytes -= len(expired)
            return result
