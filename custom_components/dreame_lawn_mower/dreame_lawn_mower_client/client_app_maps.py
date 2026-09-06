"""Serialized read-only downloads of mower-native app map payloads."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from .client_map_helpers import (
    _app_map_entries_are_valid,
    _app_map_payload_summary,
    _normalize_app_map_entries,
    _validate_app_map_chunk_size,
)
from .client_shared_helpers import _app_action_data
from .exceptions import DreameLawnMowerConnectionError
from .payload_utils import _json_safe


class _DreameLawnMowerClientAppMapsMixin:
    def _sync_get_app_maps(
        self,
        chunk_size: int = 400,
        include_payload: bool = False,
        include_objects: bool = True,
        include_object_urls: bool = False,
    ) -> dict[str, Any]:
        """Keep each MAPI-selected download isolated from other map readers."""
        with self._app_map_download_lock:
            return self._sync_get_app_maps_locked(
                chunk_size, include_payload, include_objects, include_object_urls
            )

    def _sync_get_app_maps_locked(
        self,
        chunk_size: int,
        include_payload: bool,
        include_objects: bool,
        include_object_urls: bool,
    ) -> dict[str, Any]:
        chunk_size = _validate_app_map_chunk_size(chunk_size)
        map_list_result = self._sync_call_app_action({"m": "g", "t": "MAPL"})
        map_entries = _normalize_app_map_entries(map_list_result)
        map_list_valid = _app_map_entries_are_valid(
            map_list_result,
            map_entries,
        )
        result: dict[str, Any] = {
            "source": "app_action_map",
            "available": False,
            "map_list_valid": map_list_valid,
            "map_count": len(map_entries),
            "created_map_count": sum(
                1 for entry in map_entries if entry.get("created") is not False
            ),
            "current_map_index": None,
            "raw_map_list": _json_safe(map_list_result, max_depth=5),
            "maps": [],
            "errors": [],
        }
        for entry in map_entries:
            if (
                map_list_valid
                and entry.get("created") is not False
                and entry.get("current")
            ):
                result["current_map_index"] = entry["idx"]
            if not entry.get("created"):
                result["maps"].append(entry)
                continue

            map_result = dict(entry)
            try:
                for attempt in range(2):
                    try:
                        self._sync_download_app_map(
                            map_result,
                            chunk_size=chunk_size,
                            include_payload=include_payload,
                        )
                        map_result["download_attempts"] = attempt + 1
                        break
                    except (DreameLawnMowerConnectionError, ValueError) as err:
                        if attempt:
                            raise
                        # MAPI selects the MAPD cursor. Start the entire read
                        # again after an incomplete/mixed snapshot, not just its
                        # last chunk; the mobile app may also be reading maps.
                        map_result["retry_reason"] = str(err)
            except Exception as err:  # noqa: BLE001 - probes keep per-map evidence
                map_result["available"] = False
                map_result["error"] = str(err)
                result["errors"].append({"idx": entry.get("idx"), "error": str(err)})

            result["maps"].append(map_result)

        result["available"] = any(
            isinstance(item, Mapping) and bool(item.get("available"))
            for item in result["maps"]
        )
        self._sync_update_app_map_inventory_identity(result["maps"])
        if include_objects:
            try:
                result["objects"] = self._sync_get_app_map_objects(
                    include_urls=include_object_urls,
                )
            except Exception as err:  # noqa: BLE001 - object metadata is diagnostic
                result["objects"] = {"error": str(err)}
        return result

    def _sync_download_app_map(
        self,
        entry: dict[str, Any],
        *,
        chunk_size: int,
        include_payload: bool,
    ) -> None:
        """Read and verify one selected map before exposing its geometry."""
        info_result = self._sync_call_app_action(
            {"m": "g", "t": "MAPI", "d": {"idx": entry["idx"]}}
        )
        info = _app_action_data(info_result)
        entry["info"] = _json_safe(info, max_depth=4)
        size = info.get("size") if isinstance(info, Mapping) else None
        expected_hash = info.get("hash") if isinstance(info, Mapping) else None
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise DreameLawnMowerConnectionError("map_info_missing_size")
        if isinstance(info.get("idx"), int) and info["idx"] != entry["idx"]:
            raise DreameLawnMowerConnectionError("App map metadata index mismatch.")
        entry["reported_size"] = size
        payload_text, chunk_count, received_size = self._sync_get_app_map_text(
            size=size, chunk_size=chunk_size
        )
        encoded = payload_text.encode("utf-8")
        payload_hash = hashlib.md5(encoded).hexdigest()
        hash_match = (
            expected_hash.lower() == payload_hash
            if isinstance(expected_hash, str)
            else None
        )
        entry.update(
            {
                "received_size": received_size,
                "decoded_size": len(encoded),
                "chunk_count": chunk_count,
                "md5": payload_hash,
                "hash_match": hash_match,
            }
        )
        if len(encoded) != size or received_size != size:
            raise DreameLawnMowerConnectionError("App map payload size mismatch.")
        if hash_match is False:
            raise DreameLawnMowerConnectionError("App map payload hash mismatch.")
        parsed_payload = json.loads(payload_text)
        if not isinstance(parsed_payload, Mapping):
            raise DreameLawnMowerConnectionError("App map payload must be an object.")
        entry.update(
            {
                "available": True,
                "payload_keys": sorted(str(key) for key in parsed_payload),
                "summary": _app_map_payload_summary(parsed_payload),
            }
        )
        if include_payload:
            entry["payload"] = _json_safe(parsed_payload, max_depth=12)

    def _sync_get_app_map_text(
        self,
        *,
        size: int,
        chunk_size: int,
    ) -> tuple[str, int, int]:
        chunks = bytearray()
        offset = 0
        chunk_count = 0
        while offset < size:
            requested_size = min(size - offset, chunk_size)
            chunk_result = self._sync_call_app_action(
                {
                    "m": "g",
                    "t": "MAPD",
                    "d": {"start": offset, "size": requested_size},
                }
            )
            data = _app_action_data(chunk_result)
            if not isinstance(data, Mapping):
                raise DreameLawnMowerConnectionError(
                    f"MAPD returned invalid chunk at offset {offset}."
                )
            text = data.get("data")
            returned_size = data.get("size")
            if not isinstance(text, str) or text == "":
                raise DreameLawnMowerConnectionError(
                    f"MAPD returned empty data at offset {offset}."
                )
            chunk_bytes = text.encode("utf-8")
            actual_size = len(chunk_bytes)
            if actual_size > requested_size:
                raise DreameLawnMowerConnectionError(
                    f"MAPD returned too much data at offset {offset}."
                )
            if returned_size is not None and (
                not isinstance(returned_size, int)
                or isinstance(returned_size, bool)
                or returned_size != actual_size
            ):
                raise DreameLawnMowerConnectionError(
                    f"MAPD chunk size mismatch at offset {offset}."
                )
            chunks.extend(chunk_bytes)
            offset += actual_size
            chunk_count += 1
        return chunks.decode("utf-8"), chunk_count, offset
