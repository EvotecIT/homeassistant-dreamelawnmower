"""Fetch Dreame's public status key definition for the configured mower."""

from __future__ import annotations

import asyncio
import json
import os

from dreame_lawn_mower_client import (
    DreameLawnMowerClient,
    build_cloud_key_definition_summary,
)


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
        payload = await client.async_get_cloud_key_definition(language="en")
        print(
            json.dumps(
                {
                    "summary": build_cloud_key_definition_summary(payload),
                    "key_definition": payload,
                },
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        await client.async_close()


if __name__ == "__main__":
    asyncio.run(main())
