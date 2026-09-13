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

For a 3D map report, first check whether the official app offers an actual 3D
view for the same mower. A 2D map or LiDAR does not confirm point-cloud export.
After a failed attempt, download diagnostics before restarting Home Assistant;
do not repeatedly request generation when no export has been demonstrated.
The mower and map camera expose `feature_capabilities.point_cloud`, including
whether support comes from model facts, explicit metadata, or a validated
download. Missing evidence remains `unknown`; a failed request is not proof of
`unsupported`. Include the visible `point_cloud_*` reference from the card. The
matching recent event shows whether a later request completed or the mower
failed to publish a fresh object, the object could not be downloaded and
validated, another generation was already running, or the integration reloaded
during the request.

The failed `point-cloud` request in browser developer tools also contains a
sanitized `diagnostics` section. Copy its **Response** JSON, not request headers
or cookies. This uses the same allowlisted attempt evidence as the mower's
`recent_events[].context.attempt`:

- `initial_announcement` and `latest_announcement` distinguish a missing `99.20`
  property, empty or structured values, invalid timestamps, unrecognized file
  extensions, and fresh or stale announcements.
- `latest_indexed` distinguishes an absent map index, an empty string, `null`,
  or a nonempty object name. It reports shapes and counts, never object names.
- `generation_result` records whether the generation command was acknowledged;
  acknowledgement alone does not prove that an upload completed.
- `last_download_step` separates URL signing, HTTP download, and PCD validation.
  When observed, `download_http_status` and `download_bytes` identify an HTTP
  failure or the size of a downloaded payload without exposing its contents.
- `stored_attempt` records pre-generation stored-download attempts separately
  from the generated-object download counters.

These observations reuse the request's existing reads. They do not add polling
or change download acceptance. A failure before discovery starts may have an
empty `attempt`; that is missing evidence, not proof of unsupported hardware.

In cached app-map object metadata, `object_count` is the vendor's slot count.
Use `named_object_count` and per-slot `name_present` to identify nonempty names.
`url_present: false` with `url_checked: false` means signing was **not checked**,
not that the cloud refused a URL. A missing extension does not distinguish an
empty name from an extensionless name in older diagnostics.

For local client debugging, `python examples/point_cloud_probe.py` performs one
generation request and prints the same safe failure diagnostics with a nonzero
exit status if it fails. It does not print the private exception text. Download
Home Assistant diagnostics from **Dreame Lawn Mower**, not HACS: the HACS report
helps establish installed versions but does not contain the mower attempt.

See the [VIAX 500 investigation](viax-point-cloud.md) for the evidence and
remaining hardware-validation boundary in card issue #52.

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
