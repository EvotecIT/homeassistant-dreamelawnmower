"""Read-only probe for Dreame's debug/manual OTA catalog."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from dreame_lawn_mower_client import DreameLawnMowerClient


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only probe for Dreame's debug/manual OTA catalog."
    )
    parser.add_argument(
        "--include-raw",
        action="store_true",
        help="Include the raw catalog payload in addition to the summary.",
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
        snapshot = await client.async_refresh()
        catalog = await client.async_get_debug_ota_catalog(
            current_version=snapshot.firmware_version,
            include_raw=args.include_raw,
        )
        payload = {
            "descriptor": {
                "name": devices[args.device_index].name,
                "model": devices[args.device_index].model,
                "display_model": devices[args.device_index].display_model,
            },
            "current_firmware_version": snapshot.firmware_version,
            "debug_ota_catalog": catalog,
        }
        rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        if args.out:
            args.out.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
    finally:
        await client.async_close()


if __name__ == "__main__":
    asyncio.run(main())
