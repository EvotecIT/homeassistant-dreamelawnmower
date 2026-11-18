# Optional recovery for a silently stalled Home Assistant player

This opt-in browser example is for the following specific symptom: the native
Live Video dialog stops presenting frames, while Home Assistant's HLS segments
continue advancing. Closing and reopening the dialog restores playback.
It does not diagnose every freeze or repair a stopped upstream stream.

## Evidence and scope

Observed on a MOVA LiDAX Ultra 1000, integration v0.2.196, HA 2026.11.2,
using local HTTP on an iPad, with MOVAhome closed. HLS segment changes were
observed independently on local HTTP and the owner's HTTPS route. After
installing this resource and refreshing the iPad page, playback started on the
first attempt and remained smooth for more than ten minutes. The owner accepted
the outcome. A real iPad freeze/recovery was not deliberately induced; this
does not establish a unique root cause or prove the resource caused that entire
period to be smooth. Automatic reconnection was demonstrated with a synthetic
player in Chrome, separately from the field observation.

This is independent of return-to-dock availability (#319). It changes no Python
code, transport options, movement guards, camera services or background retention.
It is not bundled into the integration release archive or automatically enabled.

## Try the example

1. Save [video-player-recovery.js](examples/video-player-recovery.js) in your
   Home Assistant `www` directory as `video-player-recovery.js`.
2. Edit the `TARGET_ENTITY` constant to the exact entity ID of your integration's
   **Live Video** camera. The placeholder intentionally matches no real camera.
3. Back up your existing Lovelace resources, then add
   `/local/video-player-recovery.js?v=1` as a JavaScript module resource using
   Home Assistant's dashboard resource editor. Do not edit `.storage` manually.
4. Refresh the browser page, open that camera's normal more-info dialog and
   observe playback. A Home Assistant restart is not needed.

The resource polls only already-open players every two seconds. It detects a
15-second lack of decoded frames (or playback-clock progress when frame counters
are unavailable). It seeks toward live only after playback has started and the
seekable end is more than two seconds ahead. Otherwise it reconnects directly;
this is expected, not a failed seek. If seeking does not restore progress, it
reconnects the same HLS element using its normal disconnect/connect lifecycle.
On HA 2026.11.2 this reloads the existing URL, rather than obtaining a fresh one.
Initial media (including pending autoplay) and replacement media get 45 seconds.
A deliberate pause, hidden player or hidden document is not
restarted. A temporary seek gets a grace period. Retries are limited to three,
with backoff and rearming only after 30 seconds of consecutive progress, which
also clears the old backoff deadline. Returning from the back/forward cache
starts a fresh stall baseline without replenishing the retry budget.

An explicitly recorded user pause also takes precedence over a retained media
error. Before the first frame, play/pause intent uses the browser's transient
user-activation signal so an initial/programmatic paused state can still time
out. That startup distinction requires the User Activation API; ordinary pause
handling after media is ready does not depend on it.

The native dialog remains open. A reconnection can briefly interrupt playback
and may exit the browser's native fullscreen mode. Only the configured entity
is considered; another camera's dialog is ignored. No stream is opened when no
target player is visible. No credentials or signed stream URLs are read or logged.

This example relies on Home Assistant's frontend element names and native
more-info routing. These are not a stable public extension API. Recheck after
frontend updates; it is offered for maintainer review, not as a universally
supported solution. A generic fix in Home Assistant's player may ultimately be
the better home for this behavior.

## Remove it

Remove the resource from the dashboard resource editor and refresh the page.
The integration and mower configuration are unaffected. Remove the standalone
file separately if it is no longer wanted.

## Run the isolated tests

With Node.js 22.7.0 or newer, from the repository root:

```sh
node --check docs/examples/video-player-recovery.js
node --test tests/js/video-player-recovery.test.mjs
```

Tests cover silent stalls, frozen decoded-frame counters despite an advancing
clock, missing counters, manual pauses, background/hidden players, stuck seeks,
initial startup, capped backoff, target isolation, normal element lifecycle and
back/forward cache restoration. They do not replace a real-browser field test.
