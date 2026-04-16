"""Brute-force mower property scanner for app-style `iotstatus/props` probes."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence

from dreame_lawn_mower_client import DreameLawnMowerClient


def _parse_csv_numbers(raw: str, *, default: Sequence[int]) -> list[int]:
    values = [item.strip() for item in raw.split(",") if item.strip()]
    if not values:
        return list(default)
    return [int(item) for item in values]


def _property_keys_from_env() -> list[str]:
    raw = os.environ.get("DREAME_PROP_KEYS", "")
    explicit = [item.strip() for item in raw.split(",") if item.strip()]
    if explicit:
        return explicit

    siids = _parse_csv_numbers(
        os.environ.get("DREAME_PROP_SIIDS", ""),
        default=(1, 2, 3, 4, 5, 6, 7, 8),
    )
    piid_start = int(os.environ.get("DREAME_PROP_PIID_START", "1"))
    piid_end = int(os.environ.get("DREAME_PROP_PIID_END", "25"))
    if piid_end < piid_start:
        raise ValueError("DREAME_PROP_PIID_END must be >= DREAME_PROP_PIID_START")

    return [
        f"{siid}.{piid}"
        for siid in siids
        for piid in range(piid_start, piid_end + 1)
    ]


async def main() -> None:
    username = os.environ["DREAME_USERNAME"]
    password = os.environ["DREAME_PASSWORD"]
    country = os.environ.get("DREAME_COUNTRY", "eu")
    account_type = os.environ.get("DREAME_ACCOUNT_TYPE", "dreame")
    language = os.environ.get("DREAME_PROP_LANG", "en")
    only_values = os.environ.get("DREAME_PROP_ONLY_VALUES", "1") != "0"

    devices = await DreameLawnMowerClient.async_discover_devices(
        username=username,
        password=password,
        country=country,
        account_type=account_type,
    )
    if not devices:
        raise RuntimeError("No mower devices found.")

    keys = _property_keys_from_env()
    client = DreameLawnMowerClient(
        username=username,
        password=password,
        country=country,
        account_type=account_type,
        descriptor=devices[0],
    )
    try:
        print("Descriptor:", devices[0].title)
        print(f"Generated key count: {len(keys)}")
        result = await client.async_scan_cloud_properties(
            keys=keys,
            language=language,
            only_values=only_values,
        )
        print(__import__("json").dumps(result, indent=2, sort_keys=True))
    finally:
        await client.async_close()


if __name__ == "__main__":
    asyncio.run(main())
