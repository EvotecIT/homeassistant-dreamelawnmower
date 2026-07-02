"""Safety-gated probe for the Dreame mower camera stream handshake.

Default mode is read-only. Add --execute or an XP2P start flag to try a short
supervised video attempt. This does not start mowing, audio, or remote control.
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
    DreameLawnMowerXp2pExternalRunner,
    DreameLawnMowerXp2pLiveStreamRequest,
    diagnose_native_xp2p_runtime,
)

PAYLOAD_MODES = ("app_action", "with_session", "no_session", "empty_session")
STATION_STATES = {
    "charging",
    "charging_completed",
    "smart_charging",
    "station_reset",
}


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
    parser.add_argument(
        "--xp2p-runner",
        type=Path,
        help="Optional external XP2P runner executable path.",
    )
    parser.add_argument(
        "--start-xp2p-runner",
        action="store_true",
        help="Start and stop the external XP2P runner live stream probe.",
    )
    parser.add_argument(
        "--wait-undocked-timeout",
        type=float,
        default=0.0,
        help=(
            "When an active stream probe is requested, wait this many seconds "
            "for the mower to be undocked before starting video."
        ),
    )
    parser.add_argument(
        "--dock-after-active",
        action="store_true",
        help=(
            "After an active stream probe is attempted, send the mower back to "
            "dock in a finally step."
        ),
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
    for key in ("did", "channel_id", "product_id", "device_name", "xp2p_id"):
        payload[f"{key}_present"] = bool(payload.get(key))
        payload.pop(key, None)
    return payload


def _safe_xp2p_request_summary(value: Any) -> dict[str, Any]:
    if not getattr(value, "ready", False):
        missing = getattr(value, "missing_required", ())
        return {
            "available": False,
            "ready": False,
            "missing_required": list(missing),
        }
    try:
        request = DreameLawnMowerXp2pLiveStreamRequest.from_runtime_inputs(value)
    except DreameLawnMowerVideoRuntimeError as err:
        return {
            "available": False,
            "ready": False,
            "error": str(err),
        }
    payload = request.as_dict(redact=True)
    payload["available"] = True
    payload["ready"] = True
    return payload


def _native_xp2p_unavailable(reason: str) -> dict[str, object]:
    return {
        "started": False,
        "available": False,
        "error": reason,
    }


def _active_probe_requested(args: argparse.Namespace) -> bool:
    return bool(args.execute or args.start_native_xp2p or args.start_xp2p_runner)


def _active_video_block_reason(snapshot: Any) -> str | None:
    state = str(getattr(snapshot, "state", "") or "").casefold()
    raw_docked = bool(getattr(snapshot, "raw_docked", False))
    if raw_docked or state in STATION_STATES:
        return (
            "Active video probe is blocked while the mower is docked. "
            "Undock it first or use --wait-undocked-timeout during a supervised run."
        )
    raw_attributes = getattr(snapshot, "raw_attributes", None) or {}
    if bool(raw_attributes.get("mapping")) or bool(raw_attributes.get("fast_mapping")):
        return "Active video probe is blocked while mapping."
    return None


def _next_step_message(
    *,
    active_requested: bool,
    active_block_reason: str | None,
) -> str | None:
    if active_block_reason:
        return (
            "Undock the mower, or rerun with --wait-undocked-timeout during "
            "a supervised test. Add --dock-after-active to return it to base "
            "after an active stream attempt."
        )
    if not active_requested:
        return (
            "Re-run with --execute for the app stream handshake, or with an "
            "XP2P runtime flag to try a short live FLV stream."
        )
    return None


async def _async_wait_for_active_video_state(
    client: DreameLawnMowerClient,
    *,
    initial_snapshot: Any,
    timeout: float,
    interval: float,
) -> tuple[Any, dict[str, object]]:
    deadline = asyncio.get_running_loop().time() + max(timeout, 0.0)
    snapshot = initial_snapshot
    attempts = 0
    reason = _active_video_block_reason(snapshot)
    while reason is not None and asyncio.get_running_loop().time() < deadline:
        attempts += 1
        await asyncio.sleep(max(interval, 0.1))
        snapshot = await client.async_refresh()
        reason = _active_video_block_reason(snapshot)
    return snapshot, {
        "waited": attempts > 0,
        "attempts": attempts,
        "ready": reason is None,
        "block_reason": reason,
        "timeout": timeout,
    }


async def _async_dock_after_active(
    client: DreameLawnMowerClient,
    output: dict[str, object],
) -> None:
    try:
        await client.async_dock()
    except Exception as err:
        output["dock_after_active"] = {
            "requested": True,
            "sent": False,
            "error": str(err),
        }
        return
    output["dock_after_active"] = {
        "requested": True,
        "sent": True,
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
        active_requested = _active_probe_requested(args)
        output: dict[str, object] = {
            "device": snapshot.descriptor.title,
            "state": snapshot.state,
            "activity": snapshot.activity,
            "executed": args.execute,
            "payload_mode": args.payload_mode,
            "camera_support": support.as_dict(),
            "field_test": {
                "active_requested": active_requested,
                "wait_undocked_timeout": args.wait_undocked_timeout,
                "dock_after_active_requested": args.dock_after_active,
            },
        }
        active_attempted = False
        try:
            stream_inputs = await client.async_get_camera_stream_inputs()
            output["camera_stream_inputs"] = _safe_stream_inputs_summary(stream_inputs)
            runtime_inputs = await client.async_get_camera_stream_runtime_inputs()
            output["camera_stream_runtime_inputs"] = _safe_runtime_inputs_summary(
                runtime_inputs
            )
            output["xp2p_request"] = _safe_xp2p_request_summary(runtime_inputs)
            active_block_reason = _active_video_block_reason(snapshot)
            if active_requested and active_block_reason and args.wait_undocked_timeout:
                snapshot, wait_result = await _async_wait_for_active_video_state(
                    client,
                    initial_snapshot=snapshot,
                    timeout=args.wait_undocked_timeout,
                    interval=args.interval,
                )
                output["field_test"]["wait_undocked"] = wait_result
                output["state"] = snapshot.state
                output["activity"] = snapshot.activity
                active_block_reason = _active_video_block_reason(snapshot)
            if active_block_reason:
                output["field_test"]["active_block_reason"] = active_block_reason
            if args.xp2p_library is not None:
                output["native_xp2p_diagnostics"] = await _async_diagnose_native_xp2p(
                    args.xp2p_library
                )
            if args.start_native_xp2p and active_block_reason is None:
                active_attempted = True
                output["native_xp2p"] = await _async_probe_native_xp2p(
                    args.xp2p_library,
                    runtime_inputs,
                )
            if args.start_xp2p_runner and active_block_reason is None:
                active_attempted = True
                output["xp2p_runner"] = await _async_probe_xp2p_runner(
                    args.xp2p_runner,
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
        active_block_reason = output["field_test"].get("active_block_reason")
        if args.execute and active_block_reason is None:
            active_attempted = True
            try:
                output["handshake"] = await client.async_probe_camera_stream_handshake(
                    timeout=args.timeout,
                    interval=args.interval,
                    payload_mode=args.payload_mode,
                )
            except DreameLawnMowerConnectionError as err:
                output["handshake_error"] = str(err)
        next_step = _next_step_message(
            active_requested=active_requested,
            active_block_reason=(
                str(active_block_reason) if active_block_reason is not None else None
            ),
        )
        if next_step:
            output["next_step"] = next_step
        if args.dock_after_active and active_attempted:
            await _async_dock_after_active(client, output)
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
    diagnostics = await _async_diagnose_native_xp2p(library_path)
    if not diagnostics.get("ready"):
        payload = _native_xp2p_unavailable(
            str(diagnostics.get("error") or "Native XP2P runtime is not ready.")
        )
        payload["diagnostics"] = diagnostics
        return payload

    def _start_and_stop() -> dict[str, object]:
        runtime = DreameLawnMowerNativeXp2pRuntime(library_path)
        session = runtime.start_live_stream(runtime_inputs)
        try:
            payload = session.as_dict(redact=True)
            payload["started"] = True
            payload["available"] = True
            return payload
        finally:
            runtime.stop_live_stream(session)

    try:
        return await asyncio.to_thread(_start_and_stop)
    except DreameLawnMowerVideoRuntimeError as err:
        return _native_xp2p_unavailable(str(err))


async def _async_diagnose_native_xp2p(library_path: Path) -> dict[str, object]:
    diagnostics = await asyncio.to_thread(diagnose_native_xp2p_runtime, library_path)
    return diagnostics.as_dict()


async def _async_probe_xp2p_runner(
    runner_path: Path | None,
    runtime_inputs: Any,
) -> dict[str, object]:
    if runner_path is None:
        return _native_xp2p_unavailable(
            "--xp2p-runner is required with --start-xp2p-runner."
        )
    if not getattr(runtime_inputs, "ready", False):
        missing = getattr(runtime_inputs, "missing_required", ())
        return _native_xp2p_unavailable(
            "Runtime inputs are incomplete: " + ", ".join(missing)
        )

    def _start_and_stop() -> dict[str, object]:
        runner = DreameLawnMowerXp2pExternalRunner((runner_path,))
        session = runner.start_live_stream(runtime_inputs)
        try:
            payload = session.as_dict(redact=True)
            payload["started"] = True
            payload["available"] = True
            return payload
        finally:
            runner.stop_live_stream(session)

    try:
        return await asyncio.to_thread(_start_and_stop)
    except DreameLawnMowerVideoRuntimeError as err:
        return _native_xp2p_unavailable(str(err))


if __name__ == "__main__":
    asyncio.run(main())
