"""Validation and metadata for mower point-cloud downloads."""

from __future__ import annotations

import math
import struct
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Literal

DEFAULT_POINT_CLOUD_MAX_BYTES = 32 * 1024 * 1024
DEFAULT_POINT_CLOUD_MAX_POINTS = 2_000_000
MAX_POINT_CLOUD_HEADER_BYTES = 64 * 1024
_SUPPORTED_DATA_ENCODINGS = frozenset({"ascii", "binary"})
_SUPPORTED_FIELD_TYPES = frozenset({"F", "I", "U"})
_SUPPORTED_FIELD_SIZES = frozenset({1, 2, 4, 8})
_MAX_ASCII_SCALAR_BYTES = 128
_MAX_ASCII_SCALARS_PER_POINT = 1024
_MAX_ASCII_ROW_BYTES = 64 * 1024
_VALIDATION_DEADLINE_CHECK_BYTES = 64 * 1024


class DreameLawnMowerPointCloudError(ValueError):
    """Describe a point-cloud failure without exposing private cloud details."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "point_cloud_failed",
        stage: str = "point_cloud",
        retryable: bool = True,
        public_message: str = "The mower point cloud is temporarily unavailable.",
        timeout_seconds: float | None = None,
        retry_after_seconds: int | None = None,
        vendor_error_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.retryable = retryable
        self.public_message = public_message
        self.timeout_seconds = timeout_seconds
        self.retry_after_seconds = retry_after_seconds
        self.vendor_error_code = vendor_error_code


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
    source: Literal["generated", "stored"] = "generated"
    content_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        """Hash validated content once for private conditional delivery."""
        object.__setattr__(self, "content_sha256", sha256(self.content).hexdigest())


def parse_pcd_metadata(
    content: bytes,
    *,
    max_bytes: int = DEFAULT_POINT_CLOUD_MAX_BYTES,
    max_points: int = DEFAULT_POINT_CLOUD_MAX_POINTS,
    deadline: float | None = None,
) -> DreameLawnMowerPointCloudMetadata:
    """Validate a PCD payload and return coordinate-free metadata."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise DreameLawnMowerPointCloudError(
            "Point-cloud maximum size must be a positive integer."
        )
    if (
        isinstance(max_points, bool)
        or not isinstance(max_points, int)
        or max_points <= 0
    ):
        raise DreameLawnMowerPointCloudError(
            "Point-cloud maximum point count must be a positive integer."
        )
    if not isinstance(content, bytes | bytearray | memoryview):
        raise DreameLawnMowerPointCloudError("Point-cloud content must be bytes.")
    _ensure_validation_deadline(deadline)
    raw = bytes(content)
    _ensure_validation_deadline(deadline)
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
    if any(
        field_type == "F" and size not in {4, 8}
        for field_type, size in zip(types, sizes, strict=True)
    ):
        raise DreameLawnMowerPointCloudError(
            "PCD floating-point fields must use 4-byte or 8-byte values."
        )
    for coordinate in ("x", "y", "z"):
        coordinate_index = fields.index(coordinate)
        if types[coordinate_index] != "F" or counts[coordinate_index] != 1:
            raise DreameLawnMowerPointCloudError(
                "PCD x, y, and z coordinates must be scalar floating-point fields."
            )
    if width * height != points:
        raise DreameLawnMowerPointCloudError(
            "PCD WIDTH and HEIGHT do not match the declared point count."
        )
    if points > max_points:
        raise DreameLawnMowerPointCloudError(
            "Point-cloud point count exceeds the supported rendering limit.",
            code="point_cloud_download_invalid",
            stage="download_validation",
            public_message=(
                "The generated 3D map is too large to render safely in a browser."
            ),
        )
    if data_encoding not in _SUPPORTED_DATA_ENCODINGS:
        raise DreameLawnMowerPointCloudError(
            f"Unsupported PCD DATA encoding {data_encoding!r}."
        )
    if (
        data_encoding == "ascii"
        and sum(counts) > _MAX_ASCII_SCALARS_PER_POINT
    ):
        raise DreameLawnMowerPointCloudError(
            "PCD ASCII point contains too many scalar values."
        )

    bytes_per_point = sum(
        size * count for size, count in zip(sizes, counts, strict=True)
    )
    payload = raw[header_bytes:]
    _validate_pcd_payload(
        payload,
        data_encoding=data_encoding,
        points=points,
        bytes_per_point=bytes_per_point,
        fields=fields,
        sizes=sizes,
        types=types,
        counts=counts,
        deadline=deadline,
    )
    _ensure_validation_deadline(deadline)
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
    fields: tuple[str, ...],
    sizes: tuple[int, ...],
    types: tuple[str, ...],
    counts: tuple[int, ...],
    deadline: float | None,
) -> None:
    expected_bytes = points * bytes_per_point
    if data_encoding == "binary":
        if len(payload) != expected_bytes:
            raise DreameLawnMowerPointCloudError(
                "PCD binary payload length does not match its header."
            )
        _validate_binary_coordinates(
            payload,
            points=points,
            bytes_per_point=bytes_per_point,
            fields=fields,
            sizes=sizes,
            counts=counts,
            deadline=deadline,
        )
        return

    scalar_count = sum(counts)
    max_row_bytes = min(
        _MAX_ASCII_ROW_BYTES,
        scalar_count * (_MAX_ASCII_SCALAR_BYTES + 1),
    )
    row_count = 0
    bytes_since_deadline_check = 0
    for row in _iter_nonempty_ascii_rows(
        payload,
        max_row_bytes=max_row_bytes,
    ):
        bytes_since_deadline_check += len(row) + 1
        if bytes_since_deadline_check >= _VALIDATION_DEADLINE_CHECK_BYTES:
            _ensure_validation_deadline(deadline)
            bytes_since_deadline_check = 0
        row_count += 1
        if row_count > points:
            raise DreameLawnMowerPointCloudError(
                "PCD ASCII payload row count does not match its header."
            )
        if len(row) > max_row_bytes:
            raise DreameLawnMowerPointCloudError(
                "PCD ASCII payload row exceeds the supported size."
            )
        try:
            values = row.decode("ascii").split()
        except UnicodeDecodeError as err:
            raise DreameLawnMowerPointCloudError(
                "PCD ASCII payload must contain only ASCII text."
            ) from err
        if len(values) != scalar_count:
            raise DreameLawnMowerPointCloudError(
                "PCD ASCII payload column count does not match its header."
            )
        _validate_ascii_scalars(
            values,
            sizes=sizes,
            types=types,
            counts=counts,
        )
    _ensure_validation_deadline(deadline)
    if row_count != points:
        raise DreameLawnMowerPointCloudError(
            "PCD ASCII payload row count does not match its header."
        )


