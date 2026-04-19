"""Read-only probe for mower-native app map payloads.

By default this downloads and parses map JSON, but omits raw coordinates from
output. Use --include-payload only for local parser/rendering work.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from dreame_lawn_mower_client import DreameLawnMowerClient

_DEFAULT_OBJECT_DOWNLOAD_USER_AGENT = "DreameHomeMapProbe/1.0"


def summarize_app_map_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a compact, raw-coordinate-free summary of app map probe output."""
    maps = [item for item in payload.get("maps", []) if isinstance(item, dict)]
    current_map = next((item for item in maps if item.get("current") is True), None)
    objects = payload.get("objects")
    object_count = None
    if isinstance(objects, dict):
        object_count = objects.get("object_count")
    object_download_probe = payload.get("object_download_probe")
    object_download_success_count = None
    object_download_statuses = None
    if isinstance(object_download_probe, dict):
        object_download_success_count = object_download_probe.get("success_count")
        statuses: dict[str, int] = {}
        for item in object_download_probe.get("objects", []):
            if not isinstance(item, dict):
                continue
            for variant in item.get("variants", []):
                if not isinstance(variant, dict):
                    continue
                for request in variant.get("requests", []):
                    if not isinstance(request, dict):
                        continue
                    status = request.get("status")
                    if isinstance(status, int):
                        statuses[str(status)] = statuses.get(str(status), 0) + 1
        object_download_statuses = statuses or None

    return {
        "available": payload.get("available"),
        "source": payload.get("source"),
        "map_count": payload.get("map_count", len(maps)),
        "current_map_index": payload.get("current_map_index"),
        "current_map_summary": (
            current_map.get("summary") if isinstance(current_map, dict) else None
        ),
        "object_count": object_count,
        "object_download_success_count": object_download_success_count,
        "object_download_statuses": object_download_statuses,
        "errors": payload.get("errors", []),
    }


def probe_app_map_object_downloads(
    payload: dict[str, Any],
    *,
    timeout: float = 10,
    user_agent: str = _DEFAULT_OBJECT_DOWNLOAD_USER_AGENT,
    fetcher: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Probe 3D object URLs without returning the signed URLs themselves."""
    objects_payload = payload.get("objects")
    objects = (
        objects_payload.get("objects")
        if isinstance(objects_payload, dict)
        else None
    )
    if not isinstance(objects, list):
        objects = []

    fetch = fetcher or _fetch_url_metadata
    results: list[dict[str, Any]] = []
    success_count = 0
    for raw_item in objects:
        if not isinstance(raw_item, dict):
            continue
        name = str(raw_item.get("name", ""))
        url = raw_item.get("url")
        item: dict[str, Any] = {
            "name": name,
            "extension": raw_item.get("extension"),
            "url_present": bool(url),
            "variants": [],
        }
        if not isinstance(url, str) or not url:
            item["error"] = "missing_url"
            results.append(item)
            continue

        object_successful = False
        for variant_name, variant_url in _object_url_variants(url):
            requests = [
                fetch(
                    variant_url,
                    method="HEAD",
                    timeout=timeout,
                    user_agent=user_agent,
                ),
                fetch(
                    variant_url,
                    method="GET",
                    timeout=timeout,
                    user_agent=user_agent,
                    byte_range="bytes=0-1023",
                ),
            ]
            if any(_request_metadata_successful(request) for request in requests):
                object_successful = True
            item["variants"].append(
                {
                    "variant": variant_name,
                    "requests": requests,
                }
            )
        if object_successful:
            success_count += 1
        results.append(item)

    return {
        "attempted": bool(results),
        "object_count": len(results),
        "success_count": success_count,
        "objects": results,
        "user_agent": user_agent,
    }


def redact_object_urls(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove signed object URLs from an app map probe payload in-place."""
    objects_payload = payload.get("objects")
    objects = (
        objects_payload.get("objects")
        if isinstance(objects_payload, dict)
        else None
    )
    if not isinstance(objects, list):
        return payload
    for item in objects:
        if isinstance(item, dict) and "url" in item:
            item.pop("url", None)
            item["url_redacted"] = True
    if isinstance(objects_payload, dict):
        objects_payload["urls_included"] = False
        objects_payload["urls_redacted"] = True
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only probe for mower-native app map payloads."
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=400,
        help="MAPD chunk size. The Dreamehome plugin uses 400.",
    )
    parser.add_argument(
        "--include-payload",
        action="store_true",
        help="Include raw parsed map coordinates in the output.",
    )
    parser.add_argument(
        "--include-object-urls",
        action="store_true",
        help="Include expiring 3D map object download URLs in the output.",
    )
    parser.add_argument(
        "--probe-object-downloads",
        action="store_true",
        help=(
            "Generate 3D object URLs internally and record sanitized HEAD/ranged "
            "GET results. Signed URLs are redacted unless --include-object-urls "
            "is also set."
        ),
    )
    parser.add_argument(
        "--object-download-timeout",
        type=float,
        default=10,
        help="Timeout in seconds for each sanitized object download request.",
    )
    parser.add_argument(
        "--object-download-user-agent",
        default=_DEFAULT_OBJECT_DOWNLOAD_USER_AGENT,
        help="User-Agent sent by --probe-object-downloads requests.",
    )
    parser.add_argument(
        "--skip-objects",
        action="store_true",
        help="Skip 3D map object metadata.",
    )
    parser.add_argument(
        "--device-index",
        type=int,
        default=0,
        help="Zero-based discovered mower index to probe.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Optional JSON output file. Prints to stdout when omitted.",
    )
    return parser


