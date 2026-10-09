"""Transport-independent assembly of versioned schedule document chunks."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .client_shared_helpers import _app_action_data, _positive_int
from .exceptions import DreameLawnMowerConnectionError


class ScheduleDocumentReader:
    """Retain byte offsets and validate every chunk before advancing a read."""

    def __init__(
        self, *, size: int, version: int, chunk_size: int, document_version: int,
    ) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be greater than zero.")
        self.size = size
        self.version = version
        self.chunk_size = chunk_size
        self.document_version = document_version
        self.offset = 0
        self.chunk_count = 0
        self._chunks = bytearray()

    @property
    def complete(self) -> bool:
        """Whether all bytes announced by the metadata have been received."""
        return self.offset >= self.size

    def request(self) -> dict[str, Any]:
        """Build the next read at the first byte not yet received."""
        return {
            "m": "g",
            "t": f"SCHDDV{self.document_version}",
            "d": {
                "s": self.offset,
                "l": min(self.chunk_size, self.size - self.offset),
                "v": self.version,
            },
        }

    def append_response(self, response: Mapping[str, Any]) -> None:
        """Validate identity and byte counts before retaining a chunk."""
        data = _app_action_data(response)
        if not isinstance(data, Mapping) or "d" not in data:
            raise DreameLawnMowerConnectionError(
                f"SCHDDV{self.document_version} returned invalid chunk "
                f"at offset {self.offset}."
            )
        text = str(data.get("d") or "")
        encoded = text.encode("utf-8")
        returned_size = _positive_int(data.get("l"))
        if any(
            key in data
            and (
                isinstance(data[key], bool) or _positive_int(data[key]) != expected
            )
            for key, expected in (("s", self.offset), ("v", self.version))
        ):
            raise DreameLawnMowerConnectionError(
                f"SCHDDV{self.document_version} returned a mismatched chunk identity."
            )
        if "l" in data and (
            isinstance(data["l"], bool) or returned_size != len(encoded)
        ):
            raise DreameLawnMowerConnectionError(
                f"SCHDDV{self.document_version} returned an invalid chunk size."
            )
        if not encoded:
            raise DreameLawnMowerConnectionError(
                f"SCHDDV{self.document_version} returned empty data "
                f"at offset {self.offset}."
            )
        if self.offset + len(encoded) > self.size:
            raise DreameLawnMowerConnectionError(
                f"SCHDDV{self.document_version} returned too much data "
                f"at offset {self.offset}."
            )
        self._chunks.extend(encoded)
        self.offset += returned_size if returned_size else len(encoded)
        self.chunk_count += 1

    def result(self) -> tuple[str, int, int]:
        """Decode only a complete document, preserving its observed byte count."""
        if not self.complete:
            raise DreameLawnMowerConnectionError("Schedule document is incomplete.")
        return self._chunks.decode("utf-8"), self.chunk_count, self.offset
