"""Fetch Dreame's public status key definition for the configured mower."""

from __future__ import annotations

import asyncio
import json

from python_client import build_client_from_env

from dreame_lawn_mower_client import (
    build_cloud_key_definition_summary,
)


async def main() -> None:
    client = await build_client_from_env()
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
