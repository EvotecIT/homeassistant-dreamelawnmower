"""Compatibility contracts for extracted legacy map codecs."""

from __future__ import annotations

import base64
import zlib

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
    raw_map = bytes(header) + b'{"timestamp_ms":123456}'
    payload = base64.b64encode(zlib.compress(raw_map)).decode()

    partial = map_decoder.DreameMowerMapDecoder.decode_map_partial(payload)

    assert partial is not None
    assert partial.map_id == 7
    assert partial.frame_id == 11
    assert partial.frame_type == 1
    assert partial.timestamp_ms == 123456


def test_json_renderer_packages_default_map_png() -> None:
    renderer = map_json_renderer.DreameMowerMapDataJsonRenderer()

    assert renderer.render_map(None).startswith(b"\x89PNG\r\n\x1a\n")
