"""Dry-run first probe for mower-native maintenance counter resets."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from dreame_lawn_mower_client import MAINTENANCE_ITEMS, DreameLawnMowerClient


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build, and optionally execute, Dreame mower CMS maintenance "
            "counter resets using the app action protocol."
        )
    )
    parser.add_argument(
        "--item",
        action="append",
        choices=[item.key for item in MAINTENANCE_ITEMS],
        required=True,
        help="Maintenance item to reset. Repeat to reset several counters.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Send the reset request. Defaults to dry-run.",
    )
    parser.add_argument(
        "--confirm-maintenance-reset",
        action="store_true",
        help="Required together with --execute before any reset is sent.",
    )
    parser.add_argument(
        "--device-index",
        type=int,
        default=0,
        help="Zero-based discovered mower index to inspect.",
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
        before = await client.async_get_maintenance_status(include_raw=False)
        resets = []
        for item in args.item:
            resets.append(
                await client.async_plan_maintenance_reset(
                    item=item,
                    execute=args.execute,
                    confirm_write=args.confirm_maintenance_reset,
                )
            )
        after = await client.async_get_maintenance_status(include_raw=False)
        payload = {
            "descriptor": {
                "title": devices[args.device_index].title,
                "model": devices[args.device_index].model,
                "display_model": devices[args.device_index].display_model,
                "device_index": args.device_index,
            },
            "before": before,
            "resets": resets,
            "after": after,
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
