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
- experimental read-only Python schedule decoder
- supervised remote-control Home Assistant services for validation
- start mowing, pause, and dock
- diagnostics, debug snapshot capture, and HACS-ready repo structure

Under the hood, this version reuses the already reverse-engineered mower protocol internals so we can test against real devices now while continuing to clean up the public architecture.

## Development

For a future-agent snapshot of the current implementation state, live-device
findings, safe probe commands, and known gaps, read
[`docs/agent-handoff.md`](docs/agent-handoff.md).

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

Downloaded diagnostics also include a `state_reconciliation` section. Start
there when Home Assistant shows a confusing state such as `Error` while another
field says `No error`: it lists the normalized state, raw mower state, error
signals, dock/charge flags, and any source-disagreement warnings.

Diagnostics also include a compact `triage` section. That is the first thing to
paste into an issue because it summarizes the model, capabilities, state
warnings, unknown property counts, unknown realtime keys, and the best next
capture to take. The larger raw sections remain in the downloaded diagnostics
for parser fixes and fixture-driven tests.

The normal `Error` sensor is cleaned for humans and falls back to known numeric
error-code labels when the vendor text says `No error`. The diagnostic `Raw
Error` sensor keeps the vendor text exactly as reported so odd captures can
still be investigated.

Unknown-property and realtime summaries also include value-type counts,
map-candidate previews, and decoded `1.1` status-blob metadata where available.
Those fields are especially useful when comparing A2/A2 Pro captures from
different mower states.

For state automations, prefer the `Activity` sensor when you need the
integration's normalized activity exactly as the Python client sees it:
`docked`, `idle`, `paused`, `mowing`, `returning`, or `error`. The normal
`State Name` sensor keeps the vendor state label, and the diagnostic `Mower
State` sensor preserves the raw app/realtime state when present.

Home Assistant also exposes `Capture Operation Snapshot`. Use it during field
tests because it logs one sanitized, grouped payload with normalized state,
realtime properties, decoded status blob, map diagnostics, firmware/update
evidence, remote-control support, and the current manual-drive safety reason.
It is read-only and does not start mowing, camera streaming, remote control, or
docking.

Firmware/update diagnostics are intentionally evidence-first. The client keeps
`update_available` unknown unless a verified mower OTA field is found, because
Dreame currently exposes conflicting `pluginForceUpdate` values that look like
mobile-app/plugin metadata. Diagnostics include per-source plugin flags and
`candidate_update_fields` so new mower models can reveal the real OTA signal
without dumping full cloud payloads.

For automations, use the normal `Docked` binary sensor or `docked` attribute.
Those are effective values derived from mower state and charging states. The
disabled-by-default `Raw Docked Flag` diagnostic entity preserves the exact
vendor flag when you need to debug dock-contact wobble.

Use the normal `Charging` binary sensor when you need an automation-friendly
charge signal. It treats explicit charging states as charging even if the
vendor boolean lags behind. The disabled-by-default `Raw Charging Flag`
diagnostic entity keeps the exact vendor value for comparison.

The same pattern applies to `Task Active` / `started` and `Returning` /
`returning`: normal entities are automation-friendly effective values, while
`Raw Started Flag` and `Raw Returning Flag` preserve sticky vendor flags for
debugging.

If Home Assistant logs contain a `Captured Dreame lawn mower ...` line, convert it to clean JSON before adding a fixture:

```bash
python examples/extract_ha_payload.py home-assistant.log --out tests/fixtures/new_capture.json
```

Use `--summary` first when triaging a large capture:

```bash
python examples/extract_ha_payload.py home-assistant.log --summary
```

Use `--kind map_probe` or `--kind schedule_probe` for focused probe logs,
`--all` if one log file contains multiple captures, or combine
`--all --summary` to get a compact triage list. The extractor also understands
operation snapshot logs from `Capture Operation Snapshot`, including
raw/effective state flags, manual-drive safety, and triage recommendations.
Schedule probe summaries include the active-version schedule selection so
hidden default or other-map slots are easier to distinguish from visible
calendar events.

## Map experiments

The reusable Python client now includes an experimental read-only map path:

- `async_refresh_map_view()` fetches one reusable map view with source, summary, image bytes, and error metadata
- `async_refresh_map_summary()` tries to fetch current mower map metadata
- `async_get_map_png()` tries to render the current mower map to PNG bytes
- `async_get_app_maps()` fetches mower-native app map JSON through confirmed read-only `MAPL`, `MAPI`, and chunked `MAPD` app commands
- `async_get_app_map_objects()` fetches read-only 3D map object metadata through `OBJ type=3dmap`; expiring download URLs are opt-in
- `async_refresh_map_view()` uses the confirmed app-map JSON first and renders a simple vector PNG; the older legacy current-map path remains a fallback

