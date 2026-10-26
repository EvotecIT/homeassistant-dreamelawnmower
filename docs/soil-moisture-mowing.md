# Mow only when the garden is ready

[Back to the README](../README.md) · [Advanced automation options](automation-permission.md)

Use **Mowing automation** to choose a schedule, soil moisture sensor and optional
irrigation switch through Home Assistant's UI. You do not need to write YAML,
templates or import a blueprint. The integration runs the rules even when your
dashboard is closed. This workflow requires **Home Assistant 2026.7 or newer**.

The idea is simple: the automation starts the mower only when its **Mowing
permitted** sensor is on. If the ground is too wet, it skips that start or waits
for conditions to improve, depending on your settings. The mower's normal
battery, connectivity, fault and rain-delay checks still apply.

## Set it up through the UI

1. Update the Dreame Lawn Mower integration and restart Home Assistant.
2. Under **Settings → Devices & services → Helpers**, create a **Schedule**
   helper. Add the weekly times when you want mowing to start. The end of a
   schedule block does not stop mowing or define its duration.
3. In the vendor app, disable native mowing plans on **every map**, including
   default slots. Keep **Rain Protection enabled**. Disable any older HA mowing
   automation that would start the same mower at those times.
4. Open **Settings → Devices & services → Dreame Lawn Mower → Configure →
   Mowing automation**. Select your Schedule helper. Leave the automation
   disabled while checking the setup.
5. Select your soil moisture sensor. It must report a percentage from 0 to 100.
   Choose the wet and dry thresholds and how long acceptable conditions must
   last before starting. If you use irrigation, select the entity that is `on`
   while watering and set a wait after watering stops. An additional on/off
   permission entity is optional.
6. Choose what happens when conditions are unsuitable. Leave the wait window
   at **0** to skip that occurrence, or enter a bounded wait such as **180
   minutes**. For a first setup, leave interruption at **No action** and
   automatic resume off. Leave outcome notifications enabled.
7. Review the selected entities and confirm that native plans are disabled on
   every map. Save. On the mower's device page, inspect **Mowing permitted**
   and its `reason` attribute. Enable the **Mowing automation** switch when the
   settings are ready. Supervise the first scheduled run.

Saving settings does not start the mower. Enabling the automation during an
active schedule block does not catch up on that block; it waits for the next
block start. You can edit the setup through the same Configure menu.

If Lawn Mower Card is installed, its **Mowing automation** panel shows the
enabled state and garden permission reason. **Set up** or **Edit setup** opens
the relevant integration entry; choose **Configure → Mowing automation**.
The card is optional and stores no separate copy of the rules.

## Understand the example values

These are starting values, not universal lawn-care thresholds. Calibrate them
for your sensor and soil.

| Setting | What the example does |
| --- | --- |
| Wet threshold: 65% | Block mowing when moisture rises above 65%. |
| Dry threshold: 55% | After a wet period, wait until moisture falls below 55%. |
| Drying wait: 30 minutes | Require 30 continuous minutes of acceptable soil readings. |
| Maximum soil report age: 1800 seconds | Block when the source has not reported for 30 minutes. |
| Irrigation settling wait: 15 minutes | Wait 15 minutes after watering is confirmed off. |

The gap between wet and dry thresholds prevents repeated switching near one
threshold. On first setup or after restart, soil must cross below the dry
threshold before the drying wait begins. Missing, invalid or stale readings
block mowing and reset the drying wait. If your sensor normally reports less
often, adjust the maximum source report age to match its actual reporting
behavior. A fresh permission evaluation does not refresh a soil measurement.

A restart conservatively starts fresh drying and irrigation settling waits.
Irrigation that is `unknown` or `unavailable` blocks mowing. An additional
permission entity must explicitly be `on` and have reported within 30 minutes;
use a helper that also checks its own source freshness when combining rules.

## Skip or wait for the ground to dry

- **Wait window 0:** a wet 10:00 start is skipped. The next attempt is at the
  next configured Schedule block start.
- **Wait window 180:** the 10:00 occurrence can wait until before 13:00. If
  the soil becomes acceptable at 11:00 and completes its 30-minute drying
  wait, mowing can start around 11:30, provided all mower checks pass.
  Otherwise that occurrence expires.

There is one managed run per mower and no backlog of missed starts. A wait
window is the latest permitted start, not a deadline to finish mowing. The
outcome notification explains whether a run is waiting, started, skipped or
expired. The mower's `last_scheduled_run` attribute provides the reason.

## Mow zones in rotation

Once the automation is set up, three controls appear on the mower's device
page under **Configuration**, and in Lawn Mower Card's Mowing automation panel.
They apply at once, without reloading the integration, and the next Schedule
block start reads them.

