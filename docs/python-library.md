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
        print(snapshot.state_name)
        print(snapshot.battery_level)
    finally:
        await client.async_close()


asyncio.run(main())
```

The same flow is available as `examples/python_client.py`.

## Useful Client Features

- account discovery for Dreamehome and MOVAhome accounts
- normalized mower snapshots with state, activity, battery, errors, firmware,
  and capability data
- read-only schedule retrieval and calendar-friendly task summaries
- dry-run schedule enable/disable planning, with explicit gates required before
  live writes
- read-only app-map retrieval, all-map summaries, and simple map rendering
- weather/rain-protection and mowing-preference diagnostics
- firmware/update evidence gathering without claiming unverified OTA support
- guarded remote-control support helpers for supervised short movement pulses
- reusable payload decoders for app realtime/status keys

## Safety Defaults

Prefer read-only calls while investigating a mower. Methods and examples that
can move the mower or change mower settings use explicit execution flags,
confirmation flags, or state guards. Do not run live movement or write probes
from automations.

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
- `examples/schedule_probe.py`
- `examples/schedule_write_probe.py`
- `examples/weather_probe.py`
- `examples/preference_probe.py`
- `examples/task_status_probe.py`
- `examples/remote_control_probe.py`
