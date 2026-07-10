"""Supervised end-to-end Home Assistant playback proof for Dreame video.

This probe creates a temporary real Home Assistant instance, loads the custom
integration through its config flow, asks the live-video camera entity for its
stream, and verifies that Home Assistant's stream integration produces HLS
media. It never uses an Android device or Android framework.

The mower is never moved or its camera enabled unless ``--execute`` is passed.
Use ``--start-before-active`` only for a supervised docked-mower proof; that
flag also makes dock-after cleanup mandatory.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import aiohttp

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from homeassistant import bootstrap, loader  # noqa: E402
from homeassistant.components import camera as camera_component  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402

DOMAIN = "dreame_lawn_mower"
CONF_ACCOUNT_TYPE = "account_type"
CONF_COUNTRY = "country"
CONF_PASSWORD = "password"
CONF_USERNAME = "username"
_STATION_STATES = {
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
        help="Enable the mower camera and verify an HA HLS media segment.",
    )
    parser.add_argument(
        "--start-before-active",
        action="store_true",
        help="Start/undock a docked mower before the playback proof.",
    )
    parser.add_argument(
        "--active-timeout",
        type=float,
        default=180.0,
        help="Seconds to wait for the mower to leave its station.",
    )
    parser.add_argument(
        "--stream-timeout",
        type=float,
        default=90.0,
        help="Seconds to wait for Home Assistant HLS media.",
    )
    parser.add_argument(
        "--dock-timeout",
        type=float,
        default=240.0,
        help="Seconds to wait for dock-after cleanup.",
    )
    parser.add_argument("--out", type=Path, help="Optional redacted JSON output.")
    args = parser.parse_args()
    if args.start_before_active and not args.execute:
        parser.error("--start-before-active requires --execute")
    return args


def _state_text(value: Any) -> str:
    text = getattr(value, "name", None) or getattr(value, "value", None) or value
    return str(text or "").rsplit(".", 1)[-1].strip().casefold()


def _snapshot_summary(snapshot: Any) -> dict[str, Any]:
    return {
        "state": _state_text(getattr(snapshot, "state", None)),
        "activity": _state_text(getattr(snapshot, "activity", None)),
        "docked": bool(getattr(snapshot, "docked", False)),
        "raw_docked": bool(getattr(snapshot, "raw_docked", False)),
        "battery_level": getattr(snapshot, "battery_level", None),
    }


def _at_station(snapshot: Any) -> bool:
    summary = _snapshot_summary(snapshot)
    return bool(
        summary["docked"]
        or summary["raw_docked"]
        or summary["state"] in _STATION_STATES
    )


async def _wait_for_state(
    client: Any,
    predicate: Any,
    *,
    timeout: float,
) -> Any:
    deadline = asyncio.get_running_loop().time() + max(timeout, 0.1)
    last_error: Exception | None = None
    while True:
        try:
            snapshot = await client.async_refresh()
        except Exception as err:  # noqa: BLE001 - cloud refreshes can be transient.
            last_error = err
        else:
            last_error = None
            if predicate(snapshot):
                return snapshot
        if asyncio.get_running_loop().time() >= deadline:
            message = "Timed out waiting for the mower state transition."
            if last_error is not None:
                message += f" Last refresh: {type(last_error).__name__}."
            raise TimeoutError(message)
        await asyncio.sleep(1.0)


def _pick_loopback_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def _setup_home_assistant(config_dir: Path, port: int) -> HomeAssistant:
    custom_components = config_dir / "custom_components"
    custom_components.mkdir(parents=True)
    (custom_components / DOMAIN).symlink_to(
        _REPO_ROOT / "custom_components" / DOMAIN,
        target_is_directory=True,
    )

    hass = HomeAssistant(str(config_dir))
    loader.async_setup(hass)
    config: dict[str, Any] = {
        "homeassistant": {},
        "http": {
            "server_host": "127.0.0.1",
            "server_port": port,
        },
        "stream": {},
        "camera": {},
    }
    if await bootstrap.async_from_config_dict(config, hass) is None:
        raise RuntimeError("Home Assistant bootstrap failed.")
    await hass.async_start()
    return hass


async def _configure_integration(hass: HomeAssistant) -> Any:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
        data={
            CONF_USERNAME: os.environ["DREAME_USERNAME"],
            CONF_PASSWORD: os.environ["DREAME_PASSWORD"],
            CONF_COUNTRY: os.environ.get("DREAME_COUNTRY", "eu"),
            CONF_ACCOUNT_TYPE: os.environ.get("DREAME_ACCOUNT_TYPE", "dreame"),
        },
    )
    await hass.async_block_till_done()
    if result.get("type") != "create_entry":
        raise RuntimeError(
            "Dreame config flow did not create an entry: "
            f"{result.get('type')} / {result.get('reason') or result.get('errors')}"
        )
    entries = hass.config_entries.async_entries(DOMAIN)
    if len(entries) != 1:
        raise RuntimeError("Expected exactly one temporary Dreame config entry.")
    entry = entries[0]
    if entry.state.name.casefold() != "loaded":
        raise RuntimeError(f"Dreame config entry is {entry.state.name}, not loaded.")
    return entry


def _find_video_camera(hass: HomeAssistant) -> Any:
    component = hass.data[camera_component.DATA_COMPONENT]
    entities = [
        entity
        for entity in component.entities
        if entity.__class__.__name__ == "DreameLawnMowerVideoCamera"
        and entity.__class__.__module__.endswith(".video_camera")
    ]
    if len(entities) != 1:
        raise RuntimeError("Expected one Dreame live-video camera entity.")
    return entities[0]


async def _wait_for_hls_media(output: Any, *, timeout: float) -> Any:
    deadline = asyncio.get_running_loop().time() + max(timeout, 0.1)
    while True:
        segment = output.last_segment
        if segment is not None and segment.init and segment.data_size > 0:
            return segment
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError("Home Assistant did not produce HLS media in time.")
        if segment is None:
            await asyncio.wait_for(output.recv(), timeout=remaining)
        else:
            await output.part_recv(timeout=min(remaining, 5.0))


async def _fetch_hls_playlist(port: int, endpoint: str) -> tuple[int, str]:
    url = urljoin(f"http://127.0.0.1:{port}/", endpoint)
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            return response.status, await response.text()


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    port = _pick_loopback_port()
    output: dict[str, Any] = {
        "executed": args.execute,
        "host_runtime": "python_linux_managed",
        "android_required": False,
        "home_assistant_hls_verified": False,
    }
    hass: HomeAssistant | None = None
    entry = None
    camera = None
    ha_stream = None
    client = None
    start_sent = False

    with tempfile.TemporaryDirectory(prefix="dreame-ha-video-proof-") as temp_dir:
        try:
            hass = await _setup_home_assistant(Path(temp_dir), port)
            entry = await _configure_integration(hass)
            coordinator = hass.data[DOMAIN][entry.entry_id]
            client = coordinator.client
            camera = _find_video_camera(hass)
            snapshot = await client.async_refresh()
            output["before"] = _snapshot_summary(snapshot)
            output["camera_entity"] = {
                "entity_id": camera.entity_id,
                "available": camera.available,
                "runtime_mode": camera.extra_state_attributes[
                    "video_runtime_mode"
                ],
                "runtime_preparation_error": camera.extra_state_attributes[
                    "video_runtime_preparation_error"
                ],
            }
            if not args.execute:
                return output

            if _at_station(snapshot):
                if not args.start_before_active:
                    raise RuntimeError(
                        "Mower is docked; rerun under supervision with "
                        "--start-before-active."
                    )
                await client.async_start_mowing()
                start_sent = True
                snapshot = await _wait_for_state(
                    client,
                    lambda value: not _at_station(value),
                    timeout=args.active_timeout,
                )
                output["after_start"] = _snapshot_summary(snapshot)

            ha_stream = await camera.async_create_stream()
            if ha_stream is None:
                raise RuntimeError(
                    camera.extra_state_attributes.get("last_stream_error")
                    or "Home Assistant camera returned no stream."
                )
            hls_output = ha_stream.add_provider("hls")
            endpoint = ha_stream.endpoint_url("hls")
            await ha_stream.start()
            segment = await _wait_for_hls_media(
                hls_output,
                timeout=args.stream_timeout,
            )
            status, playlist = await _fetch_hls_playlist(port, endpoint)
            output["playback"] = {
                "camera_stream_created": True,
                "source_scheme": "http",
                "hls_endpoint_status": status,
                "hls_playlist_present": "#EXTM3U" in playlist,
                "hls_init_bytes": len(segment.init),
                "hls_media_bytes": segment.data_size,
                "hls_init_is_mp4": segment.init[4:8] == b"ftyp",
                "sequence": segment.sequence,
            }
            output["home_assistant_hls_verified"] = bool(
                status == 200
                and "#EXTM3U" in playlist
                and segment.init[4:8] == b"ftyp"
                and segment.data_size > 0
            )
            if not output["home_assistant_hls_verified"]:
                raise RuntimeError("Home Assistant HLS verification was incomplete.")
        except Exception as err:  # noqa: BLE001 - retain cleanup evidence.
            detail = str(err).strip()
            output["error"] = type(err).__name__ + (f": {detail}" if detail else "")
        finally:
            if camera is not None:
                try:
                    await camera.async_turn_off()
                    output["camera_turned_off"] = True
                except Exception as err:  # noqa: BLE001 - preserve cleanup evidence.
                    output["camera_turn_off_error"] = str(err)
            if ha_stream is not None:
                await ha_stream.stop()
            if client is not None and (args.execute or start_sent):
                dock_request_error = None
                try:
                    await client.async_dock()
                except Exception as err:  # noqa: BLE001 - still verify station state.
                    dock_request_error = f"{type(err).__name__}: {err}"
                try:
                    snapshot = await _wait_for_state(
                        client,
                        _at_station,
                        timeout=args.dock_timeout,
                    )
                    output["cleanup"] = {
                        "dock_requested": True,
                        "verified": True,
                        "snapshot": _snapshot_summary(snapshot),
                    }
                    if dock_request_error:
                        output["cleanup"]["dock_request_error"] = dock_request_error
                except Exception as err:  # noqa: BLE001 - docking was requested.
                    output["cleanup"] = {
                        "dock_requested": True,
                        "verified": False,
                        "error": f"{type(err).__name__}: {err}",
                    }
                    if dock_request_error:
                        output["cleanup"]["dock_request_error"] = dock_request_error
            if hass is not None:
                if entry is not None:
                    await hass.config_entries.async_unload(entry.entry_id)
                await hass.async_stop(force=True)
    return output


def _write_output(output: dict[str, Any], path: Path | None) -> None:
    rendered = json.dumps(output, indent=2, sort_keys=True, default=str)
    if path is None:
        print(rendered)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered + "\n", encoding="utf-8")


async def main() -> None:
    args = _parse_args()
    output = await _run(args)
    _write_output(output, args.out)
    if output.get("error"):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
