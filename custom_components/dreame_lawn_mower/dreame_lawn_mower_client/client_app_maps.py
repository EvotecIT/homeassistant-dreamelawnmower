"""Synchronous adapter for the canonical app-map read plan."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .app_read_transport import run_app_read
from .map_read_plan import download_map, read_map_text, read_maps


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
                chunk_size,
                include_payload,
                include_objects,
                include_object_urls,
            )

    def _sync_get_app_maps_locked(
        self,
        chunk_size: int,
        include_payload: bool,
        include_objects: bool,
        include_object_urls: bool,
    ) -> dict[str, Any]:
        result = run_app_read(
            read_maps(self._app_map_payload_cache, chunk_size, include_payload),
            self._dispatch_map_read,
        )
        self._sync_update_app_map_inventory_identity(result["maps"])
        if include_objects:
            try:
                result["objects"] = self._sync_get_app_map_objects(
                    include_urls=include_object_urls
                )
            except Exception as err:  # noqa: BLE001 - diagnostic evidence
                result["objects"] = {"error": str(err)}
        return result

    def _dispatch_map_read(
        self, action: Mapping[str, Any], **kwargs: Any,
    ) -> Any:
        # Preserve the established synchronous dispatch defaults and overrides.
        return self._sync_call_app_action(action)

    def _sync_download_app_map(
        self,
        entry: dict[str, Any],
        *,
        chunk_size: int,
        include_payload: bool,
    ) -> None:
        run_app_read(
            download_map(
                self._app_map_payload_cache,
                entry,
                chunk_size=chunk_size,
                include_payload=include_payload,
            ),
            self._dispatch_map_read,
        )

    def _sync_get_app_map_text(
        self, *, size: int, chunk_size: int
    ) -> tuple[str, int, int]:
        return run_app_read(
            read_map_text(size=size, chunk_size=chunk_size), self._dispatch_map_read
        )
