"""Native property scanning and shared diagnostic result assembly."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from threading import Event
from typing import TYPE_CHECKING, Any

from .client_refresh import _run_state_worker
from .exceptions import DreameLawnMowerConnectionError
from .map_probe import build_cloud_property_summary

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .client_maps import _DreameLawnMowerClientMapsMixin
    from .cloud_session import DreameCloudSession


def cloud_property_scan_result(
    client: _DreameLawnMowerClientMapsMixin,
    requested_key_count: int,
    all_entries: list[dict[str, Any]],
    *,
    language: str,
    only_values: bool,
    key_definition: Mapping[str, Any] | None,
) -> dict[str, Any]:
    rendered = all_entries
    if only_values:
        rendered = [
            entry for entry in rendered if client._entry_has_meaningful_value(entry)
        ]

    rendered = [
        client._annotate_cloud_property_entry(
            entry,
            language=language,
            key_definition=key_definition,
            model=client._descriptor.model,
        )
        for entry in sorted(
            rendered,
            key=lambda item: str(item.get("key", "")),
        )
    ]
    result = {
        "requested_key_count": requested_key_count,
        "returned_entry_count": len(all_entries),
        "displayed_entry_count": len(rendered),
        "entries": rendered,
    }
    result["summary"] = build_cloud_property_summary(result)
    return result


async def async_scan_properties(
    client: DreameLawnMowerClient,
    *,
    keys: str | Sequence[str] | None,
    siids: Sequence[int] | None,
    piid_start: int,
    piid_end: int,
    chunk_size: int,
    language: str,
    only_values: bool,
    include_key_definition: bool,
    key_definition: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Own all chunks, optional metadata and final result work until completion."""
    cancelled = Event()
    normalized = client._build_cloud_property_keys(
        keys=keys,
        siids=siids,
        piid_start=piid_start,
        piid_end=piid_end,
    )

    async def read(_cloud: DreameCloudSession) -> dict[str, Any]:
        entries: list[dict[str, Any]] = []
        for offset in range(0, len(normalized), max(chunk_size, 1)):
            response = await client.async_get_cloud_properties(
                normalized[offset : offset + max(chunk_size, 1)],
            )
            entries.extend(client._normalize_cloud_property_entries(response))
        definition = key_definition
        if normalized and include_key_definition and definition is None:
            try:
                definition = await client.async_get_cloud_key_definition(
                    language=language
                )
            except DreameLawnMowerConnectionError:
                definition = None
        return await _run_state_worker(
            lambda: cloud_property_scan_result(
                client,
                len(normalized),
                entries,
                language=language,
                only_values=only_values,
                key_definition=definition,
            ),
            cancelled,
        )

    return await client._async_cloud_read(read)
