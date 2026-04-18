"""Example for retrieving a mower map summary and PNG from the reusable client."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from dreame_lawn_mower_client import DreameLawnMowerClient


async def main() -> None:
    username = os.environ["DREAME_USERNAME"]
    password = os.environ["DREAME_PASSWORD"]
    country = os.environ.get("DREAME_COUNTRY", "eu")
    account_type = os.environ.get("DREAME_ACCOUNT_TYPE", "dreame")
    output_path = Path(os.environ.get("DREAME_MAP_OUTPUT", "dreame-map.png"))

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
        map_view = await client.async_refresh_map_view()
        map_summary = map_view.summary

        print(snapshot.descriptor.title)
        print(f"State: {snapshot.state_name}")
        print(
            "Map diagnostics:",
            json.dumps(map_view.as_dict(), indent=2, sort_keys=True),
        )
        if map_summary is None:
            print("Map: unavailable")
            return

        print(
            "Map:",
            {
                "map_id": map_summary.map_id,
                "frame_id": map_summary.frame_id,
                "segments": map_summary.segment_count,
                "path_points": map_summary.path_point_count,
                "no_go_areas": map_summary.no_go_area_count,
                "spot_areas": map_summary.spot_area_count,
                "width": map_summary.width,
                "height": map_summary.height,
            },
        )

        if map_view.image_png:
            output_path.write_bytes(map_view.image_png)
            print(f"Saved map image to {output_path}")
    finally:
        await client.async_close()


if __name__ == "__main__":
    asyncio.run(main())
