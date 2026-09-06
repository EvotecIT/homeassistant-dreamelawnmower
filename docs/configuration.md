# Configuration

[Back to the README](../README.md)

## Connect your mower

Install the integration first, then open **Settings → Devices & services →
Add integration → Dreame Lawn Mower**.

Choose the account type you use in the vendor app (`dreame` or `mova`), the same
country/region, and your account username and password. Select the discovered
mower. If no mower is found, check the account type, region, and whether that
account can see the mower in the app.

Credentials belong in the Home Assistant setup flow, never in repository files,
automation YAML, or issue attachments.

## Find the everyday controls

Open the mower's device page to see the `lawn_mower` entity and supported
companions. Start with the primary mower controls, battery, state, and Map camera.
Diagnostic entities and specialized cameras may be disabled by default.

- [Entity reference](entities.md): common helpers and optional diagnostics.
- [Mowing controls](mowing-controls.md): map/zone selection, schedules, cutting
  preferences, charging windows, rain protection, and anti-theft settings.
- [Supported mowers](supported-mowers.md): compatibility and current limits.

Entity IDs depend on your device name and Home Assistant registry. Replace
example IDs with the entities from your own mower.

## Integration options

Open **Settings → Devices & services → Dreame Lawn Mower → Configure**.

| Setting | Where to learn more |
| --- | --- |
| Polling interval | Controls how frequently Home Assistant refreshes the mower. Map presentation and polling changes apply without reconnecting. |
| Map theme, scale, marker, and display | [Maps and 3D](maps.md) |
| Keep a map preview across restarts | [Maps and 3D](maps.md); saved previews are not live positions |
| Home Assistant notifications | [Notifications and automations](notifications.md); off by default |
| Live video retention and transport | [Live video](live-video.md) |

Connection, video, and restart-preview option changes reload the integration.
Keep advanced native-library and runner overrides at their defaults unless you
are following a specific development or unsupported-host procedure.

## Add a dashboard

[Lawn Mower Card](https://github.com/EvotecIT/lovelace-lawn-mower-card) is an
optional dashboard card for these entities. Install the card separately, add it
to a dashboard, and select the mower entity in its visual editor.

Use the primary **Map** camera for the interactive mowing map. The card can
discover companion entities automatically; choose them explicitly if needed.
The standard Home Assistant device page and dashboard cards remain usable
without the companion card.

## Automations

Use [the notification guide and blueprint](notifications.md) for fault alerts,
warnings, maintenance reminders, and offline notifications. The integration's
built-in notifications are persistent Home Assistant items, not mobile pushes.

Schedule switches can be used with normal Home Assistant switch actions.
Prefer those over low-level write probes. Review
[mowing controls and safety guards](mowing-controls.md) before creating any
automation that changes settings or moves the mower.
