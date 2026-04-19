# Dreame Lawn Mower for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://hacs.xyz/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Custom Home Assistant integration for Dreame and MOVA robotic lawn mowers.

The integration uses the cloud/app protocol exposed by Dreamehome and MOVAhome.
It is being developed against real A2-family hardware and is intentionally
conservative with anything that can move the mower or change mower settings.

## Status

This project is usable, but still young. Core mower state, controls, schedules,
maps, and diagnostics are available. Some features remain diagnostic or
disabled by default while the protocol is validated across more models.

Tested most heavily with:

- Dreame A2 (`dreame.mower.g2408`)
- Dreamehome account in the EU region

Expected to support, but still needing more fixtures and real-world reports:

- MOVA-branded accounts and mower rebadges
- additional A-series firmware and regional variants

## Features

- UI config flow with Dreamehome or MOVAhome account login
- automatic mower discovery from the cloud account
- `lawn_mower` entity for start, pause, and dock
- battery, activity, state, task, firmware, and error sensors
- binary sensors for docked, charging, mowing, paused, returning, and error state
- read-only schedule calendar using the mower-native app schedule protocol
- disabled-by-default all-schedules calendar for default and per-map schedule diagnosis
- guarded schedule enable/disable service with dry-run mode by default
- read-only map camera using the app-map payload when available
- disabled-by-default all-maps and map-diagnostics cameras
- read-only weather/rain-protection diagnostics
- read-only mowing-preference diagnostics
- supervised remote-control service for short validation pulses
- sanitized diagnostics and debug snapshot helpers

## Not Yet Public-Ready Features

The following areas are intentionally cautious:

- firmware OTA availability is reported as unknown unless a verified mower OTA
  signal is found
- preference and rain-protection writes are not exposed yet
- camera/photo/video paths are probe-only until runtime safety is clearer
- 3D map object downloads are metadata-first and not treated as stable
- manual driving must stay supervised and uses strict state and battery guards

## Installation

### HACS

1. Open HACS.
2. Add this repository as a custom integration repository.
3. Install **Dreame Lawn Mower**.
4. Restart Home Assistant.
5. Add the integration from **Settings -> Devices & services**.

### Manual

Copy `custom_components/dreame_lawn_mower` into your Home Assistant
`custom_components` directory, restart Home Assistant, then add the integration
from the UI.

## Configuration

The config flow asks for:

- account type: `dreame` or `mova`
- country/region
- username
- password

The integration stores Home Assistant config-entry data only. Do not put
credentials into repository files, fixtures, or issue attachments.

## Entities

The primary entity is:

- `lawn_mower.<device>`

Common user-facing helpers include:

- `sensor.<device>_activity`
- `sensor.<device>_state_name`
- `sensor.<device>_error`
- `sensor.<device>_battery`
- `binary_sensor.<device>_docked`
- `binary_sensor.<device>_charging`
- `binary_sensor.<device>_mowing`
- `binary_sensor.<device>_returning`
- `calendar.<device>_schedule`

Many reverse-engineering and validation helpers are disabled by default. Enable
them from the entity registry only when troubleshooting:

- map and all-map cameras
- map diagnostics camera
- all-schedules calendar
- last schedule probe/write sensors
- last task-status, weather, and preference probe sensors
- raw vendor flag sensors
- manual-drive safety diagnostics

## Schedules And Multiple Maps

Dreame A2 schedules can exist in more than one slot. Live captures have shown a
default schedule plus per-map schedules. The normal Home Assistant `Schedule`
calendar follows the active schedule version reported by the mower's `SCHDT`
response, so hidden/default/other-map schedules do not appear as normal mowing
events.

Enable the disabled `All Schedules` calendar only when you intentionally want to
inspect every decoded schedule slot.

The guarded `dreame_lawn_mower.set_schedule_plan_enabled` service is dry-run
first. It sends a write only when both `execute: true` and
`confirm_schedule_write: true` are set.

## Maps

The map camera uses the confirmed app-map JSON path first. The renderer is
read-only and produces a simple Home Assistant camera image from the decoded map
payload.

If the mower has multiple maps, enable the disabled `All Maps` camera to render
a contact sheet. Use `Map Diagnostics` when the map image is missing or when you
need source, counts, and parser evidence.

## Troubleshooting

Start with Home Assistant diagnostics:

1. Open the device page.
2. Download diagnostics.
3. Check the `triage`, `state_reconciliation`, schedule, and map sections.

For issue reports, include:

- mower model and app/account type
- firmware version
- normalized activity/state/error values
- relevant diagnostic payload sections with secrets redacted
- whether the issue happens while docked, mowing, returning, raining, or charging

Home Assistant log lines that start with `Captured Dreame lawn mower ...` can be
converted to JSON with:

```bash
python examples/extract_ha_payload.py home-assistant.log --summary
```

## Python Client Boundary

This repository contains one bundled client implementation and one public import
facade:

- `custom_components/dreame_lawn_mower/dreame_client` is the implementation used
  by the Home Assistant integration.
- `dreame_lawn_mower_client` is a thin facade for examples, tests, and future
  extraction into a standalone package.

They are not two independent clients. Keep shared protocol behavior in the
bundled implementation and expose stable imports through the facade.

## Development

Install development dependencies:

```bash
python -m pip install -e .[test]
```

Run checks:

```bash
python -m compileall dreame_lawn_mower_client custom_components tests examples
pytest
```

Useful docs:

- [Development notes](docs/development.md)
- [Roadmap](docs/roadmap.md)
- [Dreamehome protocol research](docs/dreamehome-research.md)
- [Agent handoff notes](docs/agent-handoff.md)

## Safety

Read-only probes are preferred. Anything that can move the mower or write mower
settings must remain supervised, explicitly confirmed, and safe-state guarded.
