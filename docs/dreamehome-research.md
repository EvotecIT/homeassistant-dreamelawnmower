# Dreamehome App Research

This note captures concrete findings from the real Dreamehome Android package so map work can move from guesswork to reproducible probes.

## Package confirmed

- app name: `Dreamehome`
- package: `com.dreame.smartlife`
- version: `2.5.3.0`

The app package was extracted from an XAPK so the Java and Flutter assets could be inspected locally.

## Confirmed Retrofit endpoints

These endpoints are present in the app's `DreameApi` Retrofit interface:

- `POST /dreame-user-iot/iotuserbind/device/info`
  Used by `postDeviceInfo(DeviceInfoReq)`.
- `POST /dreame-user-iot/iotuserbind/device/listV2`
  Used by `getDeviceListByMap(HashMap<String, Object>)`.
- `POST /dreame-user-iot/iotstatus/props`
  Used by `getDevicePropsByDid(DevicePropsReq)`.
- `POST /dreame-user-iot/iotuserbind/queryDevicePermit`
  Used by `getUserFeatures(UserFeatureReq)`.
- `POST /dreame-iot-com-{host}/device/sendCommand`
  Used by `sendCommand`, `sendAction`, `trySendCommand`, and `trySendActionCommand`.

These line up with the earlier hypothesis that the mower app is not relying only on the older vacuum-style `current_map` flow.

## Confirmed request DTO shape

The request DTOs in the app expose these fields:

- `DeviceInfoReq`
  `did`, `lang`
- `DevicePropsReq`
  `did`, `keys`
- `DeviceListReq`
  `current`, `size`, `lang`, `master`, `sharedStatus`

The response DTOs show that:

- `postDeviceInfo()` returns a rich `DeviceModel`
- `getDeviceListByMap()` returns `DeviceListRes -> Page -> records`
- `getDevicePropsByDid()` returns key/value entries with `key`, `value`, and `updateDate`

## Confirmed Flutter asset hints

The extracted Flutter assets add two mower-specific clues:

- `assets/home_device/common_mower_protocol.json` defines the mower state key `2.1`
- that asset maps mower values like:
  - `1 -> Working`
  - `2 -> Standby`
  - `4 -> Paused`
  - `5 -> Returning Charge`
  - `6 -> Charging`
  - `11 -> Mapping`
  - `13 -> Charging Completed`
  - `14 -> Upgrading`

The translation bundle also includes mower-specific strings that match real behavior we have already seen in testing:

- mapping guidance: "After mapping, tap the button to start mowing."
- offline recovery paths for low battery away from the station
- explicit firmware-update restart wording
- Link Module and Bluetooth fallback hints for offline mowers

## What this means for map support

The app-side evidence suggests this Python-first investigation order:

1. fetch `device/info` for the mower
2. brute-force `iotstatus/props` ranges from Python until real mower-only keys are confirmed
3. fetch `device/listV2` and inspect whether map-enabled list payloads differ from the simpler account discovery flow
4. only then decide whether the final map payload is in cloud props, `device/info`, `listV2`, or a Flutter/native side-channel

## Current unknowns

These are not proven yet:

- the exact `keys` values used by the app for mower vector maps
- whether the final map payload is delivered as `MAP.*` / `M_PATH.*` keys, another property family, or a Flutter/native bridge call
- whether docked-but-idle state suppresses part of the map payload

## First live probe result

A live A2 probe through the new Python helper confirmed that:

- `device/info` returns the expected mower payload for `dreame.mower.g2408`
- `device/listV2` returns a valid page with the expected mower record
- `iotstatus/props` accepts requests from Python
- probing legacy-looking keys `6.1`, `6.3`, `6.8`, and `6.13` while the mower was docked returned key-only entries with no values

A second live scan through `examples/property_probe.py` against a small docked range (`siid` 1, 2, and 6 with `piid` 1-8) found three non-empty entries:

- `2.1 = 13`
  The app-derived mower state label decodes this to `Charging Completed`.
- `2.2 = 31`
  This is likely another mower state or error-adjacent field and should be tracked in future scans.
- `1.1 = [206,0,0,...]`
  This looks like a compact raw status blob rather than a simple scalar property. The current Python scanner now also renders it as `20` bytes of hex: `ce000000000000000080006401ff000080d0b4ce`.

That means the endpoint is reachable, but those guessed keys are not enough by themselves to recover the map payload in the current docked state.

## Repo follow-up

The reusable Python client now includes cloud probe helpers so this research can be exercised without Home Assistant:

- `async_get_cloud_device_info()`
- `async_get_cloud_user_features()` for the app-side `queryDevicePermit` endpoint
- `async_get_cloud_device_list_page()`
- `async_get_cloud_properties(keys)`
- `async_scan_cloud_properties(...)` for chunked `siid.piid` range scans
- `build_cloud_property_summary(...)` to quickly identify non-empty, decoded, hinted, and blob-like scan results
- `mower_state_label(value)` for the app-derived `2.1` state key
- `mower_error_label(value)` for known mower error codes seen through `2.2`

Use `python examples/cloud_probe.py` to query these endpoints directly with the same credentials used by the integration.

Use `python examples/property_probe.py` to scan `siid.piid` key ranges and highlight non-empty property results while keeping `1.1`, `2.1`, and `2.2` readable.

Use `python examples/apk_research.py <apk> --max-string-length 220` when
testing a new Dreamehome APK.
It creates a compact string index of dex/assets/resources for protocol endpoints,
camera terms, stream/session terms, mower/map hints, and candidate protocol assets.
This keeps future app research repeatable without requiring a full decompiler for
the first pass.

The first pass against the locally downloaded Dreamehome APK found:

- three dex files: `classes.dex`, `classes2.dex`, and `classes3.dex`
- no obvious mower/camera/map protocol JSON assets in the base APK
- one endpoint-like string: `sendCommand`
- generic Android/framework string hits for `stream`, `camera`, `photo`, and `map`

That is still useful negative evidence. It suggests the camera/map payload
schema is either obfuscated in code, delivered dynamically, or best recovered
from app traffic while opening the feature, rather than from a simple asset file.
