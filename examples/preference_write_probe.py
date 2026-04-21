"""Dry-run first probe for mower-native mowing preference updates."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from dreame_lawn_mower_client import DreameLawnMowerClient


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build, and optionally execute, a Dreame mower mowing-preference "
            "update from the current app preference payload."
        )
    )
    parser.add_argument(
        "--map-index",
        type=int,
        required=True,
        help="Preference map index to inspect, for example 0.",
    )
    parser.add_argument(
        "--area-id",
        type=int,
        help=(
            "Preference area id returned by the mower app protocol. Required "
            "for per-area settings updates, optional for preference-mode-only "
            "requests."
        ),
    )
    parser.add_argument(
        "--preference-mode",
        help="Map preference mode, for example global, custom, 0, or 1.",
    )
    parser.add_argument(
        "--efficient-mode",
        type=int,
        help="Efficiency mode integer, typically 0 standard or 1 efficient.",
    )
    parser.add_argument(
        "--mowing-height-cm",
        type=float,
        help="Target mowing height in centimeters.",
    )
    parser.add_argument(
        "--mowing-direction-mode",
        type=int,
        help="Direction mode integer, typically 0 none, 1 rotation, 2 checkerboard.",
    )
    parser.add_argument(
        "--mowing-direction-degrees",
        type=int,
        help="Direction angle between 0 and 180 degrees.",
    )
    parser.add_argument(
        "--edge-mowing-auto",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable or disable automatic edge mowing.",
    )
    parser.add_argument(
        "--edge-mowing-walk-mode",
        type=int,
        help="Edge walk mode integer, typically 0 line or 1 side.",
    )
    parser.add_argument(
        "--edge-mowing-obstacle-avoidance",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable or disable edge obstacle avoidance.",
    )
    parser.add_argument(
        "--cutter-position",
        type=int,
        help="Cutter position integer, typically 0 center or 1 left.",
    )
    parser.add_argument(
        "--edge-mowing-num",
        type=int,
        help="Edge mowing pass count.",
    )
    parser.add_argument(
        "--obstacle-avoidance-enabled",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable or disable obstacle avoidance.",
    )
    parser.add_argument(
        "--obstacle-avoidance-height-cm",
        type=int,
        help="Obstacle avoidance height in centimeters.",
    )
    parser.add_argument(
        "--obstacle-avoidance-distance-cm",
        type=int,
        help="Obstacle avoidance distance in centimeters.",
    )
    parser.add_argument(
        "--obstacle-ai-class",
        action="append",
        choices=["people", "animals", "objects"],
        dest="obstacle_ai_classes",
        help=(
            "Repeat to set AI obstacle classes, for example "
            "--obstacle-ai-class people."
        ),
    )
    parser.add_argument(
        "--edge-mowing-safe",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable or disable safe edge mowing.",
    )
    parser.add_argument(
        "--device-index",
        type=int,
        default=0,
        help="Zero-based discovered mower index to inspect.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Send the preference write request. Defaults to dry-run.",
    )
    parser.add_argument(
        "--confirm-write",
        action="store_true",
        help="Required together with --execute before any preference write is sent.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Optional JSON output file. Prints to stdout when omitted.",
    )
    return parser


def _changes_from_args(args: argparse.Namespace) -> dict[str, object]:
    changes = {
        "preference_mode": args.preference_mode,
        "efficient_mode": args.efficient_mode,
        "mowing_height_cm": args.mowing_height_cm,
        "mowing_direction_mode": args.mowing_direction_mode,
        "mowing_direction_degrees": args.mowing_direction_degrees,
        "edge_mowing_auto": args.edge_mowing_auto,
        "edge_mowing_walk_mode": args.edge_mowing_walk_mode,
        "edge_mowing_obstacle_avoidance": args.edge_mowing_obstacle_avoidance,
        "cutter_position": args.cutter_position,
        "edge_mowing_num": args.edge_mowing_num,
        "obstacle_avoidance_enabled": args.obstacle_avoidance_enabled,
        "obstacle_avoidance_height_cm": args.obstacle_avoidance_height_cm,
        "obstacle_avoidance_distance_cm": args.obstacle_avoidance_distance_cm,
        "obstacle_avoidance_ai_classes": args.obstacle_ai_classes,
        "edge_mowing_safe": args.edge_mowing_safe,
    }
    result = {
        key: value
        for key, value in changes.items()
        if value is not None
    }
    if not result:
        raise RuntimeError("At least one preference field must be provided.")
    return result


async def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    username = os.environ["DREAME_USERNAME"]
    password = os.environ["DREAME_PASSWORD"]
    country = os.environ.get("DREAME_COUNTRY", "eu")
    account_type = os.environ.get("DREAME_ACCOUNT_TYPE", "dreame")
    changes = _changes_from_args(args)
    zone_scoped_change = any(key != "preference_mode" for key in changes)
    if zone_scoped_change and args.area_id is None:
        raise RuntimeError("--area-id is required for per-area settings updates.")

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
        result = await client.async_plan_app_mowing_preference_update(
            map_index=args.map_index,
            area_id=args.area_id,
            changes=changes,
            execute=args.execute,
            confirm_write=args.confirm_write,
        )
        result["descriptor"] = {
            "title": devices[args.device_index].title,
            "model": devices[args.device_index].model,
            "display_model": devices[args.device_index].display_model,
            "device_index": args.device_index,
        }
        rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
        if args.out:
            args.out.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
    finally:
        await client.async_close()


if __name__ == "__main__":
    asyncio.run(main())
