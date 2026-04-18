# Agent Handoff

This file is for future coding sessions after context is cleared. It captures
the current project state, live-device findings, safe commands, and known gaps.

Last updated: 2026-04-18

## Current State

- The repo contains a reusable Python client facade in `dreame_lawn_mower_client`
  and a Home Assistant custom integration in `custom_components/dreame_lawn_mower`.
- The Python client is the source of truth for protocol behavior. Home Assistant
  should call the client rather than duplicate protocol logic.
- The current validated real device is a Dreame A2 reported as
  `dreame.mower.g2408`.
- Do not commit credentials or live JSON captures. Credentials are expected via
  environment variables named `DREAME_USERNAME`, `DREAME_PASSWORD`,
  `DREAME_COUNTRY`, and `DREAME_ACCOUNT_TYPE`.

## Validated Python Client Features

- Account discovery and snapshot refresh work against the live A2.
- Normalized mower state works for docked, returning, charging, charging
  completed, error, paused, and mowing-safe distinctions.
- Remote control support is detected from protocol mapping `4.15`.
- Safe remote-control movement works through
  `async_remote_control_move_step(...)` and `async_remote_control_stop()`.
- Remote-control support payloads distinguish protocol availability from current
  state safety: `supported` means the mapping exists, `state_safe` tells whether
  nonzero movement is currently allowed.
- Home Assistant exposes guarded manual-drive services plus diagnostic
  `Manual Drive Safe` and `Manual Drive Block Reason` entities that share the
  same state guard.
- A supervised no-mow test drive was run successfully:
  stop, forward, turn right, turn left, backward, stop after each step, then
  dock.
- The mower returned to `charging_completed` / `docked` after the test drive.
- Operation snapshots work and group normalized state, realtime summaries,
  decoded status blob, map diagnostics, firmware evidence, and remote-control
  support. They also include the current manual-drive safety decision from the
  same reusable guard used by Home Assistant.
- A read-only operation snapshot on 2026-04-18 collected `before` and `final`
  captures without movement. The mower stayed docked at `charging_completed`
  with battery 100%, manual-drive state safety was true, remote-control support
  was present, and the legacy map path still returned no map data.
- Read-only remote-control support probes on 2026-04-18 reported
  `supported=true`, `state_safe=true`, no block reason, `active=false`, and
  docked states including `charging_completed` and `charging`.
- A read-only remote-control smoke probe on 2026-04-18 reported before/after
  `manual_drive_safe=true`, support `state_safe=true`, no block reason,
  `active=false`, and no movement steps.
- Firmware/update diagnostics work as evidence, but no verified mower OTA
  availability signal has been found.
- Mower-native app map retrieval now works through read-only app action
  commands recovered from the downloaded Dreamehome plugin:
  `MAPL`, `MAPI`, and chunked `MAPD`.
- A live A2 probe on 2026-04-18 returned two created maps. Both maps were
  downloaded in chunks, parsed as JSON, and matched the mower-provided MD5
  hashes. Current map index was `0`; summaries included boundary point counts,
  map area totals, spots, and trajectories.
- `examples/app_map_probe.py` provides the focused read-only app-map probe.
  By default it omits raw coordinates; `--include-payload` is opt-in for local
  parser or renderer work.
- The existing map-view path now tries the app-map payload first and renders a
  simple PNG. The older legacy current-map path remains a fallback. A live run
  of `examples/map_client.py` on 2026-04-18 produced an `app_action_map` image
  for current map `0` with 2 map areas, 2 no-go/spot areas, and 63 trajectory
  points.
- `OBJ type=3dmap` is also wired as read-only object metadata through
  `async_get_app_map_objects()`. A live A2 probe returned two `.bin` object
  names. Expiring object URLs are intentionally opt-in and omitted from default
  probe output. Direct HTTP GETs against the tested generated URLs returned
  404 XML, so treat 3D objects as metadata-only until a downloadable path is
  confirmed.
- Config-flow auth failures are classified into non-secret Home Assistant
  errors for account type, region, connectivity, generic auth, 2FA, and no
  matching mower devices.
- Downloaded diagnostics use schema version 5 and include manual-drive safety
  in `state_reconciliation` and `triage`.
