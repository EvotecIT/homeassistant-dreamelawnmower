"""Read-only probe for the Dreame mower realtime/raw status blob."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from dreame_lawn_mower_client import DreameLawnMowerClient


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only probe for the Dreame mower realtime/raw status blob."
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=1,
        help="Number of status samples to capture.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Seconds to wait between samples.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Optional JSON output file. Prints to stdout when omitted.",
    )
    return parser


async def main() -> None:
    args = _build_parser().parse_args()
    if args.samples <= 0:
        raise RuntimeError("--samples must be greater than zero.")

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

    client = DreameLawnMowerClient(
        username=username,
        password=password,
        country=country,
        account_type=account_type,
        descriptor=devices[0],
    )
    try:
        output: dict[str, object] = {
            "device": devices[0].title,
            "sample_count": args.samples,
            "interval": args.interval,
            "samples": [],
        }
        samples = output["samples"]
        assert isinstance(samples, list)
        for index in range(args.samples):
            snapshot = await client.async_refresh()
            blob = await client.async_get_status_blob()
            samples.append(
                {
                    "index": index,
                    "state": snapshot.state,
                    "activity": snapshot.activity,
                    "battery_level": snapshot.battery_level,
                    "mowing": snapshot.mowing,
                    "returning": snapshot.returning,
                    "docked": snapshot.docked,
                    "status_blob": blob.as_dict() if blob else None,
                }
            )
            if index + 1 < args.samples:
                await asyncio.sleep(max(args.interval, 0))

        rendered = json.dumps(output, indent=2, sort_keys=True) + "\n"
        if args.out:
            args.out.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
    finally:
        await client.async_close()


if __name__ == "__main__":
    asyncio.run(main())
