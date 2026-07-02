"""Safety-gated probe for the Dreame mower camera stream handshake.

Default mode is read-only. Add --execute to try a short monitor start/end
handshake. This does not start mowing, audio, or remote control.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

from dreame_lawn_mower_client import (
    DreameLawnMowerClient,
    DreameLawnMowerConnectionError,
    DreameLawnMowerNativeXp2pRuntime,
    DreameLawnMowerVideoRuntimeError,
)

PAYLOAD_MODES = ("app_action", "with_session", "no_session", "empty_session")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Start and then end a short camera monitor handshake.",
    )
    parser.add_argument("--timeout", type=float, default=6.0)
    parser.add_argument("--interval", type=float, default=0.75)
    parser.add_argument(
        "--payload-mode",
        choices=PAYLOAD_MODES,
        default="app_action",
        help="STREAM_VIDEO payload shape to test.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Optional JSON output file. Prints to stdout when omitted.",
    )
    parser.add_argument(
        "--xp2p-library",
        type=Path,
        help="Optional native XP2P shared library path for live FLV probing.",
    )
    parser.add_argument(
        "--start-native-xp2p",
        action="store_true",
        help="Start and stop the native XP2P live stream probe.",
    )
    return parser.parse_args()


def _safe_stream_inputs_summary(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"available": False}
    tx_rtc = value.get("tx_rtc_info")
    tx_rtc = tx_rtc if isinstance(tx_rtc, dict) else {}
    p2p = value.get("p2p_info")
    p2p = p2p if isinstance(p2p, dict) else {}
    return {
        "available": True,
        "source": value.get("source"),
        "tx_rtc_info": {
            "channel_id_present": bool(tx_rtc.get("channel_id")),
            "product_id_present": bool(tx_rtc.get("product_id")),
            "device_name_present": bool(tx_rtc.get("device_name")),
            "secret_id_present": bool(tx_rtc.get("secret_id")),
            "secret_key_present": bool(tx_rtc.get("secret_key")),
            "app_id_present": bool(tx_rtc.get("app_id")),
            "app_secret_present": bool(tx_rtc.get("app_secret")),
        },
        "p2p_info": {
            "available": bool(p2p.get("available")),
            "p2p_info_present": bool(p2p.get("p2p_info")),
        },
    }


def _safe_runtime_inputs_summary(value: Any) -> dict[str, Any]:
    if value is None:
        return {"ready": False, "available": False}
    if hasattr(value, "as_dict"):
        payload = value.as_dict(redact=True)
    elif isinstance(value, dict):
        payload = dict(value)
    else:
        return {"ready": False, "available": False}
    payload["available"] = True
    return payload


def _native_xp2p_unavailable(reason: str) -> dict[str, object]:
    return {
        "started": False,
        "available": False,
        "error": reason,
    }


def _write_output(output: dict[str, object], out: Path | None) -> None:
    rendered = json.dumps(output, indent=2, sort_keys=True)
    if out is None:
        print(rendered)
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered + "\n", encoding="utf-8")


async def main() -> None:
    args = _parse_args()
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
        snapshot = await client.async_refresh()
        support = await client.async_get_camera_feature_support()
        output: dict[str, object] = {
            "device": snapshot.descriptor.title,
            "state": snapshot.state,
            "activity": snapshot.activity,
            "executed": args.execute,
            "payload_mode": args.payload_mode,
            "camera_support": support.as_dict(),
        }
        try:
            stream_inputs = await client.async_get_camera_stream_inputs()
            output["camera_stream_inputs"] = _safe_stream_inputs_summary(stream_inputs)
            runtime_inputs = await client.async_get_camera_stream_runtime_inputs()
            output["camera_stream_runtime_inputs"] = _safe_runtime_inputs_summary(
                runtime_inputs
            )
            if args.start_native_xp2p:
                output["native_xp2p"] = await _async_probe_native_xp2p(
                    args.xp2p_library,
                    runtime_inputs,
                )
        except DreameLawnMowerConnectionError as err:
            output["camera_stream_inputs"] = {
                "available": False,
                "error": str(err),
            }
            output["camera_stream_runtime_inputs"] = {
                "available": False,
                "ready": False,
                "error": str(err),
            }
        if args.execute:
            try:
                output["handshake"] = await client.async_probe_camera_stream_handshake(
                    timeout=args.timeout,
                    interval=args.interval,
                    payload_mode=args.payload_mode,
                )
            except DreameLawnMowerConnectionError as err:
                output["handshake_error"] = str(err)
        else:
            output["next_step"] = (
                "Re-run with --execute to try a short monitor stream handshake."
            )
        _write_output(output, args.out)
    finally:
        await client.async_close()


async def _async_probe_native_xp2p(
    library_path: Path | None,
    runtime_inputs: Any,
) -> dict[str, object]:
    if library_path is None:
        return _native_xp2p_unavailable(
            "--xp2p-library is required with --start-native-xp2p."
        )
    if not getattr(runtime_inputs, "ready", False):
        missing = getattr(runtime_inputs, "missing_required", ())
        return _native_xp2p_unavailable(
            "Runtime inputs are incomplete: " + ", ".join(missing)
        )

    def _start_and_stop() -> dict[str, object]:
        runtime = DreameLawnMowerNativeXp2pRuntime(library_path)
        session = runtime.start_live_stream(runtime_inputs)
        try:
            payload = session.as_dict()
            payload["started"] = True
            payload["available"] = True
            return payload
        finally:
            runtime.stop_live_stream(session)

    try:
        return await asyncio.to_thread(_start_and_stop)
    except DreameLawnMowerVideoRuntimeError as err:
        return _native_xp2p_unavailable(str(err))


if __name__ == "__main__":
    asyncio.run(main())
