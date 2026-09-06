# Troubleshooting

[Back to the README](../README.md) · [Live video](live-video.md)

Start with a fresh Home Assistant diagnostics capture:

1. Reproduce the problem once.
2. Before reloading or restarting Home Assistant, open the integration or device
   page and download diagnostics.
3. Attach the downloaded JSON to the issue. Add screenshots or short log excerpts
   only when they show something that is not already in the diagnostics.

The report is sanitized by the integration and includes:

- the installed integration, Home Assistant, Python, operating-system, and CPU
  architecture versions
- config-entry and coordinator health
- privacy-safe setup, foreground-refresh, and background-metadata timings,
  including bounded recent samples plus the latest and aggregate duration for
  each operation
- on-demand `point_cloud_generation` timings plus coordinate-free completion or
  failure events, including the source, point count, payload size, selected map,
  stored-object eligibility, stable error code, safe numeric Dreame cloud error,
  stage, retryability flag, and timeout when relevant
- current state and diagnostic attributes for every entity belonging to the
  config entry, including the Live Video camera's last failure stage and a
  bounded, privacy-safe summary of each TX video cloud stage
- a bounded list of recent coordinator, map, schedule, and video failures with
  repeated failures coalesced
- the existing `triage`, `state_reconciliation`, schedule, map, firmware, and raw
  property summaries

Do not enable broad debug logging unless a maintainer asks for a specific logger.
Cloud protocol debug output can contain data that needs additional review before
it is posted publicly.

Startup, refresh, and on-demand 3D map measurements are also written as log
lines beginning with `Dreame mower performance`. The first setup, metadata
hydration, and successful point-cloud generation are logged at info level.
Unusually slow refreshes and failed point-cloud generations are logged as
warnings. Each line reports only operation names, outcome codes, stages, and
elapsed time; it does not contain credentials, mower identifiers, map data,
object names, URLs, or coordinates.

For startup reports, include both the `setup` and `metadata_refresh` entries
from downloaded diagnostics. `setup` is the blocking Home Assistant load path.
`metadata_refresh` covers optional maps, schedules, firmware, weather,
maintenance, and preference metadata that continues in the background after
the mower entity can load. The per-phase timings show which vendor endpoint is
slow without requiring broad protocol debug logging.

For a 3D map report, retry once and download diagnostics before restarting Home
Assistant. Include the visible `point_cloud_*` reference from the card. The
matching recent event shows whether a later request completed or the mower
failed to publish a fresh object, the object could not be downloaded and
validated, another generation was already running, or the integration reloaded
during the request.

`App Map Count` reports maps the mower has actually created; reserved cloud
slots with `created: false` are excluded. Raw slot count remains available as
the mower entity's `app_map_slot_count` diagnostic attribute.

The staged cloud summaries retain field names, value types, safe status codes,
required-field presence, and sanitized error messages. They do not retain raw
response values, account or device identifiers, credentials, stream URLs, or
unbounded payloads. This lets maintainers distinguish unsupported models,
malformed vendor responses, and missing provisioning without asking users to
share an account as the first debugging step.

### Live video identity is not provisioned

The integration reports `device_triple_missing` when both Dreame video identity
endpoints return vendor code `10000`, `设备三元组不存在` ("device triple does not
exist"), and the required `product_id`, `device_name`, and `p2p_info` fields are
absent. This means Dreame has not provisioned the mower's XP2P video identity
for the current account or region; it is not a video-runtime or model-support
failure.

Check live video in Dreamehome or MOVAhome first. If it is missing there too,
contact Dreame support and include the diagnostics capture. A field report for
an A3 AWD 1000 found this condition on an RU account and confirmed working video
after the same mower was rebound to an EU account. That is one account/device
result, not evidence that every device in a region behaves the same way.

Changing account region requires pairing the mower to another account and can
discard cloud-stored maps and settings. Treat that as a last resort, not the
normal fix for `device_triple_missing`.

For issue reports, include:

- the downloaded diagnostics captured immediately after the failure
- what you expected and the exact steps that failed
- whether the same operation worked in Dreamehome or MOVAhome at that time
- screenshots only when the visible result matters

Home Assistant log lines that start with `Captured Dreame lawn mower ...` can be
converted to JSON with:

```bash
python examples/extract_ha_payload.py home-assistant.log --summary
```
