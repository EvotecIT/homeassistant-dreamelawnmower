# Mowing controls, schedules, and settings

[Back to the README](../README.md) · [Entities](entities.md)

Use the mower's normal Home Assistant entities for everyday control. Confirm the
selected map and target before starting a job, and supervise anything that moves
the mower. The examples below are actions you can run deliberately; they do not
install an automatic mowing routine.

## Schedules And Multiple Maps

Native schedules use either versioned documents or per-map tables. The client
discovers the protocol from validated replies and remembers it per map. The
account brand determines probe order; it does not establish protocol support.
Failed or unrecognized reads remain unknown, while a valid empty inventory
means no plans were reported.

Document reads support V2 and V3 firmware. The client remembers the confirmed
generation for subsequent reads and enable/disable requests: V2 documents use
`SCHDSV2`, and V3 documents use the live-qualified `SCHDSV3` status command.
Older firmware and table schedules retain their own command paths. Status writes
require an explicit mower acknowledgement before the integration updates its
schedule state.

Document status writes can change the checksum. Switches use the acknowledged
checksum and complete plan-status list. Validated native documents remain
authoritative when batch data reports a different checksum; the normal calendar
waits for matching active-version evidence instead of showing an older plan.

Dreame A2 captures show a default document plus per-map documents. The normal
`Schedule` calendar follows the active version from the mower's current-task or
batch evidence. Table schedules follow the known current map. When active
selection is unknown, the normal calendar stays empty and the decoded plans
remain available in the diagnostic calendar.

Each map with a complete native schedule read also has a `Map N Schedule`
calendar. Select the calendar for the map whose saved plans you want to view.
It uses that map's native document or tables, including changes made in the
Dreame app, and does not depend on the cloud's active-version reference. For
example, `Map 0 Schedule` shows map 0's enabled weekly starts even when the
automatic `Schedule` calendar cannot establish active selection.

Map calendars describe saved plans; they do not assert that the mower currently
uses that map. Other maps and the default template are excluded. A valid empty
schedule has no events. A map calendar is unavailable when no complete native
schedule is cached. Failed refreshes can retain the last validated saved plans
until a successful read or cache invalidation. These calendars are read-only
and do not start mowing or select a map.

Tables and framed document tasks report start times without scheduled end times. Their calendar
events are one-minute markers labelled **start**; that minute is a display
marker, not a mowing duration. Cyclic tasks, weekday assignments, saved zones,
and edge contour pairs are retained in event details.

Enable the disabled `All Schedules` calendar only when you intentionally want to
inspect every decoded schedule slot.

Each decoded plan is also exposed as a normal Home Assistant switch. Turning a
plan on or off uses the mower-native schedule write and refreshes the shared
schedule cache. Table writes require fresh task data before enabling a plan,
then confirm both table flags. If enabling one disables its sibling, both
switches reflect the readback. Missing tasks prevent enabling; a known plan can
still be disabled. An ignored or unconfirmed write raises an error.
These switches are suitable for dashboards,
automations, and voice assistants; no service flags are needed for an ordinary
switch action.

Schedule edits require fresh mower state confirming there is no unfinished
task. Pausing or docking a partially completed job does not finish it. While a
task remains active or resumable, the integration rejects edits with a clear
message before sending a schedule write. Plan reading and dry-runs remain
available. Unknown task state also prevents an executed edit.

The guarded `dreame_lawn_mower.set_schedule_plan_enabled` service is dry-run
first. It sends a write only when both `execute: true` and
`confirm_schedule_write: true` are set.

The `dreame_lawn_mower.set_schedule_task_start_time` action edits an existing
all-area start on the A2's V3 map 0, plan 0 schedule. Choose the weekday
(Sunday 0 through Saturday 6), the zero-based task position within that day,
and a whole-minute time in the mower's local timezone. It preserves other
tasks, names, enable flags and native fields, and reports success only after
the mower acknowledges the upload and its native schedule matches the edit.
The mower must have finished its current task, including any paused remainder.

```yaml
action: dreame_lawn_mower.set_schedule_task_start_time
data:
  map_index: 0
  plan_id: 0
  week_day: 1
  task_index: 0
  start_time: "10:58:00"
  execute: true
  confirm_schedule_write: true
```

