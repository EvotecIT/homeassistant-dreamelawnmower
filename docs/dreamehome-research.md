# Dreamehome App Research

This note captures concrete findings from the real Dreamehome Android package so map work can move from guesswork to reproducible probes.

## Package confirmed

- app name: `Dreamehome`
- package: `com.dreame.smartlife`
- version: `2.5.3.0`

The app package was extracted from an XAPK so the Java and Flutter assets could be inspected locally.

## Confirmed app/cloud endpoints

These endpoints are present in the app's `DreameApi` Retrofit interface or APK
strings:

- `POST /dreame-user-iot/iotuserbind/device/info`
  Used by `postDeviceInfo(DeviceInfoReq)`.
- `POST /dreame-user-iot/iotuserbind/device/listV2`
  Used by `getDeviceListByMap(HashMap<String, Object>)`.
- `POST /dreame-user-iot/iotstatus/props`
  Used by `getDevicePropsByDid(DevicePropsReq)`.
- `POST /dreame-user-iot/iotuserbind/queryDevicePermit`
  Used by `getUserFeatures(UserFeatureReq)`.
- `POST /dreame-user-iot/iotstatus/devOTCInfo`
  Present in APK strings and reachable from the same app API host.
- `POST /dreame-iot-com-{host}/device/sendCommand`
  Used by `sendCommand`, `sendAction`, `trySendCommand`, and `trySendActionCommand`.
- `GET /dreame-product/upgrades/appplugin`
  Used by the app to fetch the dynamically downloaded model plugin. For the live
  A2 (`dreame.mower.g2408`) with Dreamehome app version code `2050300`, `os=1`
  returned Android plugin metadata for common plugin version `338`.

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

The base APK assets do **not** expose the A2 vector-map schema. A focused asset
scan of the local `2.5.3.0` extraction found only the common mower status
protocol under `home_device` and the generic React Native executor stubs under
`assets/plugin`; no bundled `M_PATH`, `current_map`, `object_name`, boundary,
polygon, or zone map artifact was present.

The dynamically downloaded Android plugin does expose the mower map protocol.
For `dreame.mower.g2408`, the live plugin metadata pointed to common plugin
`dreame.vacuum.common` version `338`. Its React Native bundle contains the map
commands used below.

## Confirmed mower map commands

The downloaded model plugin sends read-only mower map commands through the same
MIoT cloud action bridge as other app commands: method `action`, `siid=2`,
`aiid=50`, and `in=[payload]`.

Confirmed getter payloads:

- `{"m":"g","t":"MAPL"}` lists maps.
- `{"m":"g","t":"MAPI","d":{"idx":0}}` returns map metadata including `size`
  and `hash`.
- `{"m":"g","t":"MAPD","d":{"start":0,"size":400}}` returns a chunk of the
  selected/current map JSON.
- `{"m":"g","t":"MAPBI","d":{"idx":0}}` and `MAPBD` exist for backup maps, but
  are not yet wired into the client.
- `{"m":"g","t":"OBJ","d":{"type":"3dmap"}}` returns 3D map object filenames.

Do not call the plugin's `uploadMap` action from automated probes. It uses an
action-style payload (`m:"a"`) and can change device state.

## Confirmed mower schedule commands

The downloaded model plugin also exposes a schedule API through the same
`siid=2`, `aiid=50` app action bridge. Confirmed read-only getter payloads:

- `{"m":"g","t":"SCHDIV2","d":{"i":0}}` returns schedule metadata for a map:
  index `i`, payload length `l`, and version `v`.
- `{"m":"g","t":"SCHDDV2","d":{"s":0,"l":100,"v":19383}}` returns a chunk of
  schedule JSON for the requested version.
- `{"m":"g","t":"SCHDT","d":{"t":0}}` returns the current or next scheduled task
  window as minute-of-day start/end values plus plan/version identifiers.

The write-side commands also exist in the bundle. The Python client now has
dry-run-first helpers for these payload shapes:

- `SCHDSV2` with `m:"s"` changes enabled schedule status.
- `SCHDIV2` with `m:"s"` prepares a full schedule payload update.
- `SCHDDV2` with `m:"s"` uploads schedule chunks.

