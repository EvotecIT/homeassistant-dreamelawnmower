# Dreame Lawn Mower Python Library

`dreame_lawn_mower_client` is the reusable async Python layer that powers the
Home Assistant integration in this repository. It is useful for scripts, local
research probes, tests, and future extraction into a standalone package.

The client talks to the Dreamehome/MOVAhome cloud and app-style mower APIs. It
keeps Home Assistant entity behavior out of the protocol layer, so the same
code can be reused outside Home Assistant.

## Install For Local Development

From this repository:

```bash
python -m pip install -e .[test]
```

Then import the public package:

```python
from dreame_lawn_mower_client import DreameLawnMowerClient
```

## Minimal Example

Credentials should come from environment variables or another secret store. Do
not write credentials into fixtures, docs, or issue attachments.

```python
import asyncio
import os

from dreame_lawn_mower_client import DreameLawnMowerClient


async def main() -> None:
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
        print(snapshot.descriptor.title)
        print(snapshot.mower_state_name)
        print(snapshot.battery_level)
        print(snapshot.mowing_task_status_name)
        print(snapshot.task_resumable)
    finally:
        await client.async_close()


asyncio.run(main())
```

The same flow is available as `examples/python_client.py`.

## Mower Terminology

Use the mower-native snapshot properties in new scripts and automations:

- `mower_state` and `mower_state_name`
- `mowing_task_status` and `mowing_task_status_name`
- `mowing_mode` and `mowing_mode_name`
- `mowed_area` and `mowing_time`
- `scheduled_mow`

The inherited `state`, `state_name`, `task_status`, `task_status_name`,
`cleaning_mode`, `cleaning_mode_name`, `cleaned_area`, `cleaning_time`, and
`scheduled_clean` fields remain available as compatibility aliases. They keep
their vendor-facing values during the migration and may be removed in a future
breaking release. Home Assistant entity registry keys remain unchanged, so an
upgrade does not create duplicate entities.

## Useful Client Features

- account discovery for Dreamehome and MOVAhome accounts
- normalized mower snapshots with state, activity, battery, errors, firmware,
  capability, cloud presence, and heartbeat-backed task data
- automatic resume of a heartbeat-confirmed paused session while ordinary
  starts continue to create a fresh mower task
- read-only schedule retrieval and calendar-friendly task summaries
- dry-run schedule enable/disable planning, with explicit gates required before
  live writes
- guarded mowing-preference planning and optional live PRE writes from current
  app payloads, with explicit confirmation required before execution
- read-only app-map retrieval, all-map summaries, and simple map rendering
- on-demand app-map point-cloud generation, bounded download, and PCD validation
- decoded mower-native charging/rain settings, confirmed setting writes, and
  mowing-preference diagnostics
- firmware/update evidence gathering without claiming unverified OTA support
- guarded remote-control support helpers for supervised short movement pulses
- reusable payload decoders for app realtime/status keys

## Safety Defaults

Prefer read-only calls while investigating a mower. Methods and examples that
can move the mower or change mower settings use explicit execution flags,
confirmation flags, or state guards. Do not run live movement or write probes
from automations.

## Point Clouds

`async_download_app_map_point_cloud()` owns the complete mower/cloud flow:

```python
from pathlib import Path

download = await client.async_download_app_map_point_cloud(
    map_index=0,
    allow_stored=True,  # Only when map 0 is the mower's sole known map.
)
print(download.metadata.as_dict())

# Persist geometry only when you explicitly need a local PCD file.
Path("garden-map.pcd").write_bytes(download.content)
```

With `allow_stored=True`, the method first validates the object currently
announced through cloud property `99.20`. Callers should enable this only when
the requested index is the mower's sole known map, because the announcement
does not identify its map. If that object is absent, expired, or invalid, the
client triggers app action `o:10`, captures the fresh LiDAR object, and resolves
its short-lived download immediately. Firmware without that announcement
continues through the transient `OBJ` 3D-map fallback. Every path enforces
HTTPS, time, and size limits and validates PCD 0.7 before returning. The result
deliberately does not expose the vendor filename or cloud-signed URL.

Use `python examples/point_cloud_probe.py` to print coordinate-free metadata.
Add `--out garden-map.pcd` only when you intentionally want to persist private
garden geometry.

## Package Layout

The public import is always:

```python
import dreame_lawn_mower_client
```

For HACS, the implementation is bundled under:

```text
custom_components/dreame_lawn_mower/dreame_lawn_mower_client
```

The top-level `dreame_lawn_mower_client` package loads that bundled
implementation without importing Home Assistant. This keeps one reusable client
surface while still shipping everything HACS needs inside the custom component.

When adding protocol behavior, update the bundled implementation and expose
stable imports through the public package. Keep Home Assistant-specific entity,
service, config-flow, and registry behavior in `custom_components`.

## Related Examples

- `examples/cloud_probe.py`
- `examples/app_map_probe.py`
- `examples/point_cloud_probe.py`
- `examples/batch_device_data_probe.py`
- `examples/schedule_probe.py`
- `examples/schedule_write_probe.py`
- `examples/preference_write_probe.py`
- `examples/weather_probe.py`
- `examples/preference_probe.py`
- `examples/task_status_probe.py`
- `examples/status_blob_probe.py`
- `examples/remote_control_probe.py`
