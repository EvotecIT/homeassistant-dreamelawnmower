"""Read-only probe for the Dreame mower realtime/raw status blob."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from dreame_lawn_mower_client import DreameLawnMowerClient


def _unique_values(values: list[object]) -> list[object]:
    result: list[object] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _summarize_samples(samples: list[dict[str, object]]) -> dict[str, object]:
    """Return compact transition evidence from status blob samples."""
    states = _unique_values([sample.get("state") for sample in samples])
    activities = _unique_values([sample.get("activity") for sample in samples])
    mowing_flags = _unique_values([sample.get("mowing") for sample in samples])
    returning_flags = _unique_values([sample.get("returning") for sample in samples])
    docked_flags = _unique_values([sample.get("docked") for sample in samples])
    battery_levels = _unique_values(
        [sample.get("battery_level") for sample in samples]
    )
    candidate_battery_levels = _unique_values(
        [
            (sample.get("status_blob") or {}).get("candidate_battery_level")
            for sample in samples
            if isinstance(sample.get("status_blob"), dict)
        ]
    )
    compared_candidates = [
        (
            sample.get("battery_level"),
            (sample.get("status_blob") or {}).get("candidate_battery_level"),
        )
        for sample in samples
        if isinstance(sample.get("battery_level"), int)
        and isinstance(sample.get("status_blob"), dict)
        and isinstance(
            (sample.get("status_blob") or {}).get("candidate_battery_level"),
            int,
        )
    ]

    byte_values: dict[str, list[int]] = {}
    hex_values: list[str] = []
    for sample in samples:
        blob = sample.get("status_blob")
        if not isinstance(blob, dict):
            continue
        hex_value = blob.get("hex")
        if isinstance(hex_value, str):
            hex_values.append(hex_value)
        bytes_by_index = blob.get("bytes_by_index")
        if not isinstance(bytes_by_index, dict):
            continue
        for key, value in bytes_by_index.items():
            if isinstance(value, int):
                byte_values.setdefault(str(key), []).append(value)

    changed_bytes = [
        {
            "index": int(key),
            "values": _unique_values(values),
        }
        for key, values in sorted(byte_values.items(), key=lambda item: int(item[0]))
        if len(_unique_values(values)) > 1
    ]

    return {
        "sample_count": len(samples),
        "states": states,
        "activities": activities,
        "mowing_flags": mowing_flags,
        "returning_flags": returning_flags,
        "docked_flags": docked_flags,
        "battery_levels": battery_levels,
        "candidate_battery_levels": candidate_battery_levels,
        "candidate_battery_matches_snapshot": (
            all(snapshot == candidate for snapshot, candidate in compared_candidates)
            if compared_candidates
            else None
        ),
        "unique_status_blob_hex_count": len(_unique_values(hex_values)),
        "changed_byte_indices": changed_bytes,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only probe for the Dreame mower realtime/raw status blob."
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=1,
        help="Number of status samples to capture.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Seconds to wait between samples.",
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

    client = DreameLawnMowerClient(
        username=username,
        password=password,
        country=country,
        account_type=account_type,
        descriptor=devices[0],
    )
    try:
        output: dict[str, object] = {
            "device": devices[0].title,
            "sample_count": args.samples,
            "interval": args.interval,
            "samples": [],
        }
        samples = output["samples"]
        assert isinstance(samples, list)
        for index in range(args.samples):
            snapshot = await client.async_refresh()
            blob = await client.async_get_status_blob()
            samples.append(
                {
                    "index": index,
                    "state": snapshot.state,
                    "activity": snapshot.activity,
                    "battery_level": snapshot.battery_level,
                    "mowing": snapshot.mowing,
                    "returning": snapshot.returning,
                    "docked": snapshot.docked,
                    "status_blob": blob.as_dict() if blob else None,
                }
            )
            if index + 1 < args.samples:
                await asyncio.sleep(max(args.interval, 0))
        output["summary"] = _summarize_samples(samples)

        rendered = json.dumps(output, indent=2, sort_keys=True) + "\n"
        if args.out:
            args.out.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
    finally:
        await client.async_close()


if __name__ == "__main__":
    asyncio.run(main())