def _iter_nonempty_ascii_rows(
    payload: bytes,
    *,
    max_row_bytes: int,
) -> Iterator[bytes]:
    """Yield one non-empty ASCII PCD row without materializing all rows."""
    offset = 0
    payload_bytes = len(payload)
    while offset < payload_bytes:
        newline = payload.find(b"\n", offset)
        if newline < 0:
            newline = payload_bytes
        row_bytes = newline - offset
        if row_bytes > max_row_bytes:
            allowed_trailing_carriage_return = (
                row_bytes == max_row_bytes + 1
                and payload[newline - 1] == 0x0D
            )
            if not allowed_trailing_carriage_return:
                raise DreameLawnMowerPointCloudError(
                    "PCD ASCII payload row exceeds the supported size."
                )
        row = payload[offset:newline].rstrip(b"\r")
        offset = newline + 1
        if row.strip():
            yield row


def _validate_binary_coordinates(
    payload: bytes,
    *,
    points: int,
    bytes_per_point: int,
    fields: tuple[str, ...],
    sizes: tuple[int, ...],
    counts: tuple[int, ...],
    deadline: float | None,
) -> None:
    """Reject non-finite binary coordinates before a frontend parses them."""
    field_offsets: dict[str, tuple[int, int]] = {}
    offset = 0
    for field_name, size, count in zip(fields, sizes, counts, strict=True):
        field_offsets[field_name] = (offset, size)
        offset += size * count

    coordinates = tuple(field_offsets[field] for field in ("x", "y", "z"))
    deadline_check_interval = max(
        1,
        _VALIDATION_DEADLINE_CHECK_BYTES // bytes_per_point,
    )
    for point_index in range(points):
        if point_index % deadline_check_interval == 0:
            _ensure_validation_deadline(deadline)
        point_offset = point_index * bytes_per_point
        for coordinate_offset, coordinate_size in coordinates:
            value = struct.unpack_from(
                "<f" if coordinate_size == 4 else "<d",
                payload,
                point_offset + coordinate_offset,
            )[0]
            if not math.isfinite(value):
                raise DreameLawnMowerPointCloudError(
                    "PCD coordinates must contain only finite values."
                )
    _ensure_validation_deadline(deadline)


def _ensure_validation_deadline(deadline: float | None) -> None:
    """Stop CPU-bound validation after the caller's absolute deadline."""
    if deadline is not None and time.monotonic() >= deadline:
        raise DreameLawnMowerPointCloudError(
            "Point-cloud validation timed out."
        )


def _validate_ascii_scalars(
    values: list[str],
    *,
    sizes: tuple[int, ...],
    types: tuple[str, ...],
    counts: tuple[int, ...],
) -> None:
    """Validate every declared ASCII scalar and its numeric range."""
    scalar_index = 0
    for size, field_type, count in zip(sizes, types, counts, strict=True):
        for _ in range(count):
            token = values[scalar_index]
            scalar_index += 1
            try:
                if field_type == "F":
                    value = float(token)
                    if size == 4:
                        value = struct.unpack("<f", struct.pack("<f", value))[0]
                    if not math.isfinite(value):
                        raise ValueError
                    continue

                value = int(token, 10)
                bits = size * 8
                minimum = -(1 << (bits - 1)) if field_type == "I" else 0
                maximum = (
                    (1 << (bits - 1)) - 1
                    if field_type == "I"
                    else (1 << bits) - 1
                )
                if not minimum <= value <= maximum:
                    raise ValueError
            except (OverflowError, ValueError, struct.error) as err:
                raise DreameLawnMowerPointCloudError(
                    "PCD ASCII payload contains an invalid scalar value."
                ) from err
