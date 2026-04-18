"""Safety-gated remote-control smoke test for Dreame mower devices."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from typing import Any

from dreame_lawn_mower_client import (
    DreameLawnMowerClient,
    remote_control_block_reason,
    remote_control_state_safe,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Probe remote-control support and optionally send tiny movement "
            "pulses. This never starts mowing."
        )
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually send movement commands. Omit for read-only probing.",
    )
    parser.add_argument(
        "--velocity",
        type=int,
        default=80,
        help="Forward/backward speed for movement pulses.",
    )
    parser.add_argument(
        "--rotation",
        type=int,
        default=80,
        help="Rotation speed for turn pulses.",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=0.75,
        help="Seconds to hold each movement pulse before sending stop.",
    )
    parser.add_argument(
        "--dock",
        action="store_true",
        help="Request return-to-dock after the smoke sequence.",
    )
    parser.add_argument(
        "--device-index",
        type=int,
        default=0,
        help="Zero-based discovered mower index to test.",
    )
    return parser


async def _safe_step(
    label: str,
    action: Callable[[], Awaitable[Any]],
) -> dict[str, Any]:
    try:
        result = await action()
    except Exception as err:  # noqa: BLE001 - smoke output should preserve failures
        return {"label": label, "ok": False, "error": str(err)}
    return {"label": label, "ok": True, "result": result}


def _snapshot_summary(snapshot: Any, support: Any) -> dict[str, Any]:
    return {
        "device": snapshot.descriptor.title,
        "state": snapshot.state,
        "activity": snapshot.activity,
        "battery_level": snapshot.battery_level,
        "charging": snapshot.charging,
        "docked": snapshot.docked,
        "mowing": snapshot.mowing,
        "paused": snapshot.paused,
        "returning": snapshot.returning,
        "error_code": snapshot.error_code,
        "error_display": snapshot.error_display,
        "realtime_property_count": snapshot.realtime_property_count,
        "manual_drive_safe": remote_control_state_safe(snapshot),
        "manual_drive_block_reason": remote_control_block_reason(snapshot),
        "remote_control_support": support.as_dict(),
    }


def _raise_if_unsafe_execute(snapshot: Any) -> None:
    """Block live movement when the current snapshot looks unsafe."""
    if reason := remote_control_block_reason(snapshot):
        raise RuntimeError(reason)


async def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

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
        before = await client.async_refresh()
        support = await client.async_get_remote_control_support()
        output: dict[str, Any] = {
            "execute": args.execute,
            "before": _snapshot_summary(before, support),
            "support": support.as_dict(),
            "steps": [],
        }

        if args.execute:
            _raise_if_unsafe_execute(before)
            output["steps"].append(
                await _safe_step("stop_before", client.async_remote_control_stop)
            )
            for label, rotation, velocity in (
                ("forward", 0, args.velocity),
                ("turn_right", args.rotation, 0),
                ("turn_left", -args.rotation, 0),
                ("backward", 0, -args.velocity),
            ):
                output["steps"].append(
                    await _safe_step(
                        label,
                        lambda r=rotation, v=velocity: (
                            client.async_remote_control_move_step(
                                rotation=r,
                                velocity=v,
                                prompt=False,
                            )
                        ),
                    )
                )
                await asyncio.sleep(args.duration)
                output["steps"].append(
                    await _safe_step(
                        f"stop_after_{label}",
                        client.async_remote_control_stop,
                    )
                )
                await asyncio.sleep(0.5)

            if args.dock:
                output["steps"].append(await _safe_step("dock", client.async_dock))
                await asyncio.sleep(2)

        after = await client.async_refresh()
        after_support = await client.async_get_remote_control_support()
        output["after"] = _snapshot_summary(after, after_support)
        print(json.dumps(output, indent=2, sort_keys=True))
    finally:
        await client.async_close()


if __name__ == "__main__":
    asyncio.run(main())
