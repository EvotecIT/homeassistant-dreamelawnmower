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
- diagnostics and HACS-ready repo structure

Under the hood, this version reuses the already reverse-engineered mower protocol internals so we can test against real devices now while continuing to clean up the public architecture.

## Development

```bash
python -m pip install -e .[test]
python -m compileall dreame_lawn_mower_client custom_components tests examples
pytest
```

Note:

- the full Home Assistant pytest stack is usually easiest in Linux CI
- live validation against the real mower still matters because the upstream protocol is reverse engineered

## Architecture notes

The longer-term direction is documented in [docs/implementation-plan.md](docs/implementation-plan.md).

The most important design decision is that this should be mower-first, not a vacuum integration fork with renamed entities.
