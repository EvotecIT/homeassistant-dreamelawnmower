# Optional recovery for a silently stalled Home Assistant player

This opt-in browser example is for the following specific symptom: the native
Live Video dialog stops presenting frames, while Home Assistant's HLS segments
continue advancing. Closing and reopening the dialog restores playback.
It does not diagnose every freeze or repair a stopped upstream stream.

## Evidence and scope

The stall was observed on a MOVA LiDAX Ultra 1000 using integration v0.2.196
and Home Assistant 2026.11.2 on an iPad with MOVAhome closed. HLS segments
continued advancing over HTTP and HTTPS. After an earlier version of this
resource was installed, iPad playback stayed smooth for more than ten minutes.
Automatic reconnection was verified separately with a simulated stall in Chrome.

The example affects only the browser player. It runs when explicitly added as a
dashboard resource.

## Try the example

1. Save [video-player-recovery.js](examples/video-player-recovery.js) in your
   Home Assistant `www` directory as `video-player-recovery.js`.
2. Edit the `TARGET_ENTITY` constant to the exact entity ID of your integration's
   **Live Video** camera. The placeholder intentionally matches no real camera.
3. Back up your existing Lovelace resources, then add
   `/local/video-player-recovery.js?v=1` as a JavaScript module resource using
   Home Assistant's dashboard resource editor. Do not edit `.storage` manually.
4. Refresh the browser page, open that camera's normal more-info dialog and
   observe playback. The Lawn Mower Card's **Live Video** action can also open
   the native dialog. Reloading the dashboard is enough to load the module.

The resource polls the configured camera's native more-info dialog every two
seconds. It waits for playback progress before watching for a stall. After
15 seconds without new frames, it seeks toward live when the seekable end is
more than two seconds ahead. When frame counters are unavailable, it measures
playback-clock progress. If a seek is unavailable or fails to restore progress,
the example reconnects the player and leaves the dialog open. On HA 2026.11.2,
reconnection reloads the existing stream URL.

Recovery waits while the video is paused, the player is hidden, or the
browser tab is in the background. If playback never starts, close and reopen
the dialog. A reconnect can briefly interrupt playback or exit native
fullscreen. The same player gets at most three reconnects, with backoff.
Thirty seconds of consecutive playback progress reset the retry count.
Restoring the page from the browser's back/forward cache resets the stall
timer and preserves the retry count. Inline camera players, including the
Lawn Mower Card's camera panel, run independently of this example.

This example relies on Home Assistant's frontend element names and native
more-info routing. Recheck playback after frontend updates, since those details
can change.

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
clock, missing counters, pause and visibility, stuck seeks, capped backoff,
dialog and entity scope, element lifecycle and back/forward cache restoration.
