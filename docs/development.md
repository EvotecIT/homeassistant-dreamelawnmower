# Development Notes

This repository is both a Home Assistant custom integration and the current
home of the reusable Dreame/MOVA mower protocol client. Keep the public surface
small and the reverse-engineering notes in docs or ignored local captures.

## Repository Layout

- `custom_components/dreame_lawn_mower`
  Home Assistant integration code.
- `custom_components/dreame_lawn_mower/dreame_lawn_mower_client`
  Bundled mower client implementation. Protocol changes, payload decoders,
  mappers, and cloud/app command helpers live here.
- `dreame_lawn_mower_client`
  Public Python package name. Examples, tests, and external scripts import
  this package directly.
- `examples`
  Read-only and safety-gated probe scripts. Output files are ignored by git.
- `tests`
  Unit and fixture tests for the public package, Home Assistant entities,
  protocol helpers, and live-probe summarizers.
- `docs`
  Roadmap, handoff notes, and protocol research.

## Client Package Layout

This repo follows the same shape as the sister Home Assistant repositories:
one reusable client package name plus a Home Assistant custom component that
uses that client.

Public imports use:

```text
dreame_lawn_mower_client
```

HACS still needs the implementation bundled inside the custom component, so the
implementation currently lives here:

```text
custom_components/dreame_lawn_mower/dreame_lawn_mower_client
```

The top-level package loads the bundled implementation without importing Home
Assistant. That keeps examples and tests clean, gives users a reusable Python
library surface today, and preserves the option to extract or publish the
client separately later.

Rules:

- add new protocol behavior to `custom_components/.../dreame_lawn_mower_client`
- expose stable public imports through `dreame_lawn_mower_client`
- keep Home Assistant-specific entity, service, and config-flow behavior outside
  the client implementation
- avoid adding a second protocol implementation under the top-level package

## Local Setup

```bash
python -m pip install -e .[test]
```

Run the main local checks:

```bash
python -m compileall dreame_lawn_mower_client custom_components tests examples
pytest
```

The Home Assistant config-flow tests may require a fuller HA test environment.
When using the lighter local setup, the common broad suite is:

```bash
pytest tests --ignore=tests/components/dreame_lawn_mower/test_config_flow.py
```

## Live Probe Safety

Most scripts in `examples` are read-only by default. Scripts that can move the
mower or write settings require explicit execution and confirmation flags.

General rules:

- do not run live movement tests unsupervised
- do not run write probes from automations
- do not commit live output files
- do not store credentials in repo files
- use environment variables for credentials
- prefer short, read-only probes before adding new Home Assistant entities

Useful read-only probes:

```bash
python examples/cloud_probe.py
python examples/app_map_probe.py --out app-map-current.json
python examples/schedule_probe.py --out schedule-probe-current.json
python examples/weather_probe.py --out weather-probe-current.json
python examples/preference_probe.py --out preference-probe-current.json
python examples/task_status_probe.py --samples 6 --interval 10 --out task-status-live.json
```

Safety-gated examples:

```bash
python examples/schedule_write_probe.py --map-index 0 --plan-id 0 --disable --out schedule-write-dry-run.json
python examples/schedule_write_probe.py --map-index 0 --plan-id 0 --disable --execute --confirm-schedule-write
python examples/remote_control_smoke.py --execute --velocity 30 --rotation 25 --duration 0.35 --dock
```

## Ignored Local Files

The `.gitignore` excludes local probe outputs such as:

- `app-map*.json`
- `dreame-map*.png`
- `field-trip*.json`
- `map-sources*.json`
- `preference-probe*.json`
- `property-scan*.json`
- `remote-control*.json`
- `schedule-probe*.json`
- `schedule-write*.json`
- `task-status*.json`
- `weather-probe*.json`

Keep raw payloads there while investigating. Convert only sanitized and useful
captures into fixtures.

## Public README Standard

The README should stay user-facing:

- what the integration does
- status and tested models
- installation
- primary entities and services
- troubleshooting and diagnostics
- links to deeper docs
- a concise reusable Python package section

Keep long probe recipes, protocol notes, app decompilation notes, and live test
history in `docs/agent-handoff.md`, `docs/dreamehome-research.md`, or focused
docs like this one.

## Before Committing

Run at least:

```bash
pytest tests/test_python_package.py tests/test_ha_compatibility.py
pytest tests --ignore=tests/components/dreame_lawn_mower/test_config_flow.py
```

For changes touching protocol helpers, add focused tests around the exact
payload shape before relying on live hardware behavior.