Omit `execute` and `confirm_schedule_write` to preview without a physical write.
Creating tasks, changing weekdays or editing zone/edge targets requires the
vendor app. Other models, maps and seasonal slots are not qualified for this
start-time action. Full plan uploads are available for legacy V2 documents;
the integration rejects full uploads for V3/framed and table schedules. For an HA-owned weekly
routine, use the [guarded Schedule blueprint](notifications.md#guarded-weekly-mowing).

`dreame_lawn_mower.plan_mowing_preference_update` is dry-run first. It reads
the current app preference payload, applies the requested field changes
locally, and exposes the candidate `PRE` request in a notification plus the
disabled-by-default `Last Preference Write` diagnostic sensor. It sends a live
preference write only when both `execute: true` and
`confirm_preference_write: true` are provided. After every live `PRE` or `PREP`
acknowledgement, the integration reads the selected map preferences again and
requires the requested mode and field values to match. A generic success reply
does not count as confirmation when the mower keeps its previous settings.

The guarded preference fields include per-zone safe edge mowing through
`edge_mowing_safe`, EdgeMaster through `edge_cutting_attachment`, and mowing
direction through `mowing_direction_mode` plus `mowing_direction_degrees`. Use
the dry-run result to inspect the candidate payload before confirming a live
write.

For normal dashboard and automation use, the integration also exposes:

- **Selected Map Preference Mode**, a `select` entity with `Global` and
  `Custom` options
- **Selected Map Mowing Height**, available while the selected map uses
  `Global` preferences
- **Selected Zone Mowing Height**, available while the selected map uses
  `Custom` preferences
- selects for mowing efficiency, mowing direction mode, obstacle height and
  distance, and turning method
- a 0-180 degree mowing-direction slider
- switches for automatic and safe edge cutting, EdgeMaster, edge obstacle
  avoidance, lidar obstacle recognition, and the people, animal, and object
  recognition classes

Electronic cutting-height controls use 0.5 cm steps. A2 and other standard
families expose 3-7 cm; LiDAX Ultra and supported AWD families expose 3-10 cm.
MOVA 600/1000 and VIAX 250/300/500 use a physical 2-6 cm knob, so the
integration does not create electronic height number entities for them. A
reported height of 0 on these mowers is a vendor placeholder, not a cutting
height setting. Every other preference control
follows the current `Global` record or the zone chosen by
the normal map and zone selectors. `Global` means area `0` on the selected map,
not one device-wide setting shared by every map. `Custom` edits the selected
zone on that map. This prevents a dashboard from presenting a zone value as if
it were a whole-lawn setting. These standard entities work with Home Assistant
dashboards, automations, and voice assistants. The companion Lawn Mower Card
discovers supported preference controls automatically, and also accepts explicit
entity configuration. The guarded service remains available when you need to
inspect the complete candidate preference payload before sending it.

## Charging, Rain, And Anti-Theft Settings

Models that report the mower-native `BAT`, `WRP`, and `ATA` records expose only
the matching Home Assistant configuration entities. The charging-period switch
keeps the configured start and end times; its two time entities can define a
window that crosses midnight. Rain protection has a switch and a whole-hour
delay select. The zero-hour delay means the mower stays docked until it is
manually started.

Anti-theft settings use ordinary switches for Lift Alarm, Off-Map Alarm, and
Real-Time Location. PIN Check Before Power-Off is optional: Home Assistant adds
it only when the mower reports that fourth field. Unknown future fields in the
same record are preserved during updates.

Every setting change first reads the current `CFG` record, writes only the
setting-specific payload, and reads `CFG` again. Home Assistant updates only
after the mower reports the requested value. Battery thresholds and rain-sensor
sensitivity and untouched anti-theft flags are preserved. The mower's `2:51`
settings-change announcement causes one coalesced `CFG` refresh. Its separate
`2:52` preference announcement refreshes only mowing preferences. Changes made
in the Dreamehome or MOVAhome app therefore appear without waiting for the
normal metadata interval.

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

For example, this starts the saved zone with ID `1` on the mower's active map:

```yaml
action: dreame_lawn_mower.start_zone_mowing
target:
  entity_id: lawn_mower.my_mower
data:
  zone_ids: [1]
```

Use `entity_id` when the value starts with `lawn_mower.`. A Home Assistant
`device_id` is a separate device-registry identifier and must not contain an
entity ID. The lawn mower entity exposes `available_zone_ids`, and zone selects
use names from the matching, hash-verified native map when available. A vendor-app
rename appears after the next map refresh even if cloud vector metadata still
has the old name. Vector metadata supplies the fallback when native names are
unavailable. Clearing a native name restores the stable `Zone #<id>` label.
Name refreshes retain the existing zone IDs and map scope.

Zone, spot, and edge actions check the mower acknowledgement and fresh task
state. When native task metadata is available, the mode and zone or spot IDs
must match the request. A reported whole-map task cannot confirm a zone request.

Some firmware reports a new mowing session before publishing its native task
metadata. If the request was acknowledged, the mower was inactive beforehand,
and fresh heartbeat reads confirm that mowing started, the action succeeds
without waiting for the delayed metadata. The integration logs a warning that
the exact targets remain unverified. A lost acknowledgement or failed readback
cannot use this fallback, and any observed mode or target mismatch blocks it.
Explicit targeted services also update the local `Mowing Action` selection
after the start is accepted.
Unknown current-map IDs, map-scope mismatches, device rejection responses, and
missing start evidence are surfaced as failed actions. A repeated targeted call
is rejected before dispatch while an active task's mode is unknown, or when the
mower is running the same task type and the integration cannot prove that a
different target was requested.
