"""Validation and metadata for mower point-cloud downloads."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any

DEFAULT_POINT_CLOUD_MAX_BYTES = 64 * 1024 * 1024
MAX_POINT_CLOUD_HEADER_BYTES = 64 * 1024
_SUPPORTED_DATA_ENCODINGS = frozenset({"ascii", "binary", "binary_compressed"})
_SUPPORTED_FIELD_TYPES = frozenset({"F", "I", "U"})
_SUPPORTED_FIELD_SIZES = frozenset({1, 2, 4, 8})


class DreameLawnMowerPointCloudError(ValueError):
    """Raised when a downloaded point cloud is invalid or unsupported."""


@dataclass(frozen=True, slots=True)
class DreameLawnMowerPointCloudMetadata:
    """Describe a validated PCD payload without exposing its coordinates."""

    version: str
    fields: tuple[str, ...]
    sizes: tuple[int, ...]
    types: tuple[str, ...]
    counts: tuple[int, ...]
    width: int
    height: int
    points: int
    data_encoding: str
    bytes_per_point: int
    header_bytes: int
    payload_bytes: int
    total_bytes: int

    @property
    def has_rgb(self) -> bool:
        """Return whether the cloud contains an RGB or RGBA field."""
        return "rgb" in self.fields or "rgba" in self.fields

    def as_dict(self) -> dict[str, Any]:
        """Return safe JSON-serializable point-cloud metadata."""
        return {
            "format": "pcd",
            "version": self.version,
            "fields": list(self.fields),
            "sizes": list(self.sizes),
            "types": list(self.types),
            "counts": list(self.counts),
            "width": self.width,
            "height": self.height,
            "points": self.points,
            "data_encoding": self.data_encoding,
            "bytes_per_point": self.bytes_per_point,
            "header_bytes": self.header_bytes,
            "payload_bytes": self.payload_bytes,
            "total_bytes": self.total_bytes,
            "has_rgb": self.has_rgb,
        }


@dataclass(frozen=True, slots=True)
class DreameLawnMowerPointCloudDownload:
    """Contain a validated point cloud returned by the reusable client."""

    map_index: int
    content: bytes
    metadata: DreameLawnMowerPointCloudMetadata
    content_type: str = "application/octet-stream"
    file_extension: str = "pcd"


def parse_pcd_metadata(
    content: bytes,
    *,
    max_bytes: int = DEFAULT_POINT_CLOUD_MAX_BYTES,
) -> DreameLawnMowerPointCloudMetadata:
    """Validate a PCD payload and return coordinate-free metadata."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise DreameLawnMowerPointCloudError(
            "Point-cloud maximum size must be a positive integer."
        )
    if not isinstance(content, bytes | bytearray | memoryview):
        raise DreameLawnMowerPointCloudError("Point-cloud content must be bytes.")
    raw = bytes(content)
    if not raw:
        raise DreameLawnMowerPointCloudError("Point-cloud content is empty.")
    if len(raw) > max_bytes:
        raise DreameLawnMowerPointCloudError(
            f"Point-cloud content exceeds the {max_bytes}-byte limit."
        )

    header, header_bytes = _parse_pcd_header(raw)
    version = _single_header_value(header, "VERSION")
    fields = tuple(_header_values(header, "FIELDS"))
    sizes = _parse_positive_ints(header, "SIZE")
    types = tuple(_header_values(header, "TYPE"))
    counts = (
        _parse_positive_ints(header, "COUNT")
        if "COUNT" in header
        else tuple(1 for _ in fields)
    )
    width = _parse_nonnegative_int(header, "WIDTH")
    height = _parse_nonnegative_int(header, "HEIGHT")
    points = _parse_nonnegative_int(header, "POINTS")
    data_encoding = _single_header_value(header, "DATA").casefold()

    if version not in {"0.7", ".7"}:
        raise DreameLawnMowerPointCloudError(
            f"Unsupported PCD version {version!r}; expected 0.7."
        )
    if not fields or not {"x", "y", "z"}.issubset(fields):
        raise DreameLawnMowerPointCloudError(
            "PCD fields must include x, y, and z coordinates."
        )
    if len({*fields}) != len(fields):
        raise DreameLawnMowerPointCloudError("PCD fields must be unique.")
    if not (len(fields) == len(sizes) == len(types) == len(counts)):
        raise DreameLawnMowerPointCloudError(
            "PCD FIELDS, SIZE, TYPE, and COUNT lengths do not match."
        )
    if any(size not in _SUPPORTED_FIELD_SIZES for size in sizes):
        raise DreameLawnMowerPointCloudError("PCD contains an unsupported field size.")
    if any(field_type not in _SUPPORTED_FIELD_TYPES for field_type in types):
        raise DreameLawnMowerPointCloudError("PCD contains an unsupported field type.")
    if width * height != points:
        raise DreameLawnMowerPointCloudError(
            "PCD WIDTH and HEIGHT do not match the declared point count."
        )
    if data_encoding not in _SUPPORTED_DATA_ENCODINGS:
        raise DreameLawnMowerPointCloudError(
            f"Unsupported PCD DATA encoding {data_encoding!r}."
        )

    bytes_per_point = sum(size * count for size, count in zip(sizes, counts))
    payload = raw[header_bytes:]
    _validate_pcd_payload(
        payload,
        data_encoding=data_encoding,
        points=points,
        bytes_per_point=bytes_per_point,
    )
    return DreameLawnMowerPointCloudMetadata(
        version="0.7",
        fields=fields,
        sizes=sizes,
        types=types,
        counts=counts,
        width=width,
        height=height,
        points=points,
        data_encoding=data_encoding,
        bytes_per_point=bytes_per_point,
        header_bytes=header_bytes,
        payload_bytes=len(payload),
        total_bytes=len(raw),
    )


