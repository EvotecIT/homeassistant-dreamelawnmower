# Mowing permission, deferred starts, and recovery

[Back to automations](notifications.md#guarded-weekly-mowing)

Use external permission when mowing depends on soil moisture, irrigation,
garden use, or another HA condition. The integration still checks the mower's
native eligibility. Existing automations retain their defaults: attempt one
start at each Schedule block boundary, skip unsuitable conditions, and do
nothing at block end. Block length does not determine mowing duration.

## Schedule ownership and rain protection

For HA-managed starts, disable native plans on **every map**, including default
slots. The guarded action checks this; an enabled or unknown native plan blocks
the start. An empty calendar is not proof that all plans are disabled.

HA permission cannot inhibit starts initiated independently by an onboard
schedule, the vendor app, voice control, or another manual action. Rain
Protection enables the mower's own rain response. Changing that switch or its
after-rain delay does not simulate a wet rain sensor or force an active delay.
Keep native protection enabled; express wet-ground rules through permission.

## External permission

Select a binary sensor, input boolean, or switch in the blueprint's optional
**External mowing permission** input. Only `on` permits a start. `off`, missing,
`unknown`, and `unavailable` block it. An optional `reason` attribute supplies
readable detail; `block_reason` is also accepted.

The default maximum report age is 1,800 seconds. This uses the last report,
not the time the value last changed. Zero disables only this additional age
check. A composite helper must check its own source freshness: a newly evaluated
template does not make an old moisture measurement fresh. Reporting age also
depends on the source integration's update behavior.

Custom automations use the same action:

```yaml
action: dreame_lawn_mower.start_scheduled_mowing
target:
  entity_id: lawn_mower.garden
data:
  task_type: all
  minimum_battery: 40
  permission_entity: binary_sensor.soil_mowing_permitted
response_variable: mowing_result
```

This skips immediately when blocked. It neither creates a pending run nor
interrupts a mower already working. Permission is rechecked after mower
preflight and at serialized command admission, so conditions changed during
preparation can revoke dispatch.

## Soil moisture and irrigation examples

The [soil-moisture package fragment](../examples/automations/soil_moisture_permission.yaml)
contains a Threshold helper and a permission template. Replace the source entity
and calibrate the thresholds, report-age limit, and drying wait for your garden.
It assumes percentage units from 0 to 100; convert other units first.

Its illustrative threshold is 60 with hysteresis 5: wetness turns on above 65
and clears below 55. Permission then requires 30 minutes of acceptable
conditions. Missing or stale measurements block it. These are example values,
not universal lawn-care thresholds. A restart conservatively resets the drying
wait. Install the fragment through HA's existing package/configuration mechanism
or create equivalent helpers; importing the blueprint does not install it.

The [irrigation package fragment](../examples/automations/irrigation_permission.yaml)
blocks while irrigation is active or unknown. After watering, an automation
saves a 15-minute settling deadline in an Input Datetime helper. At HA startup
it deliberately starts a fresh settling interval when watering is off, so a
restart cannot clear the wait early. Replace its switch and tune the interval.
When adding it to a running HA instance, run its settling automation once while
irrigation is off, or restart HA. Permission remains blocked until initialized.

To combine the examples, select this helper in the mowing blueprint:

```yaml
template:
  - binary_sensor:
      - name: Garden mowing permitted
        unique_id: garden_mowing_permitted
        state: >-
          {{ is_state('binary_sensor.soil_mowing_permitted', 'on')
             and is_state('binary_sensor.irrigation_mowing_permitted', 'on') }}
        attributes:
          reason: >-
            {% if not is_state('binary_sensor.soil_mowing_permitted', 'on') %}
              {{ state_attr('binary_sensor.soil_mowing_permitted', 'reason') }}
            {% elif not is_state('binary_sensor.irrigation_mowing_permitted', 'on') %}
              {{ state_attr('binary_sensor.irrigation_mowing_permitted', 'reason') }}
            {% else %}
              Garden conditions permit mowing
            {% endif %}
```

Add frost, garden-use, or other rules to the same permission helper. Coordinate
the other direction too: the irrigation-start automation should require a
positively confirmed docked mower or wait for a supervised return. Mowing
permission alone cannot stop independently controlled watering from starting.

## Optional bounded deferral

Set **Deferred-start window** above zero to retain one pending attempt. For
example, 180 lets a 10:00 request start before 13:00 if conditions become
acceptable. Zero preserves immediate skip.

Deferral reconsiders external permission, low battery, active rain delay, and
temporary eligibility-refresh/connectivity failures. Unknown native plans,
unfinished tasks, wrong targets, and faults do not create indefinite retries.
Each actual start repeats native checks. There is one managed run per mower,
with no backlog. Its map is bound at acceptance, so a later map change cannot
redirect it. Saved targets still pass normal target validation. Optional
`run_id` deduplicates the latest accepted occurrence; the blueprint supplies it.

Intent is stored privately before dispatch. After restart, only a
never-dispatched intent is reconsidered, within its original deadline and with
matching known task identity. Unknown or changed prior identity cancels it.
A record that reached dispatch, including one with a lost acknowledgement,
is never replayed. A latest start is separate from a finish deadline: expiry
and the Schedule block's end send no pause or dock command.

## Optional interruption and recovery

With permission selected, choose what to do if it is lost during this
automation's own run:

| Selection | Effect |
| --- | --- |
| No action | Default; permission gates starts only. |
| Pause | Pause the confirmed owned task in place. |
| End and dock | End the task and return; no automatic recovery. |

Interruption requires known firmware task identity before departure and a new
confirmed identity afterward. Acknowledgement alone does not establish task
ownership. If identity is not confirmed within the observation window, the
integration reports the limitation and sends no further automatic movement.

Pause mode requires the mower to report that **automatic resume after charging
is disabled**. Configure that in the vendor app; this action does not rewrite
it. Unknown or enabled auto-resume prevents a pause-managed start. If a pause
request meets an already returning/docked unfinished task, it ends and docks
instead of preserving a charging task that might restart independently. A
changed auto-resume setting observed during a pause hold also ends and docks.

Recovery is separately opt-in: enable **Resume this automation's paused task**
and set a positive **Latest automatic recovery** window, measured from the
original request. Recovery requires the same paused task/map, valid permission,
sufficient battery, no fault/rain delay, and disabled native schedules. The
native start plan must confirm its resume branch; a fresh-task branch is
rejected. An acknowledged request awaiting confirmation is not resent.

Normal HA mower/device controls, disabling the owning automation, or reloading
automations cancel pending starts and recovery. Unexpected task/map changes or
pauses also cancel ownership. Vendor-app intervention is detectable only through
observed task/state changes; HA cannot identify an app command that leaves
those values unchanged. HA restart/integration reload cancels recovery of an
active or paused run because intervening control cannot be ruled out.

To revoke automation authority without moving the mower:

```yaml
action: dreame_lawn_mower.cancel_scheduled_mowing
target:
  entity_id: lawn_mower.garden
```

Use normal pause/dock separately to interrupt physical mowing. Cancellation or
expiry leaves a paused mower paused; it never starts a replacement task.

## Outcomes and related equipment

Action responses, `last_scheduled_run`, `recent_scheduled_runs`, and
`dreame_lawn_mower_scheduled_run` events report decisions. Managed events include
`run_id` and `automation_entity`. The blueprint routes later managed outcomes
to the same notification and custom actions as the initial attempt.
`scheduled_automation` exposes phase, map, and deadlines.

| Status | Meaning |
| --- | --- |
| `skipped` | No command; the attempt was ineligible. |
| `deferred` | One attempt is waiting within its start window. |
| `submitted` | Acknowledged; intended active state not yet confirmed. |
| `started` | Active task evidence observed. |
| `paused` | Owned task held; reason explains recovery eligibility. |
| `cancelled` | Automatic authority revoked, or end-and-dock policy applied. |
| `expired` | Start/recovery window ended. |
| `finished` | Task no longer active; does not prove full lawn coverage. |
| `failed` | Command, observation, or persistence could not be confirmed. |

External reasons are `external_permission_blocked`,
`external_permission_unknown`, and `external_permission_stale`; `detail` gives
readable context. Existing mower-native reasons remain unchanged. Failed
observation never permits movement from an old snapshot.

Garage/gate workflows should confirm an open contact before granting start
permission. Handle closure in a separate automation using a trustworthy
clear-of-doorway sensor, bounded timeout, and failure notification. Essential
closure placed only after a start action can be lost when that action fails.
Docked state or delayed map coordinates alone do not prove doorway clearance.

These HA/cloud automations are not a physical safety interlock. Detection,
polling, and command latency apply, and no HA rule enforces a new decision while
HA, the network, or the mower is unreachable. Keep native protection enabled
and supervise initial qualification on your model.
