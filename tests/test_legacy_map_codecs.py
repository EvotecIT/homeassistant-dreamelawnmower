"""Compatibility contracts for extracted legacy map codecs."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import zlib

import numpy as np
import pytest
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    device as device_module,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    map_decoder,
    map_json_renderer,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map import (
    DreameMowerMapDataJsonRenderer as LegacyMapDataJsonRenderer,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map import (
    DreameMowerMapDecoder as LegacyMapDecoder,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_types import (
    MapData,
    MapDataPartial,
    MapImageDimensions,
    Point,
    Segment,
)


def test_map_codecs_keep_historical_import_contract() -> None:
    assert LegacyMapDecoder is map_decoder.DreameMowerMapDecoder
    assert LegacyMapDataJsonRenderer is map_json_renderer.DreameMowerMapDataJsonRenderer


def test_device_uses_canonical_map_decoder_owner() -> None:
    assert device_module.DreameMowerMapDecoder is map_decoder.DreameMowerMapDecoder


def test_map_decoder_reads_compressed_header_and_metadata() -> None:
    header = bytearray(map_decoder.DreameMowerMapDecoder.HEADER_SIZE)
    header[0:2] = (7).to_bytes(2, byteorder="little", signed=True)
    header[2:4] = (11).to_bytes(2, byteorder="little", signed=True)
    header[4] = 1
    raw_map = bytes(header) + b'{"timestamp_ms":123456,"seg_inf":{"7":{"nei_id":[8]}}}'
    payload = base64.b64encode(zlib.compress(raw_map)).decode()

    partial = map_decoder.DreameMowerMapDecoder.decode_map_partial(payload)

    assert partial is not None
    assert partial.map_id == 7
    assert partial.frame_id == 11
    assert partial.frame_type == 1
    assert partial.timestamp_ms == 123456
    assert partial.data_json == {
        "timestamp_ms": 123456, "seg_inf": {"7": {"nei_id": [8]}}
    }


@pytest.mark.parametrize("metadata", [b"null", b"[]", b'{"timestamp_ms":"bad"}'])
def test_map_decoder_keeps_header_when_optional_metadata_is_invalid(
    metadata: bytes,
) -> None:
    header = bytearray(map_decoder.DreameMowerMapDecoder.HEADER_SIZE)
    header[0:2] = (7).to_bytes(2, "little", signed=True)
    payload = base64.b64encode(zlib.compress(bytes(header) + metadata)).decode()

    partial = map_decoder.DreameMowerMapDecoder.decode_map_partial(payload)

    assert partial is not None
    assert partial.map_id == 7
    assert partial.timestamp_ms is None
    assert partial.data_json == {}


def test_json_renderer_packages_default_map_png() -> None:
    renderer = map_json_renderer.DreameMowerMapDataJsonRenderer()

    assert renderer.render_map(None).startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.parametrize(
    "payload", ["", base64.b64encode(zlib.compress(b"short header")).decode()]
)
def test_rejected_map_payload_keeps_decoder_result_shape(payload: str) -> None:
    assert map_decoder.DreameMowerMapDecoder.decode_map(payload, False) == (
        None, None
    )
    assert map_decoder.DreameMowerMapDecoder.decode_saved_map(payload, False) is None


@pytest.mark.parametrize("value", ["invalid", None, [], {}])
def test_invalid_optional_metadata_does_not_hide_decoded_header(value: object) -> None:
    header = bytearray(map_decoder.DreameMowerMapDecoder.HEADER_SIZE)
    header[0:2] = (7).to_bytes(2, byteorder="little", signed=True)
    header[4] = 73
    payload = base64.b64encode(
        zlib.compress(bytes(header) + json.dumps({"mra": value}).encode())
    ).decode()

    decoded, saved = map_decoder.DreameMowerMapDecoder.decode_map(payload, False)

    assert decoded is not None
    assert decoded.map_id == 7
    assert saved is None


@pytest.mark.parametrize("inline_key", [False, True])
def test_map_decoder_decrypts_explicit_and_inline_keys(
    inline_key: bool, caplog: pytest.LogCaptureFixture
) -> None:
    key = "unit_test-map-key"
    iv = "0123456789abcdef"
    header = bytearray(map_decoder.DreameMowerMapDecoder.HEADER_SIZE)
    header[0:2] = (7).to_bytes(2, "little", signed=True)
    header[2:4] = (11).to_bytes(2, "little", signed=True)
    header[4] = 73
    raw = bytes(header) + b'{"timestamp_ms":1700000000000}'
    padder = padding.PKCS7(128).padder()
    padded = padder.update(zlib.compress(raw)) + padder.finalize()
    cipher = Cipher(
        algorithms.AES(hashlib.sha256(key.encode()).hexdigest()[:32].encode()),
        modes.CBC(iv.encode()),
    )
    encryptor = cipher.encryptor()
    encoded = base64.b64encode(
        encryptor.update(padded) + encryptor.finalize()
    ).decode()
    payload = f"{encoded},{key}" if inline_key else encoded

    with caplog.at_level(logging.DEBUG, logger=map_decoder.__name__):
        partial = map_decoder.DreameMowerMapDecoder.decode_map_partial(
            payload, iv, None if inline_key else key
        )

    assert partial is not None
    assert partial.raw == raw
    assert (partial.map_id, partial.frame_id) == (7, 11)
    assert partial.timestamp_ms == 1700000000000
    assert key not in caplog.text
    assert encoded not in caplog.text


@pytest.mark.parametrize(
    ("position", "expected"),
    [
        (Point(10, 10), 1),
        (Point(-50, -50), 1),
        (Point(-500, -500), 0),
        (Point(1000, 1000), 0),
    ],
)
def test_robot_segment_outside_pixels_uses_geometry_fallback(
    position: Point, expected: int
) -> None:
    map_data = MapData()
    map_data.saved_map_status = 2
    map_data.dimensions = MapImageDimensions(0, 0, 10, 10, 50)
    map_data.pixel_type = np.zeros((10, 10), dtype=np.uint8)
    map_data.pixel_type[0, 0] = 1
    map_data.pixel_type[9, 9] = 2
    map_data.segments = {
        1: Segment(1, 0, 0, 50, 50),
        2: Segment(2, 400, 400, 450, 450),
    }
    map_data.robot_position = position

    map_decoder.DreameMowerMapDecoder.set_robot_segment(map_data)

    assert map_data.robot_segment == expected


def test_segment_extraction_without_geometry_has_no_segments() -> None:
    assert map_decoder.DreameMowerMapDecoder.get_segments(MapData(), False) == {}


@pytest.mark.parametrize(
    ("saved", "raw_available"), [(False, True), (True, True), (True, False)]
)
def test_segment_extraction_converts_pixel_bounds_and_centers(
    saved: bool, raw_available: bool
) -> None:
    map_data = MapData()
    map_data.saved_map = saved
    map_data.dimensions = MapImageDimensions(100, 200, 2, 3, 50)
    map_data.pixel_type = np.array([[1, 1], [1, 1], [0, 2]], dtype=np.uint8)
    map_data.data = bytes([1, 1, 0, 1, 1, 2]) if raw_available else None

    segments = map_decoder.DreameMowerMapDecoder.get_segments(map_data, False)

    assert set(segments) == {1, 2}
    assert (segments[1].x0, segments[1].y0, segments[1].x1, segments[1].y1) == (
        200, 50, 300, 150
    )
    assert (segments[1].x, segments[1].y) == (250, 150)
    assert (segments[2].x0, segments[2].y0, segments[2].x1, segments[2].y1) == (
        300, 100, 350, 150
    )
    assert (segments[2].x, segments[2].y) == (300, 150)


@pytest.mark.parametrize(
    ("width", "height", "pixels"), [(2, 2, b"\x01\x01\x01"), (-1, 2, b"")]
)
def test_map_decoder_rejects_incomplete_or_negative_image_bounds(
    width: int, height: int, pixels: bytes
) -> None:
    header = bytearray(map_decoder.DreameMowerMapDecoder.HEADER_SIZE)
    header[19:21] = width.to_bytes(2, "little", signed=True)
    header[21:23] = height.to_bytes(2, "little", signed=True)
    raw = bytes(header) + pixels
    encoded = base64.b64encode(zlib.compress(raw)).decode()

    assert map_decoder.DreameMowerMapDecoder.decode_map_partial(encoded) is None
    partial = MapDataPartial()
    partial.raw = raw
    assert map_decoder.DreameMowerMapDecoder.decode_map_data_from_partial(
        partial, False
    ) == (None, None)


def test_map_decoder_partial_without_binary_data_is_unavailable() -> None:
    assert map_decoder.DreameMowerMapDecoder.decode_map_data_from_partial(
        MapDataPartial(), False
    ) == (None, None)


def test_complete_image_without_optional_metadata_is_accepted() -> None:
    header = bytearray(map_decoder.DreameMowerMapDecoder.HEADER_SIZE)
    header[4] = 73
    header[17:19] = (50).to_bytes(2, "little", signed=True)
    header[19:21] = (2).to_bytes(2, "little", signed=True)
    header[21:23] = (2).to_bytes(2, "little", signed=True)
    raw = bytes(header) + bytes(4)
    encoded = base64.b64encode(zlib.compress(raw)).decode()

    decoded, saved = map_decoder.DreameMowerMapDecoder.decode_map(encoded, False)

    assert decoded is not None
    assert decoded.dimensions == MapImageDimensions(0, 0, 2, 2, 50)
    assert decoded.data == bytes(4)
    assert saved is None


@pytest.mark.parametrize(
    "value, expected", [(42, 42), (42.9, 42), ("42", 42), (True, 1), (-42, -42)]
)
def test_numeric_map_metadata_preserves_wire_conversion(
    value: object, expected: int,
) -> None:
    header = bytearray(map_decoder.DreameMowerMapDecoder.HEADER_SIZE)
    header[4] = 73
    metadata = {key: value for key in (
        "timestamp_ms", "mra", "cs", "ct", "wm", "clean_finish_remain_electricity"
    )}
    payload = base64.b64encode(
        zlib.compress(bytes(header) + json.dumps(metadata).encode())
    ).decode()

    decoded, _ = map_decoder.DreameMowerMapDecoder.decode_map(payload, False)

    assert decoded is not None
    assert decoded.timestamp_ms == expected
    assert decoded.rotation == expected
    assert decoded.cleaned_area == expected
    assert decoded.cleaning_time == expected
    assert decoded.work_status == expected
    assert decoded.remaining_battery == expected
