# Dreame Lawn Mower for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://hacs.xyz/)

Cloud-backed Home Assistant integration for Dreame and MOVA robotic lawn mowers, with a reusable Python client bundled in the same repository.

## What is in this repo

- `dreame_lawn_mower_client`
  Reusable Python client facade for account login, device discovery, snapshot refresh, and core mower actions.
- `custom_components/dreame_lawn_mower`
  Home Assistant integration built on top of the reusable client.

## First shipped scope

This first implementation is intentionally narrow so it can be validated on real hardware quickly:

- account-based autodiscovery through Dreamehome or MOVAhome
- config flow and reauthentication
- one `lawn_mower` entity
- core sensors for battery, error, and firmware version
- start mowing, pause, and dock
- diagnostics, debug snapshot capture, and HACS-ready repo structure

Under the hood, this version reuses the already reverse-engineered mower protocol internals so we can test against real devices now while continuing to clean up the public architecture.

## Development

```bash
python -m pip install -e .[test]
python -m compileall dreame_lawn_mower_client custom_components tests examples
pytest
```

## Roadmap

The current phased backlog lives in [`docs/roadmap.md`](docs/roadmap.md). It focuses on:

- model compatibility hardening for additional mower brands and rebadges
- A2 and A2 Pro telemetry expansion from verified payloads
- mower-native vector map support
- automation-friendly state and error exposure

Note:

- the full Home Assistant pytest stack is usually easiest in Linux CI
- live validation against the real mower still matters because the protocol is reverse engineered

## Migration note

If you previously installed an older mower custom component under the `dreame_lawnmower` name, remove it before using this integration. Keeping both installed can lead to duplicate `Dreame Lawn Mower` entries during Home Assistant setup.

## Fixture workflow

When the mower is not available for live testing, use `Capture Debug Snapshot` and `Download diagnostics` in Home Assistant to collect sanitized payloads. Those captures can be turned into repo fixtures under `tests/fixtures/` and used to extend entity coverage, capability gating, and parser regressions without requiring a mowing run.

The repo already includes paused and paused-with-wheel-error A2 captures, which lets us regression-test awkward dock-contact states without reproducing them on demand.
