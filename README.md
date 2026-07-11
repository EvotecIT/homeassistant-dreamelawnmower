# Dreame Lawn Mower for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-CUSTOM-41BDF5?style=for-the-badge&labelColor=555)](https://hacs.xyz/)
[![Validate](https://img.shields.io/github/actions/workflow/status/EvotecIT/homeassistant-dreamelawnmower/validate.yml?branch=main&style=for-the-badge&label=VALIDATE&labelColor=555)](https://github.com/EvotecIT/homeassistant-dreamelawnmower/actions/workflows/validate.yml)
[![Hassfest](https://img.shields.io/github/actions/workflow/status/EvotecIT/homeassistant-dreamelawnmower/hassfest.yml?branch=main&style=for-the-badge&label=HASSFEST&labelColor=555)](https://github.com/EvotecIT/homeassistant-dreamelawnmower/actions/workflows/hassfest.yml)
[![License](https://img.shields.io/badge/LICENSE-MIT-yellow?style=for-the-badge&labelColor=555)](LICENSE)

Custom Home Assistant integration for Dreame and MOVA robotic lawn mowers.

The integration uses the cloud/app protocol exposed by Dreamehome and MOVAhome.
It is being developed against real A2-family hardware and is intentionally
conservative with anything that can move the mower or change mower settings.

![Dreame Lawn Mower device overview](assets/dreame-lawn-mower-overview.png)

## Screenshots

![Rendered mower map](assets/dreame-lawn-mower-map.png)

![Diagnostics and map entities](assets/dreame-lawn-mower-diagnostics.png)

## Status

This project is usable, but still young. Core mower state, controls, schedules,
maps, and diagnostics are available. Some features remain diagnostic or
disabled by default while the protocol is validated across more models.

## Support Matrix

Support levels in this table mean:

- `Validated`: exercised against real hardware and fixtures in this repository
- `Recognized`: model strings, account types, or rebadges are known and should
  degrade gracefully, but still need more real-world confirmation
- `Needs reports`: intended target, but not yet proven enough to claim support

| Scope | Status | Notes |
| --- | --- | --- |
| Dreame A2 (`dreame.mower.g2408`) | Validated | Primary live development device, including schedules, maps, remote control, guarded preference writes, and diagnostics |
| MOVA LiDAX Ultra 1000 (`mova.mower.g2529c`) | Recognized | Added from a MOVAhome EU diagnostics report; commands and battery are reported working, state handling now accepts model-specific cloud property ids |
| Dreame A3 AWD Pro 3500 (`dreame.mower.g2541e`) | Recognized | Added from a Dreamehome EU diagnostics report; needs broader live confirmation before it is considered validated |
| Newer A-series mower (`dreame.mower.g3255`) | Recognized | Raw model has been observed in code mapping, but the public retail name is still unverified |
| Dreame A1 (`dreame.mower.p2255`) | Recognized | Model mapping is present; needs fixtures and live validation |
| Dreame A1 Pro (`dreame.mower.g2422`) | Recognized | Model mapping is present; needs fixtures and live validation |
| MOVAhome accounts | Recognized | Login flow and account type are supported; needs broader live confirmation |
| MOVA-branded mower rebadges | Needs reports | Expected to follow the same protocol family, but still needs sanitized fixtures and user reports |
| Regional / firmware variants of known A-series models | Needs reports | Should avoid crashing, but behavior can still vary by firmware and region |

Current live validation is still centered on:

- Dreamehome account in the EU region
- A2-family hardware

If you have a mower model not listed as `Validated`, please open an issue or PR.
Model reports with sanitized diagnostics, screenshots, raw model identifiers, and
region/account details are especially helpful for moving a device from
`Recognized` or `Needs reports` to `Validated`.

## Features

- UI config flow with Dreamehome or MOVAhome account login
- automatic mower discovery from the cloud account
- `lawn_mower` entity for start, pause, and dock
- button to dock without ending the current mowing session
- heartbeat-backed task status and automatic resume of a paused mowing session
- battery, activity, state, task, firmware, and error sensors
- current-map selector entities for map, mowing action, edge, zone, and spot scope
- current-map services for switching maps and starting explicit zone, spot, or edge runs
- binary sensors for docked, charging, mowing, paused, returning, and error state
- binary sensors for active and resumable mowing sessions
- binary sensor for Bluetooth-connected runtime state
- read-only schedule calendar using the mower-native app schedule protocol
- disabled-by-default all-schedules calendar for default and per-map schedule diagnosis
- guarded schedule enable/disable service with dry-run mode by default
- guarded mowing-preference update service with dry-run mode by default
- read-only map camera using the app-map payload when available
- disabled-by-default all-maps and map-diagnostics cameras
- live video camera with a managed XP2P runtime on Linux x86_64 and aarch64 hosts
- runtime telemetry sensors for mission progress, mission area, mower pose, and live-track length
- selected-run sensors for mowing action, chosen map, and scoped zone/spot/edge target
- selected-zone preference sensors for read-only mowing height, efficiency, direction, and obstacle-avoidance details
- read-only weather/rain-protection diagnostics
- read-only weather/rain-protection entities from cached app settings
- read-only mowing-preference diagnostics
- supervised remote-control service for short validation pulses
- sanitized diagnostics and debug snapshot helpers
- cloud presence checks that make entities unavailable instead of showing stale
  mower values while the device is offline

## Not Yet Public-Ready Features

The following areas are intentionally cautious:

- firmware OTA now exposes a Home Assistant update entity using the app's
  approved `checkDeviceVersion` target and `manualFirmwareUpdate` approval
  step, but release notes remain best-effort because the live A2 endpoint still
  embeds a `missing lang` error string in the changelog field
  the live Dreame A2 verification on April 22, 2026 completed from
  `4.3.6_0320` to `4.3.6_0447`
- a read-only debug OTA catalog probe exists for version-trace work, but it is
  not treated as authoritative latest-version, changelog, or install approval
  data because it is a manual catalog rather than the mobile app's approved OTA
  response
- firmware diagnostics now include those debug-catalog candidate versions when
  available, so operation snapshots can show plausible newer builds without
  conflating them with the app-approved update target
- rain-protection writes are not exposed yet
- mowing-preference writes are guarded, validated on a supervised A2 no-op write, and still need broader model and firmware validation
- map rendering is read-only; no-go editing, virtual-wall editing, and other map
  editing flows are not exposed yet
- live video has been validated end to end on a Dreame A2, including Home
  Assistant HLS playback; other Tencent-video mower models and firmware still
  need field validation
- the managed video runtime currently supports Linux x86_64 and aarch64 Home
  Assistant hosts, and the mower must be active and away from its station before
  the vendor permits live video
- 3D map object downloads are metadata-first and not treated as stable
- manual driving must stay supervised and uses strict state and battery guards

## Installation

### HACS

1. Add this repository to HACS as a custom integration repository:

   [![Open your Home Assistant instance and open this repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=EvotecIT&repository=homeassistant-dreamelawnmower&category=integration)

   If the button does not open your Home Assistant instance, manually add this
   repository URL in HACS:

   `https://github.com/EvotecIT/homeassistant-dreamelawnmower`

2. Install **Dreame Lawn Mower** from HACS.
3. Restart Home Assistant.
4. Add the integration from **Settings -> Devices & services**:

   [![Open your Home Assistant instance and start setting up Dreame Lawn Mower.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=dreame_lawn_mower)

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

### Live video

On Linux x86_64 and aarch64 Home Assistant hosts, the `Live Video` camera uses
the managed runtime by default. No Android phone, emulator, library path, or
external runner is required. The integration prepares the runtime during entity
setup, starts it when Home Assistant requests the camera, verifies the local FLV
source, and stops it when the camera is turned off or unloaded.

The first setup needs internet access. The integration downloads fixed versions
of Tencent XP2P, the required AOSP Bionic libraries, and qemu-user-static on
x86_64 hosts. Every file is pinned and SHA-256 verified before use, then cached
under Home Assistant's `.storage` directory. The Home Assistant/Python client
owns the lifecycle; Tencent's proprietary P2P transport still runs in the small
native compatibility worker. It does not require an Android device or Android
framework.

The Dreame A2 proof uses a normal copied `custom_components` installation, the
real Home Assistant mower and camera entities, and Home Assistant's HLS output.
The retained H.264 MP4 reopened independently as 640 x 360 video and decoded
100 frames spanning 6.599 seconds. The HA camera entity also returned a real
JPEG through the integration's PyAV/Pillow still-image path, even without the
optional TurboJPEG system library, and that frame was visually inspected. This
is a pixel-level playback proof, not only an FLV header or byte-count check.

The mower vendor only allows video while the mower is active and away from its
station. Requesting the camera does not start or move the mower. The existing
native-library and persistent-runner options remain available as advanced
overrides for development or unsupported host platforms.

The integration's options expose three video transport policies. The default
keeps the proven XP2P-compatible path. `Auto` first tries Tencent's separate
same-LAN service and then uses normal XP2P with direct-capable AUTO negotiation.
`Same-LAN only` never falls back, but it requires firmware that advertises a LAN
endpoint. The camera's `last_stream_session` attribute reports `stream_route`
as `direct` only when the separate LAN service was selected; otherwise it stays
`unknown`. Tencent's misleadingly named `getStreamLinkMode` API returns a
network/NAT-type bitmask, exposed as `sdk_stream_network_type`, rather than a
direct-versus-relay result.

After a successful cloud-provisioned stream, `Auto` privately caches the minimum
XP2P identity, P2P material, QCloud/app credentials, and resolved device
configuration under Home Assistant's `.storage`. The cache uses Home Assistant's
private-store permissions and deliberately excludes the Dreame access token,
LAN discovery token, and raw cloud responses. On a later restart, `Auto` tries
that cache before any Dreame video-input or camera-toggle call and refreshes it
through the normal path if the cached material has expired.

This proof is intentionally narrower than every camera feature in the vendor
apps:

- The tested A2 sent normal-XP2P AUTO media directly between the Home Assistant
  host and the mower's same-LAN IP. A retained socket trace includes the direct
  peer address, FLV request, HTTP 200 response, and media bytes, so this does not
  depend on an SDK label. Tencent's separate WLAN discovery and
  `startLanService` path was also implemented, but this A2 firmware did not
  answer that discovery request. Dreame/Tencent cloud calls still provide the
  initial provisioning. A second copied-HA proof deliberately failed the entire
  Dreame client during config-entry reload: the integration entered cached
  camera-only mode, produced another HLS HTTP 200 response, and independently
  decoded 100 more frames without fetching runtime inputs or toggling video in
  Dreame cloud. Tencent XP2P can still use its internet rendezvous/STUN control
  plane to establish the direct peer route, so this is not a claim that video
  starts with all internet connectivity removed.
- Home Assistant can display and save the current JPEG frame, but the vendor's
  stored photo gallery is not exposed.
- Live video is field-validated on the A2 only. A3 AWD Pro and MOVA camera
  variants still need their own runtime-input and playback proof.
- Patrol movement, arbitrary voice-prompt playback, and two-way live talk are
  separate control/audio features and are not implemented by this camera.

## Help Expand Support

Support across Dreame, MOVA, and rebadged mower variants will improve fastest
with real-world reports. If your mower is recognized but not yet validated, or
if it exposes a different raw model string than this README shows, please open a
GitHub issue or PR with:

- the retail product name and raw app/cloud model identifier
- account type (`dreame` or `mova`) and region
- a sanitized diagnostics capture or Home Assistant debug snapshot
- screenshots of the product page, app model name, or device information page
- notes about what works, what is missing, and any errors you see

Please redact credentials, tokens, serial numbers, exact coordinates, and any
other secrets before attaching files.

## Entities

The primary entity is:

- `lawn_mower.<device>`

Common user-facing helpers include:

- `sensor.<device>_activity`
- `sensor.<device>_state_name`
- `sensor.<device>_error`
- `sensor.<device>_battery`
- `sensor.<device>_mowing_progress`
- `sensor.<device>_selected_mowing_action`
- `sensor.<device>_selected_map`
- `sensor.<device>_selected_target`
- `sensor.<device>_selected_zone_mowing_height`
- `sensor.<device>_selected_zone_efficiency_mode`
- `sensor.<device>_selected_zone_direction_mode`
- `sensor.<device>_selected_zone_obstacle_avoidance`
- `sensor.<device>_selected_zone_obstacle_distance`
- `sensor.<device>_selected_zone_obstacle_height`
- `sensor.<device>_selected_zone_obstacle_classes`
- `sensor.<device>_runtime_mission_progress`
- `sensor.<device>_runtime_current_area`
- `sensor.<device>_runtime_total_area`
- `sensor.<device>_runtime_live_track_length`
- `sensor.<device>_runtime_live_track_point_count`
- `sensor.<device>_weather_protection_status`
- `select.<device>_map`
- `select.<device>_mowing_action`
- `select.<device>_edge`
- `select.<device>_zone`
- `select.<device>_spot`
- `binary_sensor.<device>_docked`
- `binary_sensor.<device>_charging`
- `binary_sensor.<device>_bluetooth_connected`
- `binary_sensor.<device>_mowing`
- `binary_sensor.<device>_task_active`
- `binary_sensor.<device>_task_resumable`
- `binary_sensor.<device>_rain_delay_active`
- `binary_sensor.<device>_returning`
- `calendar.<device>_schedule`
- `camera.<device>_live_video` on supported Linux hosts

Many reverse-engineering and validation helpers are disabled by default. Enable
them from the entity registry only when troubleshooting:

- map and all-map cameras
- map diagnostics camera
- runtime pose / heading / segment-count sensors
- all-schedules calendar
- rain delay end time sensor
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

`dreame_lawn_mower.plan_mowing_preference_update` is dry-run first. It reads
the current app preference payload, applies the requested field changes
locally, and exposes the candidate `PRE` request in a notification plus the
disabled-by-default `Last Preference Write` diagnostic sensor. It sends a live
preference write only when both `execute: true` and
`confirm_preference_write: true` are provided.

The guarded preference fields include per-zone safe edge mowing through
`edge_mowing_safe`. Use the dry-run result to inspect the candidate payload
before confirming a live write.

## Maps

The map camera uses the confirmed app-map JSON path first. The renderer is
read-only and produces a simple Home Assistant camera image from the decoded map
payload.

If the mower has multiple maps, enable the disabled `All Maps` camera to render
a contact sheet. Use `Map Diagnostics` when the map image is missing or when you
need source, counts, and parser evidence.

Current map support now includes:

- a read-only `Map` camera for the active map
- a read-only `All Maps` contact sheet for quick map inventory
- `select` entities for map, mowing action, edge, zone, and spot scope
- services for switching the active mower map and starting explicit zone, spot,
  or edge jobs
- runtime live-track telemetry surfaced through sensors and map-camera attributes
- circular and rotated rectangular forbidden areas rendered from their compact
  mower map representation

Interactive map editing is still intentionally out of scope for now:

- no-go editing
- virtual-wall editing
- zone geometry edits
- other direct map mutations

## Services

The integration now exposes guarded current-map services on the `lawn_mower`
entity:

- `dreame_lawn_mower.switch_current_map`
- `dreame_lawn_mower.start_zone_mowing`
- `dreame_lawn_mower.start_spot_mowing`
- `dreame_lawn_mower.start_edge_mowing`

These use current decoded app-map and vector-map metadata. Map switching updates
the real active mower map, while zone, spot, and edge starts target explicit
current-map ids rather than relying only on the generic Home Assistant
`start_mowing` action.

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

## Reusable Python Package

This repository ships two usable layers:

- `dreame_lawn_mower_client` for direct Python access to Dreame/MOVA mower
  cloud, app-action, schedule, map, and diagnostic APIs
- the Home Assistant integration in `custom_components/dreame_lawn_mower`

Library docs: [docs/python-library.md](docs/python-library.md)

Runnable example: [examples/python_client.py](examples/python_client.py)

Example:

```python
from dreame_lawn_mower_client import DreameLawnMowerClient

devices = await DreameLawnMowerClient.async_discover_devices(
    username=username,
    password=password,
    country="eu",
    account_type="dreame",
)

client = DreameLawnMowerClient(
    username=username,
    password=password,
    country="eu",
    account_type="dreame",
    descriptor=devices[0],
)
snapshot = await client.async_refresh()
print(snapshot.descriptor.title, snapshot.state_name, snapshot.battery_level)
```

The Home Assistant integration uses the same client package name inside the
custom component bundle, so HACS installs the protocol layer together with the
integration while scripts and tests can still import `dreame_lawn_mower_client`
directly.

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

- [Python library](docs/python-library.md)
- [Development notes](docs/development.md)
- [Roadmap](docs/roadmap.md)
- [Dreamehome protocol research](docs/dreamehome-research.md)
- [Agent handoff notes](docs/agent-handoff.md)

## Safety

Read-only probes are preferred. Anything that can move the mower or write mower
settings must remain supervised, explicitly confirmed, and safe-state guarded.
