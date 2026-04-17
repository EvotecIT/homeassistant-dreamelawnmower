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
- supervised remote-control Home Assistant services for validation
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

Downloaded diagnostics also include a `state_reconciliation` section. Start
there when Home Assistant shows a confusing state such as `Error` while another
field says `No error`: it lists the normalized state, raw mower state, error
signals, dock/charge flags, and any source-disagreement warnings.

For automations, use the normal `Docked` binary sensor or `docked` attribute.
Those are effective values derived from mower state and charging states. The
disabled-by-default `Raw Docked Flag` diagnostic entity preserves the exact
vendor flag when you need to debug dock-contact wobble.

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

Use `--kind map_probe` for map probe logs, `--all` if one log file contains
multiple captures, or combine `--all --summary` to get a compact triage list.

## Map experiments

The reusable Python client now includes an experimental read-only map path:

- `async_refresh_map_view()` fetches one reusable map view with source, summary, image bytes, and error metadata
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

- the probe prints `device/info`, `device/listV2`, and `queryDevicePermit` summaries by default
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
python examples/property_probe.py
```

Optional:

- set `DREAME_PROP_KEYS=2.1,2.2,6.1` to probe an explicit list
- or set `DREAME_PROP_SIIDS=1,2,6` plus `DREAME_PROP_PIID_START=1` and `DREAME_PROP_PIID_END=40`
- `1.1`, `2.1`, and `2.2` are automatically annotated as raw status blob, mower state, and mower error
- `2.1` is labeled with mower state names extracted from the Dreamehome app asset bundle
- `2.2` is labeled with cleaned mower error names when the error code is already known
- blob-like values are annotated with `value_bytes_len` and `value_bytes_hex`
- keep `DREAME_PROP_ONLY_VALUES=1` to hide empty key-only responses while scanning

This is still experimental and read-only. The integration exposes disabled-by-default `camera` entities named `Map` and `Map Data`. `Map` returns a rendered JPEG or a valid placeholder image. `Map Data` returns the same structured map view as JSON so we can debug the mower map pipeline and eventually support custom cards without tying the data model to the renderer.

There is also a disabled-by-default `Capture Map Probe` button. Use it when the visible map is still a placeholder: it logs a compact JSON payload with the legacy current-map result, focused app-style property probes, trimmed cloud metadata from `device/info` and `device/listV2`, and the app-side `queryDevicePermit` feature payload.

The map probe includes a `cloud_property_summary` section so large logs are easier to triage. Start there first: it lists non-empty keys, decoded labels, hinted keys, and blob lengths before you inspect the full `cloud_properties.entries` payload.

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

The reverse-engineered protocol exposes remote control through property `4.15`. The reusable Python client can now report support with `async_get_remote_control_support()` and can send one validated movement step with `async_remote_control_move_step(rotation=..., velocity=..., prompt=...)`.

The read-only support check is:

```bash
python examples/remote_control_probe.py
```

The safety-gated live smoke test is:

```bash
python examples/remote_control_smoke.py --execute --velocity 60 --rotation 60 --duration 0.5 --dock
```

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
be mowing, returning, mapping, or fast mapping. Keep using short supervised
pulses until command ranges are fully validated on real hardware.

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