The quickest way to try it outside Home Assistant is:

```bash
python examples/map_client.py
```

The confirmed A2 app-map path is:

```bash
python examples/app_map_probe.py --out app-map-current.json
```

By default this omits raw coordinates and writes only metadata plus summaries.
Use `--include-payload` only for local parser or renderer work. Use
`--include-object-urls` only when you intentionally need expiring 3D object
download URLs in the ignored local output file. Use `--skip-objects` when you
only want the 2D map payload.

App-map summaries intentionally keep unknown map layers neutral. `spot` is
reported as spot-mowing areas, while `semantic` is summarized as
`semantic_count`, `semantic_boundary_point_count`, and `semantic_key_counts`
without labeling it as no-go or restriction data until more mower captures prove
the meaning.

## Schedule experiments

The reusable Python client can read mower-native schedules through confirmed
read-only app commands:

- `async_get_app_schedules()` fetches schedule metadata and decoded schedule plans
- `async_set_app_schedule_plan_enabled()` builds a dry-run write plan by default
- `SCHDIV2` returns schedule size/version metadata
- chunked `SCHDDV2` returns schedule JSON
- `SCHDT` returns the current or next scheduled task window

The quickest focused probe is:

```bash
python examples/schedule_probe.py --out schedule-probe-current.json
```

Preview the same decoded schedules as Home Assistant calendar events without a
running Home Assistant instance:

```bash
python examples/schedule_calendar_preview.py --from-file schedule-probe-current.json --timezone Europe/Warsaw --out schedule-calendar-preview.json
```

The Home Assistant calendar follows the schedule version reported by `SCHDT`
when available, so stale/default/other-map schedule slots do not appear as
normal mowing events. For diagnostics, add `--include-all-schedules` to the
preview command to show every decoded schedule slot.

Schedule write support is intentionally guarded. This builds the app-side
`SCHDSV2` enable/disable request without sending it:

```bash
python examples/schedule_write_probe.py --map-index 0 --plan-id 0 --disable --out schedule-write-dry-run.json
```

Only send a write request during a supervised test window, after reviewing the
dry-run JSON:

```bash
python examples/schedule_write_probe.py --map-index 0 --plan-id 0 --disable --execute --confirm-schedule-write
```

A no-op `SCHDSV2` write was validated live on an A2 on 2026-04-19 by disabling
an already disabled plan and reading the schedule back unchanged. Do not use
the execute flags from automations or unsupervised scripts.

Home Assistant now exposes a read-only `Schedule` calendar entity backed by the
same app schedule commands. Calendar event queries fetch current app schedule
data on demand, and the diagnostic `Scheduled Task` binary sensor still reports
whether the mower says a scheduled task is currently active.
A disabled-by-default diagnostic `Capture Schedule Probe` button can log decoded
schedule JSON from Home Assistant without including raw schedule payload text.
The guarded `dreame_lawn_mower.set_schedule_plan_enabled` service is also
available for enable/disable testing. It defaults to dry-run and only sends the
write request when both `execute: true` and `confirm_schedule_write: true` are
set. Dry-run and executed notifications include the previous enabled state,
target state, whether the request changes anything, the schedule version, and
the exact `SCHDSV2` request payload.

If you want to probe the same cloud endpoints the Dreamehome app exposes for mower discovery and raw properties, use:

```bash
python examples/cloud_probe.py
```

Optional:

- the probe prints `device/info`, `queryDevicePermit`, `devOTCInfo`, and a `device/listV2` summary by default
- `queryDevicePermit` is useful for confirming app-side feature and permit flags before exposing new Home Assistant entities
- set `DREAME_PROP_KEYS=6.1,6.3` or another comma-separated key list to query `iotstatus/props`

When researching a newer Dreamehome APK, build a compact string index first:

```bash
python examples/apk_research.py "C:\path\to\dreamehome.apk" --max-string-length 220
```

This is not a full decompiler. It scans dex/assets/resources for protocol terms,
endpoints, and camera/map hints so live mower probes are guided by app evidence
instead of random payload guesses.

If the compact APK scan is too thin, decompile the APK with `jadx` and scan the
source tree for file/line snippets:

```bash
jadx -d C:\path\to\dreamehome-jadx C:\path\to\dreamehome.apk
python examples/source_research.py "C:\path\to\dreamehome-jadx" --term STREAM_VIDEO --term operType
```

