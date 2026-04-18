"""Read-only probe for the Dreame mower realtime/raw status blob."""

from __future__ import annotations

import asyncio
import json
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
        blob = await client.async_get_status_blob()
        output = {
            "device": snapshot.descriptor.title,
            "state": snapshot.state,
            "activity": snapshot.activity,
            "battery_level": snapshot.battery_level,
            "status_blob": blob.as_dict() if blob else None,
        }
        print(json.dumps(output, indent=2, sort_keys=True))
    finally:
        await client.async_close()


if __name__ == "__main__":
    asyncio.run(main())