def _object_url_variants(url: str) -> list[tuple[str, str]]:
    return [
        ("direct", url),
        ("with_current", _add_current_query_param(url)),
    ]


def _add_current_query_param(url: str) -> str:
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query.append(("current", str(int(time.time() * 1000))))
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def _fetch_url_metadata(
    url: str,
    *,
    method: str,
    timeout: float,
    user_agent: str,
    byte_range: str | None = None,
) -> dict[str, Any]:
    headers = {
        "Accept": "*/*",
        "User-Agent": user_agent,
    }
    if byte_range:
        headers["Range"] = byte_range
    request = Request(url, method=method, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = response.read(1024 if method == "GET" else 0)
            return _response_metadata(
                method=method,
                byte_range=byte_range,
                status=response.status,
                headers=response.headers,
                bytes_read=len(body),
                error=None,
            )
    except HTTPError as err:
        body = err.read(256 if method == "GET" else 0)
        return _response_metadata(
            method=method,
            byte_range=byte_range,
            status=err.code,
            headers=err.headers,
            bytes_read=len(body),
            error="http_error",
        )
    except (TimeoutError, URLError, OSError) as err:
        return {
            "method": method,
            "range": byte_range,
            "status": None,
            "error": type(err).__name__,
        }


def _response_metadata(
    *,
    method: str,
    byte_range: str | None,
    status: int,
    headers: Any,
    bytes_read: int,
    error: str | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "method": method,
        "range": byte_range,
        "status": status,
        "content_type": headers.get("Content-Type"),
        "content_length": _int_or_none(headers.get("Content-Length")),
        "accept_ranges": headers.get("Accept-Ranges"),
        "etag_present": bool(headers.get("ETag")),
        "bytes_read": bytes_read,
    }
    if error:
        result["error"] = error
    return result


def _request_metadata_successful(request: dict[str, Any]) -> bool:
    status = request.get("status")
    return isinstance(status, int) and 200 <= status < 300


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    username = os.environ["DREAME_USERNAME"]
    password = os.environ["DREAME_PASSWORD"]
    country = os.environ.get("DREAME_COUNTRY", "eu")
    account_type = os.environ.get("DREAME_ACCOUNT_TYPE", "dreame")

    devices = await DreameLawnMowerClient.async_discover_devices(
        username=username,
        password=password,
        country=country,
        account_type=account_type,
    )
    if not devices:
        raise RuntimeError("No mower devices found.")
    if args.device_index < 0 or args.device_index >= len(devices):
        raise RuntimeError(
            f"Invalid device index {args.device_index}; found {len(devices)}."
        )

    client = DreameLawnMowerClient(
        username=username,
        password=password,
        country=country,
        account_type=account_type,
        descriptor=devices[args.device_index],
    )
    try:
        payload = await client.async_get_app_maps(
            chunk_size=args.chunk_size,
            include_payload=args.include_payload,
            include_objects=not args.skip_objects,
            include_object_urls=(
                args.include_object_urls or args.probe_object_downloads
            ),
        )
        if args.probe_object_downloads:
            payload["object_download_probe"] = probe_app_map_object_downloads(
                payload,
                timeout=args.object_download_timeout,
                user_agent=args.object_download_user_agent,
            )
            if not args.include_object_urls:
                redact_object_urls(payload)
        payload["probe_summary"] = summarize_app_map_payload(payload)
        rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        if args.out:
            args.out.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
    finally:
        await client.async_close()


if __name__ == "__main__":
    asyncio.run(main())