The reusable client now wires both the `SCHDSV2` enable/disable path and a
guarded full-upload planner for `SCHDIV2` plus chunked `SCHDDV2`. Both default
to dry-run, and sending either path requires both `execute=True` and
`confirm_write=True`.

On 2026-04-19, a supervised no-op A2 write validated the `SCHDSV2` path by
disabling map `0` plan `1`, which was already disabled. The device returned
top-level `r: 0` and payload `{"r":0,"v":19383}`. A follow-up schedule probe
confirmed map `0` still had plan `0` enabled, plan `1` disabled, and version
`19383`.

On 2026-04-21, a live A2 dry-run upload plan reused the current map `0`
schedule without sending any write. The planner rebuilt a one-chunk
`SCHDIV2`/`SCHDDV2` sequence for version `56815`, payload size `96`, and
reported `changed=false`, which confirms the full-upload planner matches the
current mower schedule when given the decoded live plan objects.

On 2026-04-19, a live A2 read-only schedule probe confirmed:

- default schedule slot `-1`: length `79`, version `31345`, one enabled plan.
- map `0`: length `96`, version `19383`, one enabled all-area mowing plan with
  a task window `10:58` to `20:57`.
- map `1`: length `96`, version `4760`, one enabled all-area mowing plan with a
  task window `10:00` to `21:01`.
- `SCHDT` returned `[658, 1257, 0, 19383]`, matching map `0` plan `0` and the
  `10:58` to `20:57` task window.

The schedule payload stores task days in a base64 binary block. The app decodes
that block into `plan_id`, `enabled`, `name`, `weeks`, and per-day task entries
with minute-of-day start/end values, type, cyclic flag, and optional regions.
The Python encoder round-trips the known live-shaped schedule payloads and can
build full upload request chunks, but full schedule editing is not exposed yet.
Home Assistant exposes decoded enabled plans through a read-only calendar
entity; calendar queries fetch the app schedule data on demand.
The normal calendar filters to the active `SCHDT` schedule version when present.
A disabled-by-default diagnostic `All Schedules` calendar includes every decoded
default and per-map schedule slot for investigation.

## Observed app mower preferences

User-provided Dreamehome A2 screenshots from 2026-04-19 show that the app
separates mowing preferences from schedule slots:

- The app exposes at least two preference scopes labelled "general mode" and
  "custom mode" in the Polish UI (`Tryb ogólny` and `Tryb własny`).
- Multiple maps can exist, and each map can carry its own schedules. This
  matches the live schedule evidence where the default slot `-1`, map `0`, and
  map `1` each had separate schedule versions.
- Visible preference groups include mowing efficiency, cutting height, mowing
  direction, automatic edge mowing, safe edge mowing, EdgeMaster, edge-obstacle
  avoidance, LiDAR obstacle recognition, obstacle avoidance height, AI obstacle
  classes for people/animals/objects, and obstacle avoidance distance.
- Some controls appear unavailable while the mower is active or in a constrained
  state. Future write-capable preference support should therefore keep the same
  style of state guards used by manual control and schedule writes.

These screenshots are UI evidence only; they do not identify the exact
app-action commands or cloud properties. Treat these preference families as
read-only discovery targets until a live probe or plugin source scan confirms
the command names, payload shapes, and whether values are global, per-map, or
per-custom-profile.

A follow-up scan of the downloaded A2 plugin bundle connected those UI controls
to the app-action preference protocol:

- Read-only commands are `PREI` (`{"m":"g","t":"PREI","d":{"idx":map}}`) for
  preference metadata and `PRE` (`{"m":"g","t":"PRE","d":{"idx":map,"region":area}}`)
  for one area/custom-region preference payload.
- Write-capable commands exist as `PRE` with `m:"s"` for settings and `PREP`
  for preference mode. The client now exposes guarded planning/execution for
  both `PRE` and `PREP`, including mode-only dry runs and combined
  `PREP` then `PRE` request sequences when switching a map into custom mode
  before applying zone-specific settings.
- The decoded payload fields line up with the UI: efficient mode, cutting
  height, mowing direction mode/direction, automatic and safe edge mowing,
  EdgeMaster/cutter position, edge obstacle avoidance, LiDAR obstacle
  avoidance, avoidance height/distance, and AI obstacle class bitmask.