Or run the guarded wrapper, which detects `jadx`, refuses to overwrite an
existing output folder unless `--overwrite` is passed, and then scans the result:

```bash
python examples/decompile_research.py "C:\path\to\dreamehome.apk" --output-dir "C:\path\to\dreamehome-jadx"
```

If you want to scan wider `siid.piid` ranges when hunting for map or telemetry keys, use:

```bash
python examples/property_probe.py --out property-scan-current.json
```

Optional:

- use `--keys 2.1,2.2,6.1` to probe an explicit list
- or use `--siids 1,2,6 --piid-start 1 --piid-end 40`
- `DREAME_PROP_KEYS`, `DREAME_PROP_SIIDS`, `DREAME_PROP_PIID_START`, and
  `DREAME_PROP_PIID_END` remain supported as environment-variable defaults
- `1.1`, `2.1`, and `2.2` are automatically annotated as raw status blob, mower state, and mower error
- `2.1` is labeled with mower state names extracted from the Dreamehome app asset bundle
- `2.2` is labeled with cleaned mower error names when the error code is already known
- blob-like values are annotated with `value_bytes_len` and `value_bytes_hex`
- the returned `summary` groups non-empty keys, unknown non-empty keys, decoded-label sources, value-type counts, and map-candidate payloads
- non-empty values are shown by default; pass `--all` to include empty key-only responses

This is still experimental and read-only. The integration exposes disabled-by-default `camera` entities named `Map` and `Map Diagnostics`. `Map` returns a rendered JPEG from the app-map source when available, or a valid placeholder image. Its attributes include `map_source`, `map_id`, dimensions, segment counts, path-point counts, `spot_area_count`, and `no_go_area_count`. In the confirmed A2 app payload, `spot` means spot-mowing areas; it is not a no-go zone list. `Map Diagnostics` returns a readable JPEG diagnostics card and keeps the structured map view in entity attributes so Home Assistant no longer tries to render JSON as a broken camera preview.

There is also a disabled-by-default `Capture Map Probe` button. Use it when the visible map is still a placeholder: it logs a compact JSON payload with the legacy current-map result, focused app-style property probes, trimmed cloud metadata from `device/info` and `device/listV2`, the app-side `queryDevicePermit` feature payload, and a payload-free summary of the confirmed app-map path.

The map probe includes a `cloud_property_summary` section so large logs are easier to triage. Start there first: it lists non-empty keys, unknown non-empty keys, decoded labels and sources, hinted keys, value-type counts, map-candidate payload previews, and blob lengths before you inspect the full `cloud_properties.entries` payload.
It also includes `cloud_property_history_summary` for the legacy map history
keys `6.1`, `6.3`, and `6.13`, which helps distinguish "no map history
records" from "history records exist but decode/render failed".
The same probe now also records a compact `cloud_device_otc_info` summary from
the app's `devOTCInfo` endpoint, redacted to top-level shape and preview data.

```bash
python examples/map_sources_probe.py --out map-sources-current.json
```

## Camera and photo experiments

Some A2-family cloud records advertise camera-related permits such as `video` and `aiobs`, but starting streams or taking photos is an active operation. The reusable Python client therefore exposes a read-only capability check first:

```bash
python examples/camera_feature_probe.py
```

The probe reports protocol mappings for stream/photo properties and actions, app-side permit metadata, cached stream session/status state, and a compact `queryDevicePermit` summary. It does not start video, audio, lights, or photo capture.

To inspect the app-style `10001.*` stream property family from both cloud and device property reads, use:

```bash
python examples/camera_sources_probe.py
```

Set `DREAME_CAMERA_PROBE_DEVICE_PROPERTIES=0` if you only want cloud `iotstatus/props` data. The default still only performs property reads; it does not call stream actions.

After the read-only probe confirms support, a narrower safety-gated metadata request is available:

```bash
python examples/photo_info_probe.py --execute
```

Without `--execute` the script only prints support details. With `--execute` it calls `GET_PHOTO_INFO` once; it still does not start streaming, audio, remote control, or mowing.

The next safety-gated probe tries the probable app stream handshake:

```bash
python examples/camera_stream_handshake_probe.py --execute
```

This calls `STREAM_VIDEO` with `STREAM_STATUS` and a short `monitor` start/end pair, then polls for a session/status blob. It is intentionally blocked while the mower is active, returning, or mapping.

If the default payload is rejected, test the app-style payload without a `session` key:

```bash
python examples/camera_stream_handshake_probe.py --execute --payload-mode no_session
```

## Remote control experiments

