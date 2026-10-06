"""Wire shapes for JSON metadata embedded in map PNGs."""

from typing import NotRequired, TypedDict


class JsonMapEntityMetadata(TypedDict):
    angle: NotRequired[float]


class JsonMapEntity(TypedDict):
    type: str
    points: list[int]
    metaData: NotRequired[JsonMapEntityMetadata]


class JsonMapAxis(TypedDict):
    min: int
    max: int
    mid: int | None
    avg: int | None


class JsonMapLayerDimensions(TypedDict):
    x: JsonMapAxis
    y: JsonMapAxis
    pixelCount: float


class JsonMapSegmentMetadata(TypedDict):
    segmentId: int
    active: bool
    name: str | None


class JsonMapPixelLayer(TypedDict):
    type: str
    pixels: list[int]
    metaData: NotRequired[JsonMapSegmentMetadata]
    dimensions: NotRequired[JsonMapLayerDimensions]
    compressedPixels: NotRequired[list[int]]


class JsonMapSize(TypedDict):
    x: int
    y: int


class JsonMapMetadata(TypedDict):
    version: int
    rotation: int | None


JsonMapDocument = TypedDict(
    "JsonMapDocument",
    {
        "__class": str,
        "size": JsonMapSize,
        "pixelSize": int,
        "layers": list[JsonMapPixelLayer],
        "entities": list[JsonMapEntity],
        "metaData": JsonMapMetadata,
    },
)