- A live A2 comparison confirmed that mowing-direction degrees are stored
  directly, while `edgeMowingWalkMode` is the app's turning method (`0` lawn
  care, `1` efficient).
- Existing `mowing_direction_mode_name` and `edge_mowing_walk_mode_name`
  values remain compatibility labels. New `mowing_direction_method_name` and
  `turning_method_name` aliases expose the app terminology without breaking
  consumers of `rotation`, `line`, or `side`.
- The app logs `prop.2.52 mowing preference update`, so cloud property `2.52`
  is a useful read-only hint during future live captures.

The Python client now exposes `async_get_mowing_preferences()`,
`async_plan_app_mowing_preference_update()`, `examples/preference_probe.py`,
and `examples/preference_write_probe.py`. Home Assistant mirrors the read path
as disabled-by-default diagnostic entities (`Capture Preference Probe` and
`Last Preference Probe`) and exposes guarded dry-run-first preference write
services that require explicit execute and confirmation flags before sending a
live `PRE` request.

A live read-only A2 run of `examples/preference_probe.py` on 2026-04-19
validated the commands:

- map `0`: available, global preference mode, 5 preference areas
- map `1`: available, global preference mode, 2 preference areas
- no probe errors

Notable live decoded values included efficient/default area preferences,
cutting heights from `3.5` to `6.0` cm, mow-at-angle/none direction modes,
obstacle avoidance enabled, obstacle avoidance heights from `5` to `20` cm,
distances from `10` to `20` cm, and all three AI classes enabled.

On 2026-04-21, a supervised no-op A2 `PRE` write validated the live settings
path using map `0`, area `1`, and the already-current
`mowing_height_cm=6.0` value. The mower returned top-level `r: 0`; the client
recorded `executed=true` and `request_verified=true` without changing the
effective mower settings.

## Observed charging and rain-protection settings

The A2 plugin bundle identifies the weather/rain-protection read path as the
general settings app action `CFG`. Relevant fields observed in the bundle:

- `WRF`: boolean weather switch used by the app as `weatherSwitch`.
- `BAT`: six-value battery record. Slots `0-2` are recharge/resume settings;
  slots `3-5` are charging-period enabled, start minutes, and end minutes.
- `WRP`: rain-protection tuple. The app default is `[1, 8, 0]`, and older
  two-value payloads are padded with a third `0` before use.
- `RPET`: read-only app action returning `endTime` while
  `INFO_BAD_WEATHER_PROTECTING` is active.

The Python client exposes `async_get_device_settings()` (with the historical
`async_get_weather_protection()` read alias), confirmed charging/rain setters,
and `examples/weather_probe.py`. Home Assistant exposes a charging-period
switch with start/end time entities plus a rain-protection switch and delay
select. Writes preserve unrelated BAT thresholds and WRP sensitivity, then
require a full `CFG` readback before publishing state.

A live read-only A2 run of `examples/weather_probe.py` on 2026-04-19 while rain
was expected returned `WRP=[1,8,0]`, decoded as rain protection enabled for 8
hours with sensitivity `0`. `CFG` did not include `WRF` on that device, and
`RPET` returned no active `endTime` at capture time.

On 2026-08-10, the live A2 on firmware `4.3.6_0625` reported
`BAT=[15,95,1,0,1080,480]`, `WRF=1`, and `WRP=[1,8,1]` while docked and idle.
Supervised charging-period and rain-protection writes were followed by `CFG`
readback and restoration of the original values. Property `2:51` remains a
generic settings-change announcement: it says that a setting changed without
naming the CFG key, so the integration coalesces it into one settings refresh.

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
  The original client decoded this as vacuum “left wheel speed.” The A2 app
  catalog identifies it as a recoverable return-to-station failure alert, so
  it remains visible without forcing the mower entity into a hard-error state.
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
- `mower_error_label(value, model=...)` for mower-native device-code labels
  seen through `2.2`; the model-aware classifier separates hard faults from
  alerts, attention items, information, and unknown codes

Use `python examples/cloud_probe.py` to query these endpoints directly with the same credentials used by the integration.

