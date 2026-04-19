"""Read-only sampler for live Dreame mower task/status cloud properties."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dreame_lawn_mower_client import DreameLawnMowerClient

TASK_STATUS_KEYS = ("2.1", "2.2", "2.50", "2.51", "3.1", "5.106")


def _unique_values(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _entry_by_key(sample: dict[str, Any], key: str) -> dict[str, Any] | None:
    for entry in sample.get("entries", []):
        if isinstance(entry, dict) and str(entry.get("key")) == key:
            return entry
    return None


def _entry_value(entry: dict[str, Any] | None) -> Any:
    if not isinstance(entry, dict):
        return None
    return entry.get("value", entry.get("value_preview"))


def summarize_task_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Return compact evidence from repeated task property scans."""
    task_statuses = [
        entry["task_status"]
        for sample in samples
        if isinstance(entry := _entry_by_key(sample, "2.50"), dict)
        and isinstance(entry.get("task_status"), dict)
    ]
    states = [
        {
            "value": _entry_value(entry),
            "label": entry.get("decoded_label"),
        }
        for sample in samples
        if isinstance(entry := _entry_by_key(sample, "2.1"), dict)
    ]
    batteries = [
        _entry_value(entry)
        for sample in samples
        if isinstance(entry := _entry_by_key(sample, "3.1"), dict)
    ]
    unknown_keys: list[str] = []
    unknown_values: dict[str, list[Any]] = {}
    for sample in samples:
        for key in sample.get("unknown_non_empty_keys", []):
            if isinstance(key, str) and key not in unknown_keys:
                unknown_keys.append(key)
            if isinstance(key, str):
                unknown_values.setdefault(key, []).append(
                    _entry_value(_entry_by_key(sample, key))
                )

    return {
        "sample_count": len(samples),
        "states": _unique_values(states),
        "task_statuses": _unique_values(task_statuses),
        "battery_levels": _unique_values(batteries),
        "unknown_non_empty_keys": unknown_keys,
        "unknown_values": {
            key: _unique_values(values) for key, values in unknown_values.items()
        },
        "task_status_changed": len(_unique_values(task_statuses)) > 1,
        "state_changed": len(_unique_values(states)) > 1,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only sampler for live Dreame mower task/status properties."
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=5,
        help="Number of property scans to capture.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=10.0,
        help="Seconds to wait between samples.",
    )
    parser.add_argument(
        "--keys",
        default=",".join(TASK_STATUS_KEYS),
        help="Comma-separated property keys to sample.",
    )
    parser.add_argument(
        "--language",
        default="en",
        help="Language used for cloud key-definition labels.",
    )
    parser.add_argument(
        "--device-index",
        type=int,
        default=0,
        help="Zero-based discovered mower index to sample.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Optional JSON output file. Prints to stdout when omitted.",
    )
    return parser


async def main() -> None:
    args = _build_parser().parse_args()
    if args.samples <= 0:
        raise RuntimeError("--samples must be greater than zero.")

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
    if args.device_index < 0 or args.device_index >= len(devices):
        raise RuntimeError(
            f"Invalid device index {args.device_index}; found {len(devices)}."
        )

    keys = [item.strip() for item in args.keys.split(",") if item.strip()]
    client = DreameLawnMowerClient(
        username=username,
        password=password,
        country=country,
        account_type=account_type,
        descriptor=devices[args.device_index],
    )
    try:
        samples: list[dict[str, Any]] = []
        for index in range(args.samples):
            scan = await client.async_scan_cloud_properties(
                keys=keys,
                language=args.language,
                only_values=True,
            )
            samples.append(
                {
                    "index": index,
                    "captured_at": datetime.now(UTC).isoformat(),
                    "entries": scan.get("entries", []),
                    "unknown_non_empty_keys": (
                        scan.get("summary", {}).get("unknown_non_empty_keys", [])
                    ),
                }
            )
            if index + 1 < args.samples:
                await asyncio.sleep(max(args.interval, 0))

        output = {
            "device": devices[args.device_index].title,
            "keys": keys,
            "sample_count": args.samples,
            "interval": args.interval,
            "samples": samples,
            "summary": summarize_task_samples(samples),
        }
        rendered = json.dumps(output, indent=2, sort_keys=True) + "\n"
        if args.out:
            args.out.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
    finally:
        await client.async_close()


if __name__ == "__main__":
    asyncio.run(main())
