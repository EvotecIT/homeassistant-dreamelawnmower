"""Compatibility contracts for extracted legacy map codecs."""

from __future__ import annotations

import base64
import hashlib
import json
import zlib
from types import SimpleNamespace
from unittest.mock import Mock

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
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device_types import (
    ObstacleType,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map import (
    DreameMapMowerMapManager,
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


@pytest.mark.parametrize("include_predefined", [False, True])
def test_task_cruise_points_have_independent_ids(include_predefined: bool) -> None:
    header = bytearray(map_decoder.DreameMowerMapDecoder.HEADER_SIZE)
    header[4] = 73
    metadata = {"tpointinfo": [[120, -60, 0, 2], [240, 80, 1, 3]]}
    if include_predefined:
        metadata["pointinfo"] = {"spoint": [[10, 20, 0, 1]]}
    payload = base64.b64encode(
        zlib.compress(bytes(header) + json.dumps(metadata).encode())
    ).decode()

    decoded, _ = map_decoder.DreameMowerMapDecoder.decode_map(payload, False)

    assert decoded is not None
    assert list(decoded.task_cruise_points) == [1, 2]
    first, second = decoded.task_cruise_points.values()
    assert (first.x, first.y, first.completed, first.type) == (120, -60, False, 2)
    assert (second.x, second.y, second.completed, second.type) == (240, 80, True, 3)


@pytest.mark.parametrize("layout", ["compact", "expanded", "neglected"])
def test_obstacle_wire_photo_key_reaches_decryption(layout):
    key = "synthetic-photo-key"
    filename = "synthetic-photo.jpg"
    kind = (ObstacleType.NEGLECTED_ZONE if layout == "neglected"
            else ObstacleType.OBSTACLE)
    wire_obstacle = [1, 2, kind.value, 0.99, 1001]
    wire_obstacle += ([key, filename] if layout == "compact"
                      else [filename, key, 0, 0, 0.1, 0.1, 2, 0])
    header = bytearray(map_decoder.DreameMowerMapDecoder.HEADER_SIZE)
    header[4] = 73
    header[17:19] = (50).to_bytes(2, byteorder="little", signed=True)
    header[19:21] = (1).to_bytes(2, byteorder="little", signed=True)
    header[21:23] = (1).to_bytes(2, byteorder="little", signed=True)
    payload = base64.b64encode(zlib.compress(
        bytes(header) + b"\x01" + json.dumps({
            "iscleanlog": 1, "ai_obstacle": [wire_obstacle],
        }).encode()
    )).decode()
    decoded, _ = map_decoder.DreameMowerMapDecoder.decode_map(payload, False)
    assert decoded is not None
    obstacle = decoded.obstacles["1"]
    assert obstacle.key == key
    assert obstacle.possibility == (None if layout == "neglected" else 99)

    photo = b"synthetic obstacle photo"
    padder = padding.PKCS7(128).padder()
    padded = padder.update(photo) + padder.finalize()
    encryptor = Cipher(
        algorithms.AES(hashlib.md5(key.encode()).digest()), modes.ECB(),
    ).encryptor()
    cloud = SimpleNamespace(get_file=Mock(return_value=(
        encryptor.update(padded) + encryptor.finalize()
    )))
    manager = DreameMapMowerMapManager(
        SimpleNamespace(cloud=cloud, dreame_cloud=True),
    )
    manager._get_file_url = Mock(return_value="https://example.invalid/photo")

    assert manager.get_obstacle_image(decoded, 1) == (photo, obstacle)