def _parse_pcd_header(content: bytes) -> tuple[dict[str, list[str]], int]:
    header: dict[str, list[str]] = {}
    offset = 0
    limit = min(len(content), MAX_POINT_CLOUD_HEADER_BYTES)
    while offset < limit:
        newline = content.find(b"\n", offset, limit)
        if newline < 0:
            break
        line_bytes = content[offset:newline].rstrip(b"\r")
        offset = newline + 1
        try:
            line = line_bytes.decode("ascii")
        except UnicodeDecodeError as err:
            raise DreameLawnMowerPointCloudError(
                "PCD header must contain only ASCII text."
            ) from err
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        key = parts[0].upper()
        if key in header:
            raise DreameLawnMowerPointCloudError(
                f"PCD header contains duplicate {key} entries."
            )
        header[key] = parts[1:]
        if key == "DATA":
            return header, offset
    raise DreameLawnMowerPointCloudError(
        "PCD header is missing a complete DATA declaration."
    )


def _header_values(header: dict[str, list[str]], key: str) -> list[str]:
    values = header.get(key)
    if not values:
        raise DreameLawnMowerPointCloudError(
            f"PCD header is missing a {key} value."
        )
    return values


def _single_header_value(header: dict[str, list[str]], key: str) -> str:
    values = _header_values(header, key)
    if len(values) != 1:
        raise DreameLawnMowerPointCloudError(
            f"PCD {key} must contain exactly one value."
        )
    return values[0]


def _parse_positive_ints(
    header: dict[str, list[str]],
    key: str,
) -> tuple[int, ...]:
    values = _parse_ints(header, key)
    if any(value <= 0 for value in values):
        raise DreameLawnMowerPointCloudError(
            f"PCD {key} values must be positive integers."
        )
    return values


def _parse_nonnegative_int(header: dict[str, list[str]], key: str) -> int:
    values = _parse_ints(header, key)
    if len(values) != 1 or values[0] < 0:
        raise DreameLawnMowerPointCloudError(
            f"PCD {key} must be one non-negative integer."
        )
    return values[0]


def _parse_ints(
    header: dict[str, list[str]],
    key: str,
) -> tuple[int, ...]:
    try:
        return tuple(int(value) for value in _header_values(header, key))
    except ValueError as err:
        raise DreameLawnMowerPointCloudError(
            f"PCD {key} contains a non-integer value."
        ) from err


def _validate_pcd_payload(
    payload: bytes,
    *,
    data_encoding: str,
    points: int,
    bytes_per_point: int,
) -> None:
    expected_bytes = points * bytes_per_point
    if data_encoding == "binary":
        if len(payload) != expected_bytes:
            raise DreameLawnMowerPointCloudError(
                "PCD binary payload length does not match its header."
            )
        return

    if data_encoding == "binary_compressed":
        if len(payload) < 8:
            raise DreameLawnMowerPointCloudError(
                "PCD compressed payload is missing its size prefix."
            )
        compressed_bytes, uncompressed_bytes = struct.unpack_from("<II", payload)
        if uncompressed_bytes != expected_bytes:
            raise DreameLawnMowerPointCloudError(
                "PCD compressed payload size does not match its header."
            )
        if len(payload) != 8 + compressed_bytes:
            raise DreameLawnMowerPointCloudError(
                "PCD compressed payload length is incomplete."
            )
        return

    lines = [line for line in payload.splitlines() if line.strip()]
    if len(lines) != points:
        raise DreameLawnMowerPointCloudError(
            "PCD ASCII payload row count does not match its header."
        )
