"""Native reads for public catalog and cloud-advertised JSON documents."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .client_map_helpers import (
    _key_define_from_device_list_page,
    _key_define_from_mapping,
)
from .exceptions import DreameLawnMowerConnectionError

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession


async def async_read_key_definition(
    client: DreameLawnMowerClient, *, language: str | None,
    device_info: Mapping[str, Any] | None = None,
    device_list_page: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve the advertised definition and preserve its partial-result shape."""
    async def read(cloud: DreameCloudSession) -> dict[str, Any]:
        info = device_info or await client.async_get_cloud_device_info(
            language=language,
        )
        key_define = _key_define_from_mapping(info or {})
        source = "device_info"
        if not key_define.get("url"):
            try:
                page = device_list_page
                if page is None:
                    page = await client.async_get_cloud_device_list_page(
                        current=1, size=20, language=language, master=None,
                        shared_status=None,
                    )
            except DreameLawnMowerConnectionError:
                page = None
            fallback = _key_define_from_device_list_page(client._descriptor.did, page)
            if fallback.get("url"):
                key_define = fallback
                source = "device_list_v2"
        url = key_define.get("url")
        result: dict[str, Any] = {
            "url": url, "url_present": bool(url), "ver": key_define.get("ver"),
            "source": source if url else None, "fetched": False,
            "payload": None, "error": None,
        }
        if not url:
            result["error"] = "key_define_url_missing"
            return result
        try:
            content = await cloud.async_get_public_file(
                str(url), deadline=time.monotonic() + 12, attempts=2, timeout=6,
            )
        except DreameLawnMowerConnectionError:
            result["error"] = "key_definition_fetch_failed"
            return result
        if not content:
            result["error"] = "key_definition_fetch_failed"
            return result
        try:
            result["payload"] = json.loads(content.decode("utf-8"))
            result["fetched"] = True
        except (UnicodeDecodeError, json.JSONDecodeError):
            result["error"] = "key_definition_parse_failed"
        return result

    # Discovery and file reads have their own deadlines; bound the composition too.
    try:
        async with asyncio.timeout(52):
            return await client._async_cloud_read(read)
    except TimeoutError:
        raise DreameLawnMowerConnectionError("Key definition read timed out") from None
