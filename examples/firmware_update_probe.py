"""Read-only probe for Dreame mower firmware/update evidence.

This intentionally does not install or trigger firmware updates. It only
reports device/cloud metadata so we can identify the real OTA signal before
adding a Home Assistant update entity.
"""

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
        support = await client.async_get_firmware_update_support()
        output = {
            "device": snapshot.descriptor.title,
            "state": snapshot.state,
            "activity": snapshot.activity,
            "firmware_update": support.as_dict(),
        }
        print(json.dumps(output, indent=2, sort_keys=True))
    finally:
        await client.async_close()


if __name__ == "__main__":
    asyncio.run(main())
