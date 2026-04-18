"""Read-only probe for Dreame mower remote-control support."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

from dreame_lawn_mower_client import (
    DreameLawnMowerClient,
    remote_control_block_reason,
    remote_control_state_safe,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only probe for Dreame mower remote-control support."
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


def _support_probe_payload(snapshot: Any, support: Any) -> dict[str, Any]:
    """Return a compact read-only support payload."""
    return {
        "device": snapshot.descriptor.title,
        "state": snapshot.state,
        "activity": snapshot.activity,
        "battery_level": snapshot.battery_level,
        "manual_drive_safe": remote_control_state_safe(snapshot),
        "manual_drive_block_reason": remote_control_block_reason(snapshot),
        "remote_control_support": support.as_dict(),
    }


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
        support = await client.async_get_remote_control_support()
        payload = _support_probe_payload(snapshot, support)
        rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        if args.out:
            args.out.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
    finally:
        await client.async_close()


if __name__ == "__main__":
    asyncio.run(main())
