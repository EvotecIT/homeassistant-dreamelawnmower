# Task history and link diagnostics

The integration exposes the 30 most recent task sessions reported by the mower.
The Lawn Mower Card's Hero and Dashboard layouts display them in **History** when
the mower entity advertises `task_history_api_path`.

Each task can include its reported start, duration, area and outcome, its own
historical map and routes, zone outcomes, fault codes and task photos. A missing
or deleted cloud summary remains unavailable; today's map never fills that gap.
Routes show recorded movement, rather than proof of cut-area coverage.

## Battery and settings

Battery curves use observations collected while Home Assistant is running.
The integration saves changed readings and occasional coverage anchors, retaining
at most 30 runs, 500 samples per run and 90 days of observations. Restart recovery
keeps those readings; it does not fill the offline interval.

Usage is measured in **percentage points**, also shown per square metre. A drop
from 80% to 70% is 10 percentage points. These values are not energy in Wh or a
measurement of battery capacity. Charging rises, unknown states and intervals
longer than five minutes do not count toward observed mowing consumption.
The card labels partial coverage rather than presenting a partial curve as a
complete task measurement.

When available, Home Assistant's recorder can supply an older battery curve for
the same mower and task interval. This uses Home Assistant's history API and is
labelled **Home Assistant recorder · Partial coverage**. Recorder data cannot
reconstruct settings that were never captured. Excluded or purged entities have
no recorder backfill.

Settings come from the task summary or an immutable snapshot of the first
available decoded preferences during the observed mission. The latter records
its observation time and map revision. **First observed after the task started**
means the settings may have changed before Home Assistant saw them. Summary
defaults do not establish every zone's overrides. The current settings screen
does not substitute for missing historical settings.

Observations attach to a cloud task only when their intervals fit its times and
verified map revision. Disjoint segments from a restart can belong to the same
task while preserving the break between them. Competing, overlapping intervals
remain ambiguous. A later map revision never labels earlier unverified evidence.

## Storage and privacy

The integration keeps the private task index and downloaded, normalized summaries
in a separate atomic HA storage file from the frequently updated observations.
Summary drawings have a 4 MiB cache budget; older drawings can be downloaded again
when their cloud objects remain available. The index retains at most 30 tasks.
Cached history remains readable during a cloud refresh failure and is explicitly
labelled as cached.

The history API requires authentication and read access to the mower entity.
Task IDs are opaque and scoped to the device. Vendor object names, signed vendor
URLs, credentials and raw packets never enter the dashboard payload. Photos use
separately authorized HA paths and are streamed on demand, with an 8 MiB download
limit per image. Images are not copied into the history storage files. The card
releases its private image URLs when leaving the view.

Deleting the integration entry removes both private history stores. Unloading an
entry withdraws API access, drains owned reads and flushes pending observations.

## Link diagnostics

**Wi-Fi Signal** and **Cellular Signal** are diagnostic measurements in dBm. They
appear as available only after a validated heartbeat reports a real reading.
Absent hardware and protocol placeholders remain unknown, rather than becoming
0 dBm. The cellular entity also exposes the reported `module_installed` and
`plan_valid` flags; these do not establish network connectivity or subscription
expiry dates. Offline entities remain unavailable.

## Read-only API, schema version 1

The mower entity's `task_history_api_path` identifies the collection endpoint:

```text
GET /api/dreame_lawn_mower/task-history/{entry_id}
GET /api/dreame_lawn_mower/task-history/{entry_id}/{task_id}
GET /api/dreame_lawn_mower/task-history/{entry_id}/{task_id}/photo/{index}
```

Collection and detail responses include `schema_version: 1` and `stale`.
Collection entries contain an opaque `id`, normalized `mode`, timestamps,
`duration_minutes`, `area_sqm`, `result` and the exact local `detail_path`.
Mode values 0–3 represent all-area, edge, zone and spot tasks; unsupported values
remain unknown. Result 1 means complete, 3 stopped, and 0 or 2 incomplete.

Detail responses include `features` with native map-unit `points` and separate
`tracks`, `zone_results`, timestamped `faults`, `recorded_settings`,
`per_zone_settings`, optional `battery` observations and exact local
`photo_paths`. Coordinates come from the task summary, scaled to the native
map units used by the integration. Native X is mirrored for display. Track
breaks remain separate arrays.

Battery data identifies its `source`, `coverage`, `covered_seconds`,
`percentage_points`, `percentage_points_per_sqm`, `charging_rises`, timestamped
`samples`, and captured `settings` with `settings_observed_at` and
`settings_timing`. Missing measurements use JSON `null`, never an invented zero.
Each sample's `continuous` flag states whether its preceding interval can be
connected; unknown/offline periods and restarts break continuity.
All responses use private, non-cacheable HTTP headers. A signature for one route
cannot authorize a different task or photo route.