| Control | Default | Meaning |
| --- | --- | --- |
| What to mow | Whole lawn | or **Due zones**, least recently mowed first |
| Rest days between zone mowings | 1 | 0 means once a day; 1 means every other day |
| Maximum area per start | 0 | whole zones are added while their total fits; 0 means no limit |

With **Due zones**, the automation reads the same history as the **Least
recently mowed zone** sensor for the current map. A zone is due when it has no
confirmed completion, or when its rest days have passed. Zones without a
completion come first, then the earliest due date; on the same due date the
zone with fewer rest days leads. Zones are never split; the first due zone is
always included, so a zone larger than the limit is not starved.

A zone can rest a different number of days than the default — for example a
front strip mowed every day while the rest of the lawn rests a day:

```yaml
action: dreame_lawn_mower.set_rotation_zone_rest_days
target:
  entity_id: lawn_mower.garden
data:
  zone_id: 6
  rest_days: 0
```

Omit `rest_days` to return the zone to the default. The setting applies at
once without reloading the integration. The `mowing_automation` attribute
lists every zone in `rotation_zones` with its rest days and the date it is due
again (`due_on`; empty means due now).

The start is skipped, never widened to the whole lawn, when no zone is due
(`no_zone_due`) or the history is incomplete or not tied to the current map
(`zone_history_unavailable`). A zone counts as mowed only when a session
summary confirms its completion; an interrupted zone stays due.

The mower's `mowing_automation` attribute includes a `plan`: the next block
start and the zones it would request if it began then. It uses the same
selection as the start itself; permission, battery and native checks still
apply at that moment.

## Optional interruption and recovery

The default **No action** checks conditions before starting and does not
interrupt mowing already underway. Choose **Pause** to hold this automation's
confirmed task when permission is lost, or **End and dock** to end it and
return without automatic recovery. Pause mode requires native automatic resume
after charging to be disabled in the vendor app.

Automatic resume is a separate opt-in. It requires Pause, a positive recovery
window measured from the original scheduled request, the same confirmed task
and map, and fresh permission and mower checks. HA restart or integration
reload cancels recovery of an active or paused task. Turning **Mowing
automation** off cancels this wizard's pending starts and recovery without
moving the mower; use normal pause/dock controls separately when needed.

The automation controls its own starts and confirmed tasks. It cannot prevent
independent starts from the vendor app, manual controls or onboard schedules.
Keep native Rain Protection enabled. Changing a rain-protection switch does
not simulate rain or force an active rain delay. HA/cloud connectivity and
command latency still apply; this is not a physical safety interlock.

## Existing custom automations and YAML setup

Existing blueprints and scheduled-mowing actions retain their defaults. You can
keep using them instead of the built-in wizard; avoid enabling two schedules
for the same mower. The [advanced guide](automation-permission.md) covers custom
actions, cancellation, saved targets and combined permission rules.

For the original soil-sensor walkthrough using a YAML package:

1. Copy the [soil permission example](../examples/automations/soil_moisture_permission.yaml)
   into a file named `soil_mowing.yaml` next to your `configuration.yaml`.
   Replace every occurrence of `sensor.garden_soil_moisture` with your actual
   sensor ID from **Developer tools → States**.
2. If you do not already use packages, add the following to
   `configuration.yaml`. Merge it into an existing `homeassistant:` section
   rather than adding a duplicate. If you already use packages, follow your
   existing package layout instead.

   ```yaml
   homeassistant:
     packages:
       soil_mowing: !include soil_mowing.yaml
   ```

3. Check the HA configuration and restart. Inspect the created
   `binary_sensor.lawn_soil_too_wet` and `binary_sensor.soil_mowing_permitted`.
   If their IDs differ because names were already used, update the wetness
   sensor reference in the example and select the actual permission ID below.
4. Disable native plans on every map and keep Rain Protection enabled. Create
   a Schedule helper, then [import the guarded weekly blueprint](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2FEvotecIT%2Fhomeassistant-dreamelawnmower%2Fmain%2Fblueprints%2Fautomation%2Fdreame_lawn_mower%2Fguarded_schedule_mowing.yaml).
   Re-import if you have an older copy. Create an automation from it.
5. Select your mower, Schedule helper and **All areas**. Set **External mowing
   permission** to your soil permission sensor. Leave **Maximum permission
   report age** at 1800 seconds for this example. Choose zero or a bounded
   deferred-start window, and leave interruption and recovery off initially.
6. Check the permission state and `reason` before the first run, then read the
   outcome notification at the next scheduled start.

Importing the blueprint does not install the soil helper. See
[HA's package documentation](https://www.home-assistant.io/docs/configuration/packages/)
for configuration layouts. The [irrigation example](../examples/automations/irrigation_permission.yaml)
and [advanced guide](automation-permission.md#soil-moisture-and-irrigation-examples)
cover the corresponding custom irrigation workflow.
