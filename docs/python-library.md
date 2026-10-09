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

## HTTP Sessions

Account discovery uses native async HTTP through aiohttp. Supply an existing
session with `DreameLawnMowerClient.async_discover_devices(..., session=session)`
to share its connection pool. The caller owns that session; discovery never
closes it. Supply a session without `base_url` or a default `Authorization`
header; incompatible sessions raise `ValueError` before any request. Discovery
sets vendor authentication per request, overriding a session's default `auth`
without changing the borrowed session. It also controls status handling and
decompression so authentication failures and decoded response limits remain
consistent.

When the session is omitted, discovery opens and closes a temporary session
that honors environment proxies (`HTTP_PROXY`, `HTTPS_PROXY`, and `NO_PROXY`).
Borrowed sessions retain their own proxy policy. Environment credentials cannot
override vendor authentication.

Home Assistant supplies its shared session during setup and credential repair.
Login and inventory responses have a one MiB decoded-body limit, a total
operation deadline, cancellation cleanup, and no automatic redirect following.
Only read-only inventory requests retry transport failures.

The client constructor accepts `session=session` for native account reads,
device polling, supported mower commands, schedules, maps and camera discovery.
Without an injected session, the first native operation opens a reusable session.
Home Assistant lends its shared session to each client. Existing payload builders,
authentication rules, command confirmation and device routing remain shared with
the synchronous protocol owner.

Always call `await client.async_close()` when finished. Closing cancels outstanding
native operations, drains started HTTP and state work, and closes only a session
the client created. Borrowed sessions remain open; a closed client rejects further
operations. Page filters and pagination retain the synchronous wire format, while
malformed pages and rejected requests raise `DreameLawnMowerConnectionError`.

MQTT external-loop delivery, remaining native map callbacks, managed video assets
and signed Tencent configuration requests have separate follow-up owners. Native
HTTP alone does not establish complete async or hardware qualification.

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

## Realtime notice occurrences

Snapshots expose `notification_events`, an immutable tuple of `MowerNoticeEvent`
values. It retains up to 64 recent person-detection announcements on the confirmed
MOVA LiDAX Ultra 800 and Dreame A3 AWD 1000 models while they report mowing.
Each occurrence includes `stream_id`, `sequence`, `received_at`, `code`, `name`,
`tier`, and `source`. Track `(stream_id, sequence)` in your consumer to process
each occurrence once across repeated snapshots; timestamps alone are not unique.
Readers do not consume each other's events.

MQTT reconnect resets message-ID deduplication and retains occurrences so queued
updates can still reach their consumers. Sequences continue increasing, while
replacing the device creates a new stream identifier.
Polling the same notice creates no occurrence. Receipt time is local UTC epoch
time, not a firmware detection timestamp, and this buffer is not an archive of
announcements made while disconnected.

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
    allow_stored=True,  # Reuse only objects attributed to map 0.
    allow_unscoped_stored=False,  # Keep the unindexed 99.20 object out.
)
print(download.metadata.as_dict())

# Persist geometry only when you explicitly need a local PCD file.
Path("garden-map.pcd").write_bytes(download.content)
```

With `allow_stored=True`, the method first tries stored objects whose map index
is confirmed by the app inventory or the mower's `OBJ` response. The separate
`allow_unscoped_stored` option controls the object announced through cloud
property `99.20`; keep it false when more than one map exists because that
announcement has no map identity. Its default is `None`, which follows
`allow_stored` for compatibility with single-map callers. If no safely
attributed object is available, the client triggers app action `o:10`, captures
the fresh LiDAR object, and resolves its short-lived download immediately. Some
A2 firmware refreshes a stable announcement name without changing its property
timestamp; the client accepts that object only after a live post-request
indexed-object read proves it belongs to the requested map and pre/post object
evidence proves it was refreshed.
Firmware without the announcement continues through the transient `OBJ` 3D-map
fallback. Every path enforces
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
