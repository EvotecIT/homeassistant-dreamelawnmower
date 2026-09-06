# Live video

[Back to the README](../README.md) · [Troubleshooting](troubleshooting.md)

## Before you start

- Use a mower that supports live video and an account where video works in
  Dreamehome or MOVAhome. Model recognition alone does not establish camera support.
- Home Assistant must run on Linux x86_64 or aarch64 for the managed video runtime.
- Keep internet access available, especially during first setup. Neither transport
  option promises internet-independent startup.
- The mower must be in a state where its vendor permits video. Opening the camera
  never starts or moves the mower.

The integration prepares the managed runtime automatically. You do not need an
Android phone, emulator, external runner, or a manually supplied library path.

## Open the camera

Open the mower's **Live Video** camera from its Home Assistant device page, add it
to a dashboard, or choose **Camera** in
[Lawn Mower Card](https://github.com/EvotecIT/lovelace-lawn-mower-card).
A stream starts only when a viewer or snapshot consumer requests media.

A camera can remain available while playback is blocked. Check its
`video_block_reason` attribute for the reason, such as docking or returning.
Do not start the mower just to clear a video error.

Startup time depends on the mower and firmware. An A3 AWD 1000 field test took
about 30–40 seconds to negotiate video; this is not a promised startup time for
other models. Home Assistant uses a compatible WebRTC provider when available
and otherwise falls back to HLS.

## Choose retention behavior

Open **Settings → Devices & services → Dreame Lawn Mower → Configure** and change
**Live video retention** if the default does not suit your dashboard.

| Option | When the session stays ready |
| --- | --- |
| Balanced (default) | Snapshot previews get a 60-second reconnect window. Opening live video keeps the session ready during the current mowing run. |
| Battery saver | Releases the session after the short reconnect grace once the last consumer leaves. |
| Video priority | Snapshot access can also keep the session ready while mowing. |

The short reconnect grace is 15 seconds. Every mode starts on demand and stops
when the mower's state blocks video.

## Transport options

Keep **Live video transport** on **Automatic XP2P with cached restart** for normal
use. It reuses previously working provisioning when possible and refreshes it
when needed. **XP2P (cloud provisioned)** fetches fresh vendor video inputs first
and is available as a compatibility option.

These are not “local only” versus “cloud only” switches. The vendor chooses the
media route. A local media connection does not mean the camera can start without
internet access.

## If playback does not start

1. Check whether video works in the vendor app with the same account.
2. Check the camera's blocking reason and the Home Assistant host architecture.
3. Retry once, then download integration diagnostics before restarting Home
   Assistant.
4. Include the visible error and the failed action in a
   [bug report](https://github.com/EvotecIT/homeassistant-dreamelawnmower/issues/new/choose).
   Review attachments for personal information before posting.

For `device_triple_missing`, follow
[video identity troubleshooting](troubleshooting.md#live-video-identity-is-not-provisioned).
Do not change account region as a routine troubleshooting step: rebinding can
discard cloud-stored maps and settings.

## Limits and technical details

The camera does not expose the vendor's stored photo gallery, patrol movement,
arbitrary voice playback, or two-way live talk. See
[supported mowers](supported-mowers.md) for model-specific evidence.

Maintainers working on provisioning, the runtime, or LAN behavior should use
[Video transport and same-LAN research](video-transport.md).
