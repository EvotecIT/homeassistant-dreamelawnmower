"""Read-only probe for mower-native app schedules."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from dreame_lawn_mower_client import DreameLawnMowerClient


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only Dreame mower schedule probe using app actions."
    )
    parser.add_argument(
        "--include-raw",
        action="store_true",
        help="Include raw schedule JSON strings. Defaults to decoded summaries only.",
    )
    parser.add_argument(
        "--map-indices",
        default="",
        help=(
            "Optional comma-separated map indices to probe. By default the probe "
            "uses the app map list plus the default schedule slot."
        ),
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


def _parse_indices(value: str) -> list[int] | None:
    values = [item.strip() for item in value.split(",") if item.strip()]
    if not values:
        return None
    return [int(item) for item in values]


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
        await client.async_refresh()
        result = await client.async_get_app_schedules(
            include_raw=args.include_raw,
            map_indices=_parse_indices(args.map_indices),
        )
        result["descriptor"] = {
            "title": devices[args.device_index].title,
            "model": devices[args.device_index].model,
            "display_model": devices[args.device_index].display_model,
            "device_index": args.device_index,
        }
        rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
        if args.out:
            args.out.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
    finally:
        await client.async_close()


if __name__ == "__main__":
    asyncio.run(main())
