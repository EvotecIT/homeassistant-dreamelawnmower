"""Minimal example for the reusable mower client."""

from __future__ import annotations

import asyncio
import os

from dreame_lawn_mower_client import DreameLawnMowerClient


async def main() -> None:
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
        print(snapshot.descriptor.title)
        print(snapshot.state_name)
        print(snapshot.battery_level)
    finally:
        await client.async_close()


if __name__ == "__main__":
    asyncio.run(main())

