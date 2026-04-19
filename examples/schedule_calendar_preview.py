"""Preview Home Assistant calendar events from mower-native schedules."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from homeassistant.util import dt as dt_util

from custom_components.dreame_lawn_mower.calendar import schedule_calendar_events
from dreame_lawn_mower_client import DreameLawnMowerClient


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Render decoded Dreame mower schedules as Home Assistant-style "
            "calendar events."
        )
    )
    parser.add_argument(
        "--from-file",
        type=Path,
        help=(
            "Read an existing schedule_probe JSON file instead of contacting "
            "the cloud."
        ),
    )
    parser.add_argument(
        "--timezone",
        default=os.environ.get("TZ", "UTC"),
        help="IANA time zone for standalone preview output. Defaults to TZ or UTC.",
    )
    parser.add_argument(
        "--start",
        help="Inclusive ISO datetime start. Defaults to now in --timezone.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=14,
        help="Number of days to preview from --start. Defaults to 14.",
    )
    parser.add_argument(
        "--include-all-schedules",
        action="store_true",
        help=(
            "Include every decoded schedule slot. By default the preview "
            "matches Home Assistant and uses the SCHDT active schedule version "
            "when available."
        ),
    )
    parser.add_argument(
        "--map-indices",
        default="",
        help=(
            "Optional comma-separated map indices for live fetches. Ignored "
            "with --from-file."
        ),
    )
    parser.add_argument(
        "--device-index",
        type=int,
        default=0,
        help="Zero-based discovered mower index for live fetches.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Optional JSON output file. Prints to stdout when omitted.",
    )
    return parser


def _parse_indices(value: str) -> list[int] | None:
    values = [item.strip() for item in value.split(",") if item.strip()]
    if not values:
        return None
    return [int(item) for item in values]


def _parse_start(value: str | None, timezone: ZoneInfo) -> datetime:
    if not value:
        return datetime.now(timezone)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)


def _descriptor_title(payload: dict[str, object]) -> str | None:
    descriptor = payload.get("descriptor")
    if isinstance(descriptor, dict):
        title = descriptor.get("title")
        return str(title) if title else None
    return None


async def _fetch_payload(args: argparse.Namespace) -> dict[str, object]:
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

    client = DreameLawnMowerClient(
        username=username,
        password=password,
        country=country,
        account_type=account_type,
        descriptor=devices[args.device_index],
    )
    try:
        await client.async_refresh()
        payload = await client.async_get_app_schedules(
            map_indices=_parse_indices(args.map_indices),
        )
        payload["descriptor"] = {
            "title": devices[args.device_index].title,
            "model": devices[args.device_index].model,
            "display_model": devices[args.device_index].display_model,
            "device_index": args.device_index,
        }
        return payload
    finally:
        await client.async_close()


async def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if args.days <= 0:
        raise ValueError("--days must be greater than zero.")

    timezone = ZoneInfo(args.timezone)
    dt_util.set_default_time_zone(timezone)
    start = _parse_start(args.start, timezone)
    end = start + timedelta(days=args.days)

    if args.from_file:
        payload = json.loads(args.from_file.read_text(encoding="utf-8"))
        source = str(args.from_file)
    else:
        payload = await _fetch_payload(args)
        source = "live"

    events = schedule_calendar_events(
        payload,
        start,
        end,
        include_all_schedules=args.include_all_schedules,
        mower_name=_descriptor_title(payload),
    )
    result = {
        "source": source,
        "timezone": args.timezone,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "event_count": len(events),
        "events": [event.as_dict() for event in events],
    }
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    asyncio.run(main())
