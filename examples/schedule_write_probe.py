"""Dry-run first probe for mower-native schedule write requests."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from dreame_lawn_mower_client import DreameLawnMowerClient


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build, and optionally execute, a Dreame mower schedule enable "
            "request using the app action protocol."
        )
    )
    parser.add_argument(
        "--map-index",
        type=int,
        required=True,
        help="Schedule map index to modify, for example 0 or -1.",
    )
    parser.add_argument(
        "--plan-id",
        type=int,
        required=True,
        help="Schedule plan id to enable or disable.",
    )
    state = parser.add_mutually_exclusive_group(required=True)
    state.add_argument("--enable", action="store_true", help="Enable the plan.")
    state.add_argument("--disable", action="store_true", help="Disable the plan.")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Send the write request. Defaults to dry-run.",
    )
    parser.add_argument(
        "--confirm-schedule-write",
        action="store_true",
        help="Required together with --execute before any schedule write is sent.",
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
        await client.async_refresh()
        result = await client.async_set_app_schedule_plan_enabled(
            map_index=args.map_index,
            plan_id=args.plan_id,
            enabled=args.enable,
            execute=args.execute,
            confirm_write=args.confirm_schedule_write,
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
