"""Read-only probe for Dreame mower map sources.

This combines the legacy current-map fetch with app-style cloud property probes
so map failures carry useful evidence instead of only "no image".
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
        payload = await client.async_probe_map_sources(timeout=6, interval=0.5)
        print(json.dumps(payload, indent=2, sort_keys=True))
    finally:
        await client.async_close()


if __name__ == "__main__":
    asyncio.run(main())
