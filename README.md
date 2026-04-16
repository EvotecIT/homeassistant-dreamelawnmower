# Dreame Lawn Mower for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://hacs.xyz/)

Cloud-backed Home Assistant integration for Dreame and MOVA robotic lawn mowers, with a reusable Python client bundled in the same repository.

## What is in this repo

- `dreame_lawn_mower_client`
  Reusable Python client facade for account login, device discovery, snapshot refresh, and core mower actions.
- `custom_components/dreame_lawn_mower`
  Home Assistant integration built on top of the reusable client.

## First shipped scope

This first implementation is intentionally narrow so it can be validated on real hardware quickly:

- account-based autodiscovery through Dreamehome or MOVAhome
- config flow and reauthentication
- one `lawn_mower` entity
- normalized sensors for battery, error, firmware, state name, and task status
- normalized binary sensors for `Error Active`, `Docked`, `Paused`, `Mowing`, and `Returning`
- opt-in diagnostic sensors for unknown-property and realtime telemetry counts
- experimental Python-first map summary and PNG rendering helpers
- experimental disabled-by-default Home Assistant map camera
- start mowing, pause, and dock
- diagnostics, debug snapshot capture, and HACS-ready repo structure

Under the hood, this version reuses the already reverse-engineered mower protocol internals so we can test against real devices now while continuing to clean up the public architecture.

## Development

```bash
python -m pip install -e .[test]
python -m compileall dreame_lawn_mower_client custom_components tests examples
pytest
```

## Roadmap

The current phased backlog lives in [`docs/roadmap.md`](docs/roadmap.md). It focuses on:

- model compatibility hardening for additional mower brands and rebadges
- A2 and A2 Pro telemetry expansion from verified payloads
- mower-native vector map support
- automation-friendly state and error exposure
- app-guided reverse-engineering notes in [`docs/dreamehome-research.md`](docs/dreamehome-research.md)

Note:

- the full Home Assistant pytest stack is usually easiest in Linux CI
- live validation against the real mower still matters because the protocol is reverse engineered

## Migration note

If you previously installed an older mower custom component under the `dreame_lawnmower` name, remove it before using this integration. Keeping both installed can lead to duplicate `Dreame Lawn Mower` entries during Home Assistant setup.

## Fixture workflow

When the mower is not available for live testing, use `Capture Debug Snapshot` and `Download diagnostics` in Home Assistant to collect sanitized payloads. Those captures can be turned into repo fixtures under `tests/fixtures/` and used to extend entity coverage, capability gating, and parser regressions without requiring a mowing run.

The repo already includes paused and paused-with-wheel-error A2 captures, which lets us regression-test awkward dock-contact states without reproducing them on demand.

If you are troubleshooting a new model or strange dock behavior, enable the disabled-by-default diagnostic sensors for unknown-property count, realtime-property count, and last realtime method. They help confirm whether the mower is publishing live MQTT telemetry and whether we are seeing unmapped data that still needs decoding.

## Map experiments

The reusable Python client now includes an experimental read-only map path:

- `async_refresh_map_summary()` tries to fetch current mower map metadata
- `async_get_map_png()` tries to render the current mower map to PNG bytes

The quickest way to try it outside Home Assistant is:

```bash
python examples/map_client.py
```

If you want to probe the same cloud endpoints the Dreamehome app exposes for mower discovery and raw properties, use:

```bash
python examples/cloud_probe.py
```

Optional:

- the probe prints `device/info` and `device/listV2` summaries by default
- set `DREAME_PROP_KEYS=6.1,6.3` or another comma-separated key list to query `iotstatus/props`

If you want to scan wider `siid.piid` ranges when hunting for map or telemetry keys, use:

```bash
python examples/property_probe.py
```

Optional:

- set `DREAME_PROP_KEYS=2.1,2.2,6.1` to probe an explicit list
- or set `DREAME_PROP_SIIDS=1,2,6` plus `DREAME_PROP_PIID_START=1` and `DREAME_PROP_PIID_END=40`
- `2.1` is automatically labeled with the mower state names extracted from the Dreamehome app asset bundle
- blob-like values are annotated with `value_bytes_len` and `value_bytes_hex`
- keep `DREAME_PROP_ONLY_VALUES=1` to hide empty key-only responses while scanning

This is still experimental and read-only. The integration now also exposes a disabled-by-default `camera` entity named `Map`, which tries to fetch and cache a rendered PNG on demand while keeping normal mower polling isolated from map failures.

## Automation examples

The normalized sensors and binary sensors are intended to keep automations out of mower attributes as much as possible.

Open the garage door when mowing starts:

```yaml
automation:
  - alias: Dreame mower opens garage door
    triggers:
      - trigger: state
        entity_id: binary_sensor.dreame_a2_bodzio_mowing
        to: "on"
    actions:
      - action: cover.open_cover
        target:
          entity_id: cover.garage_door
```

Notify when the mower reports an active error:

```yaml
automation:
  - alias: Dreame mower error notification
    triggers:
      - trigger: state
        entity_id: binary_sensor.dreame_a2_bodzio_error_active
        to: "on"
    actions:
      - action: notify.mobile_app_phone
        data:
          title: Mower error
          message: >
            State: {{ states('sensor.dreame_a2_bodzio_state_name') }},
            error: {{ states('sensor.dreame_a2_bodzio_error') }}
```

Close the garage door once the mower is back on the dock and charging:

```yaml
automation:
  - alias: Dreame mower closes garage door
    triggers:
      - trigger: state
        entity_id: binary_sensor.dreame_a2_bodzio_docked
        to: "on"
    conditions:
      - condition: state
        entity_id: binary_sensor.dreame_a2_bodzio_charging
        state: "on"
    actions:
      - action: cover.close_cover
        target:
          entity_id: cover.garage_door
```