- The live docked `charging_completed` shape has a sanitized fixture regression
  that preserves effective docked, raw dock false, not actively charging, raw
  started true, effective started false, and manual-drive-safe semantics.
- The live docked `charging` shape also has a sanitized fixture regression that
  preserves raw/effective docked true, raw/effective charging true, raw started
  true, effective started false, and manual-drive-safe semantics.

## Known Gaps

- Real mower map retrieval is solved at the Python-client level through the
  app action path, but Home Assistant does not yet render or expose the vector
  map as an entity/camera.
- The legacy vacuum-style current-map path still returns no data for the live
  A2. Keep it as negative diagnostics rather than the primary mower map source.
- App-style cloud property probing currently returns useful status/realtime
  fields, but no non-empty map-like fields. The confirmed map payload comes
  from app actions, not `iotstatus/props`.
- The app-side `devOTCInfo` endpoint is reachable, but the 2026-04-18 live A2
  docked response was an empty object, so it is not a solved map source yet.
- `pluginForceUpdate` is not treated as firmware update availability. It is
  conflicting across cloud metadata sources and appears to be app/plugin
  metadata rather than a verified mower OTA signal.
- Realtime key meanings are still being learned. Known useful keys include
  `1.1` as a raw status blob, `2.1` as mower state, and `2.2` as mower error.
  Movement/docking also surfaced `2.50`, currently unknown.

## Local Live Evidence

The following generated files are intentionally ignored by git and may exist in
the workspace after live tests:

- `field-trip-current-readonly.json`
- `field-trip-current-live.json`
- `field-trip-live.json`
- `field-trip-postdock.json`
- `field-trip-readonly.json`
- `firmware-update-live.json`
- `app-map-current.json`
- `dreame-map-current.png`
- `map-sources-current.json`
- `source-scan-map.json`
- `property-scan-*.json`
- `property-scan-*.txt`
- `remote-control-current.json`
- `apk-scan*.json`
- `asset-scan*.json`

These files can help a future agent understand recent live behavior, but they
must not be committed.

## Safe Live Commands

Read-only operation snapshot:

```powershell
$env:DREAME_USERNAME = [Environment]::GetEnvironmentVariable('DREAME_USERNAME','User')
$env:DREAME_PASSWORD = [Environment]::GetEnvironmentVariable('DREAME_PASSWORD','User')
$env:DREAME_COUNTRY = [Environment]::GetEnvironmentVariable('DREAME_COUNTRY','User')
$env:DREAME_ACCOUNT_TYPE = [Environment]::GetEnvironmentVariable('DREAME_ACCOUNT_TYPE','User')
python examples\field_trip_probe.py --include-map --include-firmware --map-timeout 8 --out field-trip-current-readonly.json
```

Supervised no-mow drive and dock smoke test:

```powershell
$env:DREAME_USERNAME = [Environment]::GetEnvironmentVariable('DREAME_USERNAME','User')
$env:DREAME_PASSWORD = [Environment]::GetEnvironmentVariable('DREAME_PASSWORD','User')
$env:DREAME_COUNTRY = [Environment]::GetEnvironmentVariable('DREAME_COUNTRY','User')
$env:DREAME_ACCOUNT_TYPE = [Environment]::GetEnvironmentVariable('DREAME_ACCOUNT_TYPE','User')
python examples\field_trip_probe.py --execute --confirm-supervised --velocity 30 --rotation 25 --duration 0.35 --settle 1.5 --dock --include-map --include-firmware --map-timeout 8 --out field-trip-current-live.json
```

Observed live A2 values so far are intentionally conservative: velocity `30`,
rotation `25`, duration `0.35`, settle `1.5`, stop before the first pulse, stop
after every pulse, then dock. Do not treat this as a full range calibration.

Focused map-source probe:

```powershell
$env:DREAME_USERNAME = [Environment]::GetEnvironmentVariable('DREAME_USERNAME','User')
$env:DREAME_PASSWORD = [Environment]::GetEnvironmentVariable('DREAME_PASSWORD','User')
$env:DREAME_COUNTRY = [Environment]::GetEnvironmentVariable('DREAME_COUNTRY','User')
$env:DREAME_ACCOUNT_TYPE = [Environment]::GetEnvironmentVariable('DREAME_ACCOUNT_TYPE','User')
python examples\map_sources_probe.py --out map-sources-current.json
```