Use `python examples/property_probe.py` to scan `siid.piid` key ranges and highlight non-empty property results while keeping `1.1`, `2.1`, and `2.2` readable.
Property scans now also fetch the device `keyDefine` JSON when available, so
published Dreame labels are applied before mower-specific fallback labels.
The scan output includes a `summary` block with non-empty keys, unknown
non-empty keys, value-type counts, blob previews, decoded-label sources, and
map-candidate entries. That summary is the preferred payload to compare between
models or mower states before adding new Home Assistant entities.

An April 18, 2026 follow-up against the live A2 found:

- `device/info` did not expose `keyDefine.url`, but the matching `device/listV2`
  record did. The client now falls back to that record and successfully fetches
  Dreame's public key definition from `device_list_v2`.
- The fetched key definition currently contains only `2.1`, enough to label the
  mower state but not enough to reveal map keys.
- A broad read-only `iotstatus/props` scan across siids `1-12`, piids `1-80`
  returned 16 displayed values and no map candidates. The only blob-like values
  were `1.1` and `1.4`.
- Follow-up scans across siids `13-24` and `25-40`, piids `1-80`, returned no
  non-empty values.
- Direct read-only history probes for legacy map keys `6.1` (`MAP_DATA`), `6.3`
  (`OBJECT_NAME`), and `6.13` (`OLD_MAP_DATA`) returned zero records while the
  mower was docked and charging.
- The APK string scan confirms `/dreame-user-iot/iotstatus/devOTCInfo` and
  `/dreame-user-iot/iotstatus/history`. A live read-only `devOTCInfo` call
  succeeds for the same A2, but returned an empty object in the current docked
  state.

This is useful negative evidence: the current docked live A2 map is not exposed
through the legacy current-map path, the fixed map-property guesses, the broad
`iotstatus/props` ranges tested so far, the legacy map history endpoint, or the
current docked `devOTCInfo` response.

The successful map path is the app action bridge described above. On
2026-04-18, a live A2 returned:

- `MAPL`: two created maps, both with backups, current map index `0`.
- `MAPI idx=0`: size `5679`, hash `8664aa561145354644a40145e705cc7b`.
- `MAPI idx=1`: size `7112`, hash `936e9cdfc3e1ced2c4c2365b0cdb24d5`.
- Chunked `MAPD`: both maps reassembled and parsed as JSON, with hashes
  matching the mower metadata.
- Payload keys: `cut_relation`, `map`, `name`, `point`, `semantic`, `spot`,
  `total_area`, and `trajectory`.
- Current parser summaries keep `semantic` neutral: they report entry count,
  drawable boundary point count, and observed key counts without assuming those
  entries are no-go or restriction zones.
- The Python client downloads every created map returned by `MAPL`. The Home
  Assistant map camera renders the current map image, and its attributes expose
  compact metadata for every app map so secondary maps remain visible without
  switching the rendered camera frame.
- Historical `OBJ type=3dmap` reads returned stale `.bin` metadata whose signed
  URLs failed with `403`/`404`. Those objects are not the generated PCD flow.
- Live A2 runs confirmed that
  `{"m":"a","p":0,"o":10,"d":{"idx":0}}` requests the upload. The reliable
  completion signal is cloud property `99.20`, which carries the short-lived
  LiDAR object name. The client must resolve it immediately through
  `/dreame-user-iot/iotfile/getDownloadUrl`; the returned HTTPS URL needs no
  custom download headers. `OBJ type=3dmap` remains a compatibility fallback
  but can miss the object's short publication window.
- Property `99.20` can keep the last generated object signable after the upload.
  A read-only A2 check on 2026-07-25 resolved that stored object in 0.03 seconds
  and downloaded and validated the 2,437,272-byte PCD in another 0.38 seconds.
  Home Assistant uses this unscoped stored path only when the mower has exactly
  one verified map because the property does not carry a map index. Multi-map
  mowers can still reuse stored objects attributed to the active map by the app
  inventory or mower `OBJ` response; absent, expired, or invalid objects retain
  fresh generation.
- The generated file was a standard PCD 0.7 binary with fields `x y z rgb`,
  152,318 finite points, 991 observed colors, and 2,437,272 total bytes.
  Repeating the flow through the new public client returned the same validated
  format and counts while the mower remained docked at
  `charging_completed`.
- On 2026-07-25, the `OBJ`-only flow timed out at 45 and 120 seconds while the
  A2 was mowing even though property `99.20` and upload progress `2.54` had
  updated. Capturing the fresh announcement directly returned the same
  2,437,272-byte PCD through the public API in 12.5 seconds without changing
  the mower's activity.
