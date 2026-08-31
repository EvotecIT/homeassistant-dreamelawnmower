# Notifications and issue automations

The integration provides two complementary notification paths:

1. An opt-in, integration-managed persistent notification for hard faults and
   actionable status notices.
2. An importable automation blueprint for mobile notifications, persistent
   notifications, maintenance reminders, offline alerts, delays, repeats, and
   custom Home Assistant actions.

Both paths are disabled until you choose to use them.

## Integration-managed notifications

Open **Settings → Devices & services → Dreame Lawn Mower → Configure**, then
set **Home Assistant notifications**:

- **Off** is the default and creates nothing.
- **Hard faults** follows the mower's `Error Active` condition.
- **Hard faults and warnings** also follows status notices classified as
  `alert`, `attention`, or `unknown`. Informational notices remain silent.

Home Assistant keeps at most one fault item and one warning item per mower.
An item is updated when the displayed detail or code changes and dismissed
when the condition clears, the option is disabled, or the integration unloads.
This path uses Home Assistant's persistent notification panel; it does not
select a phone or other delivery service.

## Import the blueprint

[Import the Dreame mower condition notifications blueprint](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2FEvotecIT%2Fhomeassistant-dreamelawnmower%2Fmain%2Fblueprints%2Fautomation%2Fdreame_lawn_mower%2Fmower_condition_notifications.yaml),
then create an automation from it.

Select these entities from one mower:

- the mower entity
- `Error Active`
- `Error`
- `Status Notice`
- `Online`
- `Maintenance Warning`
- `Maintenance Due`

The blueprint ignores informational status notices. It reports only alert,
attention, or unknown-tier notices, matching the integration option. Its
confirmation delay suppresses brief conditions that clear on their own.
Recovery immediately dismisses the blueprint's persistent item and can run
optional recovery actions. Home Assistant startup and automation reloads also
reconcile conditions that are already active and remove stale persistent items.

Choose one delivery mode:

- **One-shot custom action** runs the notification actions once for each new
  condition.
- **Home Assistant persistent notification** creates or updates one stable
  item per mower and condition.
- **Repeating custom action** reruns the notification actions at the selected
  interval while that exact condition remains active.

When the blueprint uses persistent delivery, keep the integration-managed
option off so the two paths do not report the same condition twice.

## Mobile notification action

For one-shot or repeating delivery, configure an action such as:

```yaml
- action: notify.mobile_app_your_phone
  data:
    title: "{{ issue_title }}"
    message: "{{ issue_message }}"
```

Replace `notify.mobile_app_your_phone` with a notification action that exists
in your Home Assistant instance. The action can use these variables:

- `issue_title`
- `issue_message`
- `issue_kind` (`fault`, `notice`, `offline`, `maintenance_warning`, or
  `maintenance_due`)
- `issue_entity`
- `is_recovery`

## Other Home Assistant actions

Notification and recovery action lists can contain any normal Home Assistant
action. Useful choices include turning on a warning light, making a local TTS
announcement, writing to a logbook, or calling a user-owned script that routes
alerts by time of day. For example:

```yaml
- action: light.turn_on
  target:
    entity_id: light.garden_warning
  data:
    flash: long
```

A matching recovery action could turn that light off:

```yaml
- action: light.turn_off
  target:
    entity_id: light.garden_warning
```

## Safety boundary

The blueprint never starts, resumes, returns, or manually moves the mower.
A fault can describe a blocked wheel, lifted mower, positioning problem, or
another condition that requires inspection. Automatically issuing a generic
recovery command would be unsafe because Home Assistant cannot know that the
physical cause has been removed. If you add a mower-control action yourself,
make it specific to a condition you understand and include any physical safety
checks required for your property and model.