The reverse-engineered protocol exposes remote control through property `4.15`.
The reusable Python client can now report support with
`async_get_remote_control_support()` and can send one validated movement step
with `async_remote_control_move_step(rotation=..., velocity=..., prompt=...)`.
In support payloads, `supported` means the protocol surface exists and
`state_safe` means the mower's current state is safe enough for a nonzero
manual-drive step. Stop commands remain allowed so a caller can always send a
zero-motion command.

The read-only support check is:

```bash
python examples/remote_control_probe.py
```

To save a read-only support payload without committing it:

```bash
python examples/remote_control_probe.py --out remote-control-current.json
```

The safety-gated live smoke test is:

```bash
python examples/remote_control_smoke.py --execute --velocity 60 --rotation 60 --duration 0.5 --dock
```

Add `--out remote-control-smoke.json` when you want to keep the structured
before/after support and step evidence locally without committing it.

The more conservative live A2 validation used `velocity=30`, `rotation=25`,
`duration=0.35`, and `settle=1.5` with a stop command before the first pulse and
after every forward, turn-right, turn-left, and backward pulse. That sequence
ended with a dock request and the mower returned to a docked charging state.
Treat those as observed safe smoke-test values, not as a full command-range
calibration.

For a more useful field-trip capture, use the operation snapshot helper. In
read-only mode it captures normalized state, realtime status blob, remote
control support, and optional map/update evidence without moving the mower:

```bash
python examples/field_trip_probe.py --include-map --include-firmware
```

To inspect only firmware/update metadata, run the dedicated read-only probe:

```bash
python examples/firmware_update_probe.py
```

When the mower is outdoors and supervised, the same script can wrap tiny
movement pulses with before/during/after captures:

```bash
python examples/field_trip_probe.py --execute --confirm-supervised --velocity 60 --rotation 45 --duration 0.5 --dock --include-map --out field-trip.json
```

The reusable client and both live movement scripts refuse nonzero movement while
the mower appears to be mowing, returning, mapping, fast mapping, in error, or
below 20% battery.

Home Assistant also exposes supervised service calls for validation:

```yaml
action: dreame_lawn_mower.remote_control_step
data:
  rotation: 0
  velocity: 60
  prompt: false
```

Stop manual driving with:

```yaml
action: dreame_lawn_mower.remote_control_stop
```

If you have more than one mower entry loaded, include `entry_id`. The movement
service is intentionally guarded: it refuses to move while the mower appears to
be mowing, returning, mapping, fast mapping, in error, or below 20% battery.
Keep using short supervised pulses until wider command ranges are fully
validated on real hardware.

The `Manual Drive Safe` diagnostic binary sensor mirrors the same state guard
used by the service. It does not prove that the protocol exposes remote control;
it only tells you whether the current mower state is safe enough to attempt a
supervised manual-drive step. Enable the diagnostic `Manual Drive Block Reason`
sensor when you want the exact guard reason in dashboards or traces.

## Automation examples

The normalized sensors and binary sensors are intended to keep automations out
of mower attributes as much as possible. Entity IDs below are examples; use the
ones created by your Home Assistant instance.

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

Keep the garage open while the mower is returning, then close it only after the
effective docked and charging signals agree:

```yaml
automation:
  - alias: Dreame mower keeps garage open while returning
    triggers:
      - trigger: state
        entity_id: binary_sensor.dreame_a2_bodzio_returning
        to: "on"
    actions:
      - action: cover.open_cover
        target:
          entity_id: cover.garage_door

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

Use `Activity` when you prefer one normalized state sensor instead of several
binary sensors:

```yaml
automation:
  - alias: Dreame mower garage follows activity
    triggers:
      - trigger: state
        entity_id: sensor.dreame_a2_bodzio_activity
    actions:
      - choose:
          - conditions:
              - condition: state
                entity_id: sensor.dreame_a2_bodzio_activity
                state: "mowing"
            sequence:
              - action: cover.open_cover
                target:
                  entity_id: cover.garage_door
          - conditions:
              - condition: state
                entity_id: sensor.dreame_a2_bodzio_activity
                state: "returning"
            sequence:
              - action: cover.open_cover
                target:
                  entity_id: cover.garage_door
          - conditions:
              - condition: state
                entity_id: sensor.dreame_a2_bodzio_activity
                state: "docked"
              - condition: state
                entity_id: binary_sensor.dreame_a2_bodzio_charging
                state: "on"
            sequence:
              - action: cover.close_cover
                target:
                  entity_id: cover.garage_door
```

For manual-drive testing dashboards, put `Manual Drive Safe` next to the
service buttons and enable `Manual Drive Block Reason` while validating. Keep
the service buttons supervised; the sensor is a guardrail, not a joystick UI.