- On 2026-08-12, firmware `4.3.6_0625` accepted `o:10` with `r:0` but kept the
  existing `99.20` object name, its `updateDate`, and `2.54=100` unchanged. The
  request instead made that stable object signable within 0.36 seconds. Its
  name matched indexed `OBJ` map `0` and not map `1`. The client therefore
  accepts an unchanged announcement only after bounded indexed-object evidence
  proves that it belongs to the requested map and pre/post object evidence
  proves that the generation refreshed it. With stored fallback disabled, the
  hardened primary path downloaded and validated the 152,318-point PCD in 1.5
  seconds.
- `async_download_app_map_point_cloud()` now owns the generation, transient
  object capture, bounded HTTPS download, and PCD validation. It returns bytes
  plus coordinate-free metadata and deliberately omits the vendor filename and
  signed URL.
- Home Assistant serves those bytes only through an authenticated admin-only
  local endpoint with `private, no-store` caching, a short in-memory TTL, and
  in-flight request deduplication. The map camera publishes only its local
  `point_cloud_api_path`.

Use `python examples/app_map_probe.py --out app-map-current.json` for a focused
read-only probe that omits raw coordinates by default. Add `--include-payload`
only for local parser/rendering work.

Use
`python examples/app_map_probe.py --probe-object-downloads --out app-map-objects.json`
only when comparing the older metadata objects. It records sanitized
HEAD/ranged GET results and redacts signed URLs unless
`--include-object-urls` is explicitly added for local-only debugging.

Use `python examples/point_cloud_probe.py` for the confirmed generation flow.
It prints PCD metadata without coordinates or signed cloud details. Add
`--out garden-map.pcd` only for intentional local geometry analysis.

Use
`python examples/app_map_probe.py --render-dir app-map-renders --out app-map-render.json`
to write one PNG per drawable app map without keeping raw coordinate payloads in
the JSON output. A live A2 check rendered both current map `0` and secondary map
`1` from the read-only `MAPD` payloads.

Home Assistant mirrors this as a disabled-by-default `All Maps` diagnostic
camera that renders the read-only app-map payloads into one contact sheet. This
surfaces secondary maps without changing the mower-selected map.

## Confirmed TX video playback sequence

The local Dreamehome `2.5.8.1` decompile and the downloaded A2 React Native
plugin expose the Tencent XP2P video path in:

- `com.tencent.xnet.XP2P`
- `com.dreame.plugin.video.tx.dreame_flutter_plugin_tx_video.rn.TXAVVideoPlayer`
- `com.dreame.plugin.video.tx.dreame_flutter_plugin_tx_video.rn.video.Command`

For live playback, the plugin supplies the device identity, `productId`,
`deviceName`, and `p2pInfo`. Captures have returned both `channelId` and
`deviceId` for the identity field. The runtime therefore accepts either name
and falls back to `productId/deviceName` when no separate channel identifier is
present. The Java XP2P wrapper uses:

- `productId/deviceName` for native `startService` and `setDeviceXp2pInfo`
- the returned channel/device identity, or the service identifier fallback, for
  FLV delegation and shutdown

The app initializes the XP2P config with `autoConfigFromDevice=false`. It uses
the SDK default STUN/config path during playback rather than making
`AppDescribeConfigureDeviceP2P` a startup dependency. The identity response
also contains the encrypted values used for QCloud and for deriving the XP2P
application credentials, but those application credentials are not a gate for
the app's default playback path.

After the XP2P callback reports event `1004`, the app checks device state with
`action=inner_define&channel={channel}&cmd=get_device_st&type=live&quality=standard`.
Only when that status is `0` does it call `XP2P.delegateHttpFlv(channelId)`,
store the returned URL prefix, and reset the player. The player then starts
`urlPrefix + ipc.flv?action=live&channel={channel}&quality=high&_crypto=on`
for the high-quality live stream.

This matters for probes: the status/readiness command uses `quality=standard`
even when the final FLV URL requests `quality=high`. Treat a returned local URL
as only an intermediate step; the proof point remains readable FLV bytes or
frame data.

