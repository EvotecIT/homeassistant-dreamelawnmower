"""Brute-force mower property scanner for app-style `iotstatus/props` probes."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Sequence
from pathlib import Path

from dreame_lawn_mower_client import DreameLawnMowerClient


def _parse_csv_numbers(raw: str | None, *, default: Sequence[int]) -> list[int]:
    raw = raw or ""
    values = [item.strip() for item in raw.split(",") if item.strip()]
    if not values:
        return list(default)
    return [int(item) for item in values]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only scanner for app-style Dreame cloud properties."
    )
    parser.add_argument(
        "--keys",
        default=os.environ.get("DREAME_PROP_KEYS", ""),
        help="Comma-separated explicit siid.piid keys to query.",
    )
    parser.add_argument(
        "--siids",
        default=os.environ.get("DREAME_PROP_SIIDS", ""),
        help="Comma-separated siids to scan when --keys is omitted.",
    )
    parser.add_argument(
        "--piid-start",
        type=int,
        default=int(os.environ.get("DREAME_PROP_PIID_START", "1")),
        help="First piid to scan when --keys is omitted.",
    )
    parser.add_argument(
        "--piid-end",
        type=int,
        default=int(os.environ.get("DREAME_PROP_PIID_END", "25")),
        help="Last piid to scan when --keys is omitted.",
    )
    parser.add_argument(
        "--language",
        default=os.environ.get("DREAME_PROP_LANG", "en"),
        help="Language used for cloud key-definition labels.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Include empty key-only responses. Defaults to non-empty values only.",
    )
    parser.add_argument(
        "--device-index",
        type=int,
        default=0,
        help="Zero-based discovered mower index to scan.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Optional JSON output file. Prints to stdout when omitted.",
    )
    return parser


def _property_keys(args: argparse.Namespace) -> list[str]:
    raw = args.keys or ""
    explicit = [item.strip() for item in raw.split(",") if item.strip()]
    if explicit:
        return explicit

    siids = _parse_csv_numbers(
        args.siids,
        default=(1, 2, 3, 4, 5, 6, 7, 8),
    )
    if args.piid_end < args.piid_start:
        raise ValueError("--piid-end must be >= --piid-start")

    return [
        f"{siid}.{piid}"
        for siid in siids
        for piid in range(args.piid_start, args.piid_end + 1)
    ]


async def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    username = os.environ["DREAME_USERNAME"]
    password = os.environ["DREAME_PASSWORD"]
    country = os.environ.get("DREAME_COUNTRY", "eu")
    account_type = os.environ.get("DREAME_ACCOUNT_TYPE", "dreame")
    only_values = (
        not args.all and os.environ.get("DREAME_PROP_ONLY_VALUES", "1") != "0"
    )

    devices = await DreameLawnMowerClient.async_discover_devices(
        username=username,
        password=password,
        country=country,
        account_type=account_type,
    )
    if not devices:
        raise RuntimeError("No mower devices found.")
    if args.device_index < 0 or args.device_index >= len(devices):
        raise RuntimeError(
            f"Invalid device index {args.device_index}; found {len(devices)}."
        )

    keys = _property_keys(args)
    client = DreameLawnMowerClient(
        username=username,
        password=password,
        country=country,
        account_type=account_type,
        descriptor=devices[args.device_index],
    )
    try:
        result = await client.async_scan_cloud_properties(
            keys=keys,
            language=args.language,
            only_values=only_values,
        )
        result["descriptor"] = {
            "title": devices[args.device_index].title,
            "model": devices[args.device_index].model,
            "display_model": devices[args.device_index].display_model,
            "device_index": args.device_index,
        }
        result["generated_key_count"] = len(keys)
        rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
        if args.out:
            args.out.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
    finally:
        await client.async_close()


if __name__ == "__main__":
    asyncio.run(main())
