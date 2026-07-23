"""Generate and validate one mower point cloud through the public client."""

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
            "Generate one mower point cloud and print coordinate-free PCD metadata."
        )
    )
    parser.add_argument(
        "--device-index",
        type=int,
        default=0,
        help="Zero-based discovered mower index.",
    )
    parser.add_argument(
        "--map-index",
        type=int,
        default=0,
        help="Zero-based app map index.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Optional local .pcd output. Omit it to avoid persisting geometry.",
    )
    return parser


async def main() -> None:
    args = _build_parser().parse_args()
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
        download = await client.async_download_app_map_point_cloud(
            map_index=args.map_index
        )
        result = {
            "device": {
                "name": devices[args.device_index].name,
                "model": devices[args.device_index].display_model,
            },
            "map_index": download.map_index,
            "metadata": download.metadata.as_dict(),
            "saved_to": None,
        }
        if args.out is not None:
            if args.out.exists():
                raise RuntimeError(f"Refusing to overwrite existing file: {args.out}")
            args.out.write_bytes(download.content)
            result["saved_to"] = str(args.out)
        print(json.dumps(result, indent=2, sort_keys=True))
    finally:
        await client.async_close()


if __name__ == "__main__":
    asyncio.run(main())
