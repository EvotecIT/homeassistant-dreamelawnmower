# VIAX 500 point-cloud investigation

[Card issue #52](https://github.com/EvotecIT/lovelace-lawn-mower-card/issues/52)
reports failed 3D downloads and, later, a disabled map selector. They are separate
paths: the integration discovers and validates the point cloud; the card displays
it and controls the selector.

## Current conclusion

The owner [confirmed that MOVAhome shows only a 2D map](https://github.com/EvotecIT/lovelace-lawn-mower-card/issues/52#issuecomment-5652909635),
both while mowing and while docked. The 0.2.98 diagnostics show three completed
failed attempts across map indices 0 and 1: generation was acknowledged, but
the selected indexed OBJ slot was an empty string, no usable announcement was
observed, and no download began. Those attempts lasted about 45-49 seconds.

This does not establish a working VIAX point-cloud export. It also does not
prove that every firmware version lacks one. VIAX remains **unverified**, not
permanently unsupported. LiDAR navigation and 2D mapping are separate features.
The earlier assumption that a successful A2 export implied a broken VIAX export
was not justified by the evidence.

Read-only shared-account validation on the same firmware retrieved both map
slots and rendered them through the existing vector-map and mowing-map scene
code. The current map index matched the mower's inventory. Battery and docked
status were readable too. The indexed 3D-object query was accepted by both the
cloud and mower, but returned two empty slots; this was not an authorization
rejection. These results establish working map access, not raw LiDAR export.
The docked status packets supplied no position, so live position and trail
updates still need observation during normal mowing. No movement, map changes,
generation, or camera activation was used for this validation.

## What the supplied evidence establishes

The issue's first ten JSON attachments through comment `5652302135` contain four
mower diagnostics and six HACS diagnostics. A subsequent mower capture in
[comment 5652426735](https://github.com/EvotecIT/lovelace-lawn-mower-card/issues/52#issuecomment-5652426735)
adds a fifth mower report, on integration 0.2.97. All five mower reports identify
`mova.mower.g2583`, marketed as VIAX 500, on firmware `4.3.6_0260`.

| Integration in mower report | Recorded result | Subsequent change |
| --- | --- | --- |
| 0.2.93 | Seven recorded point-cloud failures; generation timed out | [PR #191](https://github.com/EvotecIT/homeassistant-dreamelawnmower/pull/191), released in 0.2.94, corrected fixed-object baseline handling |
| 0.2.94 | One recorded point-cloud timeout | [PR #192](https://github.com/EvotecIT/homeassistant-dreamelawnmower/pull/192), released in 0.2.95, allowed acknowledged fixed `.bin` objects for the exact VIAX model |
| 0.2.95 | Seven recorded point-cloud timeouts | [PR #193](https://github.com/EvotecIT/homeassistant-dreamelawnmower/pull/193), released in 0.2.96, added indexed verification for stable announcements |
| 0.2.96 | Eight recorded timeouts; acknowledgement true, announcement route, object not observed | [PR #195](https://github.com/EvotecIT/homeassistant-dreamelawnmower/pull/195), released in 0.2.97, added VIAX indexed fallback and attempt counters |
| 0.2.97 | Seven failed attempts; acknowledgement true, 15-19 indexed checks per attempt, zero downloads | [PR #196](https://github.com/EvotecIT/homeassistant-dreamelawnmower/pull/196), released in 0.2.98, added response shapes and timeout-safe evidence |
| 0.2.98 | Three completed failed attempts; empty selected OBJ slots, no usable announcement, zero downloads | Separate export capability from the request route; stop automatic retries after no file is published |

These are counts within individual captures, not a claim that every event is
unique across reports. The latest HACS report confirms integration 0.2.97 and
card 0.2.9; the subsequent mower report supplies actual 0.2.97 attempt counters.
Those counters locate the failure before a download attempt, not in PCD validation.
The subsequent 0.2.98 capture distinguishes empty selected OBJ slots from an
unfamiliar indexed response shape.
The paused-selector correction
in [card PR #55](https://github.com/EvotecIT/lovelace-lawn-mower-card/pull/55)
shipped in [card 0.2.10](https://github.com/EvotecIT/lovelace-lawn-mower-card/releases/tag/v0.2.10).
Installed card metadata does not establish which bundle an existing browser tab
has loaded.

## What the earlier diagnostics did not prove

The mower reports contain two OBJ slots, each summarized as
`{"url_present": false}`. The metadata read did not request signing. Its count
included empty slots, and its projection omitted unknown extensions. These
summaries therefore do **not** establish that:

- either slot held a `.bin` file;
- the signer refused a URL;
- a previously downloaded file retained its checksum or HTTP validators;
- VIAX kept a usable `99.20` filename and timestamp unchanged.

The stable-announcement observation on firmware `4.3.6_0625` in
[Dreamehome research](dreamehome-research.md) was an A2 observation. It is not
a capture from this VIAX. Likewise, a model capability flag is not proof that a
particular cloud upload route works on that firmware.

## Diagnostics and compatibility

The client now reports bounded response shapes, filtering reasons, object-slot
state, baseline availability, and signing/download/validation stages. The HTTP
failure and standalone probe expose the same sanitized evidence as the mower
diagnostics. No additional polling, generation commands, accepted extensions,
or model-specific freshness exceptions are introduced by this diagnostic change.

Each failed attempt has an `attempt_id` and a timed `timeline` containing the
first eight and latest 24 observations. `first_failure` preserves the first
classified download or validation failure even if it falls outside that window;
`trace_dropped_events` makes omitted observations explicit. `worker_finished`
is false when the caller's deadline expires before the worker returns. The report
then retains evidence gathered up to that deadline, not any later worker result.

Validation failures identify the rejected rule, such as unsupported PCD encoding,
inconsistent payload length, or non-finite coordinates. Unfamiliar JSON values
expose only recognized member names and coarse types, plus a count of unknown
members. Object names, URLs, coordinates, arbitrary keys, and exception messages
are excluded. Very large JSON structures are marked as truncated.

These diagnostics distinguish known failure paths; they cannot guarantee that an
arbitrary new firmware schema can be implemented without a further targeted
capture. An unknown shape is evidence of an unrecognized response, not proof of
missing mower capability or permission to accept unvalidated map data.

Existing `.pcd`/`.bin` validation, requested-map checks, byte limits, deadlines,
and stored-map rules remain in force. Existing VIAX fixed-object handling is
retained for compatibility; the supplied reports do not validate that hypothesis
on the reporter's mower.

The integration exposes `feature_capabilities.point_cloud` on the mower and map
camera, and includes it in diagnostics. A2 has confirmed model support; a
validated download supplies stronger observed evidence for any model for the
current integration session. Failed attempts never mark a model unsupported or
erase positive evidence. The companion card asks for an explicit load on an
unverified mower, and a completed no-publication failure stops automatic retries.

Keep 2D mapping available independently. Do not request repeated generation
captures unless new evidence, such as an actual official-app 3D view or a
published point-cloud object, justifies another attempt. See
[3D troubleshooting](troubleshooting.md) for the bounded diagnostic contract.
