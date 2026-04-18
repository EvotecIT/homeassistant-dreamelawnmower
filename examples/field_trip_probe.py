"""Capture before/during/after evidence for a supervised mower field trip.

The default mode is read-only. With --execute and --confirm-supervised, this
script sends tiny remote-control movement pulses and captures operation
snapshots around each step. It never starts mowing or camera streaming.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from dreame_lawn_mower_client import DreameLawnMowerClient


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Capture structured mower diagnostics before/during/after a short "
            "supervised remote-control field trip."
        )
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually send tiny remote-control movement pulses.",
    )
    parser.add_argument(
        "--confirm-supervised",
        action="store_true",
        help="Required with --execute to confirm the mower is supervised outdoors.",
    )
    parser.add_argument(
        "--velocity",
        type=int,
        default=60,
        help="Forward/backward speed for movement pulses.",
    )
    parser.add_argument(
        "--rotation",
        type=int,
        default=45,
        help="Rotation speed for turn pulses.",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=0.5,
        help="Seconds to hold each movement pulse before sending stop.",
    )
    parser.add_argument(
        "--settle",
        type=float,
        default=1.0,
        help="Seconds to wait after each stop before capturing state.",
    )
    parser.add_argument(
        "--dock",
        action="store_true",
        help="Request return-to-dock after the smoke sequence.",
    )
    parser.add_argument(
        "--include-map",
        action="store_true",
        help="Include map-view diagnostics in every operation snapshot.",
    )
    parser.add_argument(
        "--include-firmware",
        action="store_true",
        help="Include firmware/update evidence in every operation snapshot.",
    )
    parser.add_argument(
        "--map-timeout",
        type=float,
        default=6.0,
        help="Map-view probe timeout when --include-map is set.",
    )
    parser.add_argument(
        "--device-index",
        type=int,
        default=0,
        help="Zero-based discovered mower index to test.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Optional JSON output file. Prints to stdout when omitted.",
    )
    return parser


async def _safe_step(
    label: str,
    action: Callable[[], Awaitable[Any]],
) -> dict[str, Any]:
    try:
        result = await action()
    except Exception as err:  # noqa: BLE001 - field output should preserve failures
        return {"label": label, "ok": False, "error": str(err)}
    return {"label": label, "ok": True, "result": result}


async def _capture(
    client: DreameLawnMowerClient,
    label: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    return await client.async_capture_operation_snapshot(
        label=label,
        include_map_view=args.include_map,
        include_firmware=args.include_firmware,
        map_timeout=args.map_timeout,
    )


def _write_checkpoint(args: argparse.Namespace, output: dict[str, Any]) -> None:
    """Persist partial results so a slow cloud call does not lose evidence."""
    if args.out:
        args.out.write_text(
            json.dumps(output, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


async def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.execute and not args.confirm_supervised:
        raise RuntimeError("--execute requires --confirm-supervised.")

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
    output: dict[str, Any] = {
        "execute": args.execute,
        "device_index": args.device_index,
        "settings": {
            "velocity": args.velocity,
            "rotation": args.rotation,
            "duration": args.duration,
            "settle": args.settle,
            "dock": args.dock,
            "include_map": args.include_map,
            "include_firmware": args.include_firmware,
        },
        "steps": [],
        "captures": [],
    }
    try:
        output["captures"].append(await _capture(client, "before", args))
        _write_checkpoint(args, output)

        if args.execute:
            output["steps"].append(
                await _safe_step("stop_before", client.async_remote_control_stop)
            )
            _write_checkpoint(args, output)
            await asyncio.sleep(args.settle)
            output["captures"].append(await _capture(client, "after_stop_before", args))
            _write_checkpoint(args, output)

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
                _write_checkpoint(args, output)
                await asyncio.sleep(args.duration)
                output["steps"].append(
                    await _safe_step(
                        f"stop_after_{label}",
                        client.async_remote_control_stop,
                    )
                )
                _write_checkpoint(args, output)
                await asyncio.sleep(args.settle)
                output["captures"].append(
                    await _capture(client, f"after_{label}", args)
                )
                _write_checkpoint(args, output)

            if args.dock:
                output["steps"].append(await _safe_step("dock", client.async_dock))
                _write_checkpoint(args, output)
                await asyncio.sleep(max(2.0, args.settle))
                output["captures"].append(await _capture(client, "after_dock", args))
                _write_checkpoint(args, output)
        else:
            output["steps"].append(
                {
                    "label": "read_only",
                    "ok": True,
                    "result": "No movement commands sent.",
                }
            )
            _write_checkpoint(args, output)

        output["captures"].append(await _capture(client, "final", args))
        _write_checkpoint(args, output)
    finally:
        await client.async_close()

    rendered = json.dumps(output, indent=2, sort_keys=True)
    if args.out:
        args.out.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    asyncio.run(main())
