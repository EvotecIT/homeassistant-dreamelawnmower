"""Safety-gated probe for Dreame mower photo metadata.

Default mode is read-only. Add --execute to call GET_PHOTO_INFO once. This does
not start video/audio streaming, remote control, or mowing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os

from dreame_lawn_mower_client import (
    DreameLawnMowerClient,
    DreameLawnMowerConnectionError,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Call GET_PHOTO_INFO once. Without this flag the script is read-only.",
    )
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
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
        snapshot = await client.async_refresh()
        support = await client.async_get_camera_feature_support()
        output: dict[str, object] = {
            "device": snapshot.descriptor.title,
            "state": snapshot.state,
            "activity": snapshot.activity,
            "camera_support": support.as_dict(),
            "executed": args.execute,
        }
        if args.execute:
            try:
                output["photo_info"] = await client.async_request_photo_info()
            except DreameLawnMowerConnectionError as err:
                output["photo_info_error"] = str(err)
        else:
            output["next_step"] = "Re-run with --execute to call GET_PHOTO_INFO once."
        print(json.dumps(output, indent=2, sort_keys=True))
    finally:
        await client.async_close()


if __name__ == "__main__":
    asyncio.run(main())
