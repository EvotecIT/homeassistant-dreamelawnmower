"""Brute-force mower property scanner for app-style `iotstatus/props` probes."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Sequence
from typing import Any

from dreame_lawn_mower_client import (
    MOWER_STATE_PROPERTY_KEY,
    DreameLawnMowerClient,
    mower_state_label,
)


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


def _normalize_prop_entries(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if isinstance(payload, dict):
        for key in ("data", "result", "records", "list"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _has_meaningful_value(entry: dict[str, Any]) -> bool:
    value = entry.get("value")
    if value not in (None, "", [], {}):
        return True

    for nested_key in ("values", "data", "raw", "content"):
        nested = entry.get(nested_key)
        if nested not in (None, "", [], {}):
            return True
    return False


def _render_entry(entry: dict[str, Any], *, language: str) -> dict[str, Any]:
    rendered = dict(entry)
    key = str(rendered.get("key", ""))

    if key == MOWER_STATE_PROPERTY_KEY:
        label = mower_state_label(rendered.get("value"), language=language)
        if label:
            rendered["decoded_label"] = label

    return rendered


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

        payload = await client.async_get_cloud_properties(keys)
        entries = _normalize_prop_entries(payload)
        if only_values:
            entries = [entry for entry in entries if _has_meaningful_value(entry)]

        rendered = [
            _render_entry(entry, language=language)
            for entry in sorted(
                entries,
                key=lambda item: str(item.get("key", "")),
            )
        ]

        print(
            json.dumps(
                {
                    "requested_key_count": len(keys),
                    "returned_entry_count": len(_normalize_prop_entries(payload)),
                    "displayed_entry_count": len(rendered),
                    "entries": rendered,
                },
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        await client.async_close()


if __name__ == "__main__":
    asyncio.run(main())
