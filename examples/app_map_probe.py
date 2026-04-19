"""Read-only probe for mower-native app map payloads.

By default this downloads and parses map JSON, but omits raw coordinates from
output. Use --include-payload only for local parser/rendering work.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

from dreame_lawn_mower_client import DreameLawnMowerClient


def summarize_app_map_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a compact, raw-coordinate-free summary of app map probe output."""
    maps = [item for item in payload.get("maps", []) if isinstance(item, dict)]
    current_map = next((item for item in maps if item.get("current") is True), None)
    objects = payload.get("objects")
    object_count = None
    if isinstance(objects, dict):
        object_count = objects.get("object_count")

    return {
        "available": payload.get("available"),
        "source": payload.get("source"),
        "map_count": payload.get("map_count", len(maps)),
        "current_map_index": payload.get("current_map_index"),
        "current_map_summary": (
            current_map.get("summary") if isinstance(current_map, dict) else None
        ),
        "object_count": object_count,
        "errors": payload.get("errors", []),
    }


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
            include_object_urls=args.include_object_urls,
        )
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