A supervised Dreame A2 run on July 10, 2026 first confirmed the host protocol:
XP2P reported ready event `1004`, the device-status and local delegate calls
succeeded, and the first 16 stream bytes began with `46 4c 56` (`FLV`). A second
run copied the integration into a normal Home Assistant `custom_components`
installation on Linux x86_64, loaded the real mower and camera entities, started
the managed Python-owned runtime, and passed the stream through Home Assistant's
HLS pipeline. The HA endpoint returned HTTP 200 with an HLS playlist and
produced a complete fragmented-MP4 segment.

The final retained segment was 507,615 bytes and independently reopened as H.264
MP4 at 640 x 360. PyAV decoded 100 frames whose timestamps span 6.599 seconds.
The real HA camera entity also returned a 29,815-byte JPEG through the
integration-owned PyAV/Pillow still-image path; this worked in an environment
without the optional TurboJPEG system library. Visual inspection showed the
real outdoor mower view rather than a blank or synthetic image. Its SHA-256 was
`ed54c246ae87183c9a0f549783dbc8adddf68ce129e6f5875b36817e891ef02f`; the MP4
SHA-256 was
`c38aeae1e44efc79cf5588f661d2c7cb0eb6e3ab722452729d297f790a8dc934`.
Home Assistant turned the camera off, no worker remained, and the mower reported
both `docked=true` and `raw_docked=true` during cleanup. A later read-only HA
snapshot reported `charging_completed`, `docked=true`, and battery 100%.

This host path does not use an Android phone, emulator, or framework. Python
owns runtime installation, credential delivery over stdin, process lifetime,
FLV health checks, and Home Assistant cleanup. A small AArch64/Bionic worker is
still required to call Tencent's proprietary native XP2P ABI; on x86_64 Linux
it runs through a pinned qemu-user-static binary. All downloaded runtime files
are SHA-256 verified before use.

### Separate Tencent LAN, talk, and movement paths

Tencent's public Android SDK
[WLAN preview demo](https://github.com/tencentyun/iot-link-android/blob/73252a0c23572143818de635867a7874000eea47/sdkdemo/src/main/java/com/tencent/iot/explorer/link/demo/video/preview/WlanVideoPreviewActivity.kt)
confirms that explicit LAN playback is a different path from the XP2P
`startService` flow used above. Its WLAN preview:

- discovers a device address and port, then calls
  `startLanService(id, productId, deviceName, address, port)`
- builds the FLV endpoint with `getLanUrl(id)` and disables stream encryption
  for that local proxy request
- opens the microphone send path with `runSendService` and a `voice` URL
- sends directional PTZ commands over a separate `command` URL

The managed Home Assistant runtime currently implements downstream video over
`startService`; it does not perform WLAN discovery, call `startLanService`, send
microphone audio, or expose movement/patrol controls. The native SDK also
contains direct and TURN/relay transports, but the retained A2 proof did not
capture enough route telemetry to say which transport normal XP2P selected.

Consequently, explicit same-LAN playback, A3/MOVA compatibility, two-way talk,
and patrol are separate follow-up capabilities. Each needs its own device-safe
proof rather than being inferred from the working A2 camera stream.

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

For the next offline pass, decompile the APK with `jadx` and scan the output:

```bash
jadx -d C:\path\to\dreamehome-jadx C:\path\to\dreamehome.apk
python examples/source_research.py "C:\path\to\dreamehome-jadx" --term STREAM_VIDEO --term operType --term sendAction
```

The source scanner reports candidate files plus compact file/line snippets, which
should make it easier to identify the exact app class or bridge method before we
try another live camera/map action.

`python examples/decompile_research.py <apk> --output-dir <jadx-output>` wraps
both steps once Java and `jadx` are available locally. It does not install tools
or overwrite an existing output directory unless `--overwrite` is passed.

Use `python examples/asset_research.py <extracted-assets-dir>` for a tighter
scan of Flutter/plugin assets. It is useful when users provide an extracted APK
or XAPK and we want compact evidence about whether map protocol strings are
bundled in assets before asking them for runtime captures.

Use `python examples/key_definition_probe.py` to fetch the public
`keyDefine.url` advertised by `device/info`. This pulls Dreame's own
device-status translation JSON for the mower model, which can help decode
`iotstatus/props` values without guessing.
