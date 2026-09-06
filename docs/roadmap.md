# Open development work

Current setup, supported devices, and controls are documented in the
[README](../README.md), [supported mowers](supported-mowers.md), and
[mowing controls](mowing-controls.md). This backlog lists remaining validation
and feature work, not the history of completed implementation.

## Model and firmware coverage

- [ ] Add sanitized fixtures for additional mower families, firmware versions,
  and idle, return, dock, and fault-recovery transitions.
- [ ] Fill the model-specific map, point-cloud, and camera proof gaps recorded
  in the support matrix. Do not infer one feature from another working feature.
- [ ] Test schedule enable/disable and full-upload behavior in supervised Home
  Assistant sessions, including multi-map slots and readback after writes.
- [ ] Collect firmware-update responses from additional models. Keep the existing
  update entity tied to the app-approved target, not plugin metadata or an
  unverified catalog.

## Protocol questions

- [ ] Decode remaining realtime status fields from repeatable captures. Record
  evidence and confidence before widening normalized sensor support; unknown
  service-5 values must retain their diagnostic labels.
- [ ] Add fixtures for additional map boundaries, restrictions, paths, and
  semantic objects. Keep unverified geometry neutral rather than labeling it
  as a no-go zone or cut-area coverage.
- [ ] Verify maintenance and statistics fields per model before adding or
  widening their entity exposure.

Protocol evidence and probe entrypoints live in
[Dreamehome research](dreamehome-research.md) and
[development](development.md).

## Separate feature candidates

- [ ] Investigate explicit same-LAN video and two-way audio with separate
  transport and device proof; a working XP2P stream does not establish either.
- [ ] Evaluate guarded map/zone editing only after its write contract and
  readback are proven.
- [ ] Evaluate a supervised manual-driving UI separately from ordinary mowing
  controls, retaining the client safety checks and bounded movement.

Live video already exists; its host requirements and current limits are in
[live video](live-video.md) and [video transport](video-transport.md).
