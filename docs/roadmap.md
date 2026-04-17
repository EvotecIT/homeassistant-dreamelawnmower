# Roadmap

This document turns the current mower research, live A2 validation, and upstream issue patterns into a practical backlog for this repository.

The goal is not to mirror another integration feature-for-feature. The goal is to build a mower-native Home Assistant integration that:

- tolerates model differences instead of crashing on them
- exposes clear mower state for automations
- grows new entities only when the protocol proves they exist
- keeps fixture-driven regressions in the repo so we do not guess blindly

## Current baseline

Already working in this repo today:

- Dreamehome and MOVAhome account login
- mower autodiscovery
- one `lawn_mower` entity
- start, pause, and dock actions
- diagnostic sensors and binary sensors
- debug snapshot capture
- fixture-backed parser regressions for paused and paused-with-error A2 states

## Guiding rules

When adding new support, prefer these rules:

- unknown device properties must never crash setup or refresh
- every new model family should produce a sanitized fixture before broad entity expansion
- mower-specific data sources should win over vacuum-era abstractions
- user-facing entities should show cleaned values, but raw values must remain available for diagnostics

## Phase 1: Model Compatibility Hardening

Why this comes first:

- upstream open issues are dominated by startup failures on additional brands and rebadged models
- the common pattern is an unknown property ID or model-specific payload shape causing the entire integration to fail

Targets:

- make unknown property IDs non-fatal
- preserve unrecognized raw values for diagnostics
- expand model-name mapping without hardcoding behavior per model where avoidable
- gate entities by discovered capability and data presence

Checklist:

- [x] catch unknown property enum values and store them in a raw diagnostics bucket
- [x] ensure refresh continues even when one property block is unrecognized
- [ ] add model-family fixtures as new users report supported hardware
- [x] normalize display names for known Dreame, MOVA, LiDAX, Viax, and related mower rebadges
- [x] add regression tests for unknown-property tolerance

Definition of done:

- a newly reported mower should degrade gracefully into a minimal working device instead of failing setup

## Phase 2: A2 and A2 Pro Telemetry Expansion

Why this is next:

- the A2 family appears to expose richer runtime and maintenance data than the current cloud-property surface
- upstream reports strongly suggest important data lives in MQTT status blobs rather than the simple cloud snapshot

Targets:

- identify and decode A2-family runtime blobs
- expose maintenance and statistics sensors only when verified by payloads
- improve understanding of contradictory states like paused-plus-error or dock-contact wobble

Checklist:

- [x] capture raw realtime MQTT payloads and `siid/piid` pairs for diagnostics
- [ ] capture and decode A2-family MQTT or realtime status payloads
- [ ] document which fields are authoritative for live state, battery, task, and maintenance
- [ ] add normalized snapshot fields only after fixture validation
- [ ] expose maintenance/statistics sensors behind data-driven existence checks
- [ ] add fixtures for idle, docked-charging, paused, and paused-with-error A2 states

Definition of done:

- A2 and A2 Pro users get richer sensors without introducing guesswork for other models

## Phase 3: Read-Only Vector Map Support

Why this matters:

- mower maps are polygon or vector data, not the vacuum bitmap model
- upstream work already suggests zone boundaries, forbidden areas, and trails can be extracted from cloud batch APIs

Targets:

- build a mower-native vector map parser
- render zone maps as a read-only first step
- avoid reusing vacuum map assumptions where they do not fit mower data
- use confirmed app-side cloud endpoints before guessing at more legacy vacuum map paths

Checklist:

- [x] confirm Dreamehome app-side map-adjacent cloud endpoints from the real Android package
- [ ] capture real mower `MAP.*` and `M_PATH.*` payload fixtures
- [x] probe `device/info`, `iotstatus/props`, and `device/listV2` from Python with captured A2 credentials
- [x] expose Python-side helpers for `device/info`, `iotstatus/props`, and `device/listV2`
- [x] extract mower app protocol hints like the `2.1` state table into reusable Python-side tooling
- [x] add a Python-side property range scanner so `iotstatus/props` discovery is repeatable
- [ ] implement a parser for mower zones, boundaries, forbidden areas, and path segments
- [ ] implement a simple renderer for read-only Home Assistant display
- [ ] expose a map entity or camera-style surface only after the parser is stable
- [ ] add tests for parser chunk reassembly, boundary extraction, and trail parsing

Definition of done:

- users can view mower zones and paths without relying on vacuum map code paths

## Phase 4: Automation-Friendly State Surface

Why this matters:

- users want garage-door, dock, and return-home automations
- those automations only work when `activity`, `state`, and raw flags are understandable and stable

Targets:

- make mower transitions easier to automate
- keep raw state available so unusual dock or wheel conditions remain debuggable

Checklist:

- [ ] keep `activity`, `state`, and raw mower-state semantics clearly separated
- [x] include a diagnostic `state_reconciliation` summary for source disagreements
- [x] avoid surfacing a sticky upstream error flag when code/text/name say no error
- [x] separate effective docked state from the raw vendor dock flag
- [ ] expose reliable charging, returning, and active-task indicators
- [ ] add friendly error text plus raw vendor error text where both exist
- [ ] document example Home Assistant automations for garage-door workflows
- [ ] add fixture-based tests for paused, returning, docked, charging, and fault transitions as captures become available

Definition of done:

- common automations can be built from documented entities without reverse-engineering attributes

## Phase 5: Auth, Session, and Update Hardening

Why this is later:

- it matters, but it is less valuable than basic compatibility and state quality
- auth problems are real, but they should not block us from improving the mower domain model first

Targets:

- make account setup more resilient
- improve diagnostics around login failures
- expose firmware-update support only when we can prove the correct signals exist

Checklist:

- [ ] improve error messages for bad region, account type, or incomplete auth flow
- [ ] add more auth diagnostics without leaking secrets
- [ ] verify whether OTA-available data exists for supported models
- [ ] add an `update` entity only after OTA availability is confirmed from real payloads

Definition of done:

- auth failures are diagnosable, and update support is data-backed rather than speculative

## Explicit non-goals for now

These are intentionally not early priorities:

- vacuum consumables and vacuum-era feature surface
- write-capable zone editing before read-only map parsing is stable
- broad entity expansion based only on assumptions from another repo
- camera-first work unless it is required by mower map rendering

## Working method

We should keep using this loop:

1. capture sanitized diagnostics from Home Assistant
2. turn the capture into a fixture in `tests/fixtures/`
3. add or adjust normalized snapshot fields
4. expose entities only when the fixture proves they exist
5. add regression tests before widening support

That workflow is slower than copy-paste porting, but it is the safest way to support more mower models without repeating the same mess we are trying to replace.
