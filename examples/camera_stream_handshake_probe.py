"""Safety-gated probe for the Dreame mower camera stream handshake.

Default mode is read-only. Add --execute to try a short monitor start/end
handshake. This does not start mowing, audio, or remote control.
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
        help="Start and then end a short camera monitor handshake.",
    )
    parser.add_argument("--timeout", type=float, default=6.0)
    parser.add_argument("--interval", type=float, default=0.75)
    parser.add_argument(
        "--payload-mode",
        choices=("with_session", "no_session", "empty_session"),
        default="with_session",
        help="STREAM_VIDEO payload shape to test.",
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
            "executed": args.execute,
            "camera_support": support.as_dict(),
        }
        if args.execute:
            try:
                output["handshake"] = await client.async_probe_camera_stream_handshake(
                    timeout=args.timeout,
                    interval=args.interval,
                    payload_mode=args.payload_mode,
                )
            except DreameLawnMowerConnectionError as err:
                output["handshake_error"] = str(err)
        else:
            output["next_step"] = (
                "Re-run with --execute to try a short monitor stream handshake."
            )
        print(json.dumps(output, indent=2, sort_keys=True))
    finally:
        await client.async_close()


if __name__ == "__main__":
    asyncio.run(main())