Focused app-map probe without raw coordinates:

```powershell
$env:DREAME_USERNAME = [Environment]::GetEnvironmentVariable('DREAME_USERNAME','User')
$env:DREAME_PASSWORD = [Environment]::GetEnvironmentVariable('DREAME_PASSWORD','User')
$env:DREAME_COUNTRY = [Environment]::GetEnvironmentVariable('DREAME_COUNTRY','User')
$env:DREAME_ACCOUNT_TYPE = [Environment]::GetEnvironmentVariable('DREAME_ACCOUNT_TYPE','User')
python examples\app_map_probe.py --out app-map-current.json
```

Only use `--include-payload` for local parser/renderer work. It includes raw
map coordinates and should stay in ignored local files. Only use
`--include-object-urls` when debugging 3D map object downloads; it writes
expiring signed URLs to the ignored output file. Use `--skip-objects` when you
only want the 2D map payload.

Render the current app-map fallback PNG:

```powershell
$env:DREAME_USERNAME = [Environment]::GetEnvironmentVariable('DREAME_USERNAME','User')
$env:DREAME_PASSWORD = [Environment]::GetEnvironmentVariable('DREAME_PASSWORD','User')
$env:DREAME_COUNTRY = [Environment]::GetEnvironmentVariable('DREAME_COUNTRY','User')
$env:DREAME_ACCOUNT_TYPE = [Environment]::GetEnvironmentVariable('DREAME_ACCOUNT_TYPE','User')
$env:DREAME_MAP_OUTPUT = 'dreame-map-current.png'
python examples\map_client.py
```

Broad read-only property scan:

```powershell
python examples\property_probe.py --siids 1,2,3,4,5,6,7,8,9,10,11,12 --piid-start 1 --piid-end 80 --out property-scan-1-12-current.json
```

Read-only remote-control support probe:

```powershell
python examples\remote_control_probe.py --out remote-control-current.json
```

Firmware evidence probe:

```powershell
$env:DREAME_USERNAME = [Environment]::GetEnvironmentVariable('DREAME_USERNAME','User')
$env:DREAME_PASSWORD = [Environment]::GetEnvironmentVariable('DREAME_PASSWORD','User')
$env:DREAME_COUNTRY = [Environment]::GetEnvironmentVariable('DREAME_COUNTRY','User')
$env:DREAME_ACCOUNT_TYPE = [Environment]::GetEnvironmentVariable('DREAME_ACCOUNT_TYPE','User')
python examples\firmware_update_probe.py | Out-File -FilePath firmware-update-live.json -Encoding utf8
```

## Safety Notes

- Do not start mowing from automated probes unless the user explicitly asks.
- Prefer read-only probes first. Only use `--execute --confirm-supervised` after
  checking the mower is safe to move.
- Keep movement pulses short and low speed unless the user asks for more.
- Always stop after each movement pulse and request dock at the end of a live
  drive test.
- If the mower reports mapping, mowing, active error, or low battery, do not
  drive it.

## Validation Commands

Use these before committing:

```powershell
python -m ruff check .
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest tests --ignore=tests\components
python -m compileall dreame_lawn_mower_client custom_components tests examples
git diff --check
```

Also run a secret sweep for the known local username/password values before
committing. The sweep should return no matches. Do not write the actual
credentials into repo files.

## Recommended Next Work

- Keep adding fixtures for real mower transitions such as idle, returning,
  docked-charging, charging, and fault recovery.
- Do not expose a Home Assistant firmware update entity yet. Keep firmware as
  diagnostics until a verified latest-version or OTA-available field is found.
- Improve the simple app-map renderer into a polished mower-specific renderer
  once more fixtures are available. The confirmed payload keys are `map`,
  `spot`, `point`, `semantic`, `trajectory`, `total_area`, `name`, and
  `cut_relation`.
- `OBJ type=3dmap` object names and signed-looking download URLs are
  discoverable, but the tested URLs returned 404 XML. The `.bin` format is not
  downloaded or decoded yet.
- Keep the map camera/entity disabled by default until the renderer has stable
  fixtures from more mower states and models.
