# Mowing controls, schedules, and settings

[Back to the README](../README.md) · [Entities](entities.md)

Use the mower's normal Home Assistant entities for everyday control. Confirm the
selected map and target before starting a job, and supervise anything that moves
the mower. The examples below are actions you can run deliberately; they do not
install an automatic mowing routine.

## Schedules And Multiple Maps

Dreame A2 schedules can exist in more than one slot. Live captures have shown a
default schedule plus per-map schedules. The normal Home Assistant `Schedule`
calendar follows the active schedule version reported by the mower's `SCHDT`
response, so hidden/default/other-map schedules do not appear as normal mowing
events.

Enable the disabled `All Schedules` calendar only when you intentionally want to
inspect every decoded schedule slot.

Each decoded plan is also exposed as a normal Home Assistant switch. Turning a
plan on or off uses the mower-native schedule write, then reads the schedules
again before updating the entity. These switches are suitable for dashboards,
automations, and voice assistants; no service flags are needed for an ordinary
switch action.

The guarded `dreame_lawn_mower.set_schedule_plan_enabled` service is dry-run
first. It sends a write only when both `execute: true` and
`confirm_schedule_write: true` are set.

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

The cutting-height controls use 0.5 cm steps. A2 and other standard mower
families expose 3-7 cm; verified AWD families expose 3-10 cm. Every other
preference control follows the current `Global` record or the zone chosen by
the normal map and zone selectors. `Global` means area `0` on the selected map,
not one device-wide setting shared by every map. `Custom` edits the selected
zone on that map. This prevents a dashboard from presenting a zone value as if
it were a whole-lawn setting. These standard entities work with Home Assistant
dashboards, automations, and voice assistants. The companion Lawn Mower Card
can use them when explicitly configured; automatic discovery support is tracked
in the card project. The guarded service remains available when you need to
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
use names saved in the Dreame app when vector-map metadata provides them. An
unnamed zone retains the stable `Zone #<id>` fallback.

Zone, spot, and edge actions require both a mower acknowledgement and an
authoritative task-type readback before Home Assistant reports success. If the
mower acknowledges a zone request but starts whole-map mowing, the action fails
instead of silently reporting the wrong job as successful. Explicit targeted
services also update the local `Mowing Action` selection after confirmation.
Unknown current-map IDs, map-scope mismatches, device rejection responses, and
unconfirmed task types are surfaced as failed actions. A repeated targeted call
is rejected before dispatch when the mower is already running the same task type
and the integration cannot prove that a different target was requested.
