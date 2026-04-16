# Implementation Plan

## What I found

After reviewing `Tasshack/dreame-vacuum` and `bhuebschen/dreame-mower`, the mower repo looks like a large vacuum-port, not a mower-native integration.

Key problems in the current mower approach:

- It keeps the same broad file layout and complexity as the vacuum integration.
- It exposes a very large service surface that still includes vacuum-era concepts such as room/segment cleaning, cleaning sequences, mop/no-mop zones, fan speed names, brush/filter consumables, and other behavior that does not belong in a mower-first design.
- It carries over heavy map logic from the vacuum project, even though mower data appears to be different in shape and use.
- It mixes protocol discovery, device logic, Home Assistant entity logic, map handling, and feature experiments in the same code paths.

At the same time, the mower repo contains useful reverse-engineering work that should not be thrown away:

- mower model detection groundwork
- Dreamehome and Mova account support
- raw command discoveries in `todo.txt`
- evidence that mower maps and schedules are available from the cloud

## Most important insight

The mower data model should not be built as a direct continuation of the vacuum model.

The reverse-engineered notes strongly suggest mower-specific structures such as:

- polygon-based mowing areas and contours
- schedule payloads
- mower profile/config payloads such as cut height, obstacle sensitivity, edge mowing, and mode selection
- mower-specific protection features such as child lock, frost protection, and lift/theft behavior

That means the correct approach is:

- reuse the upstream repos as references
- do not reuse their architecture as the product design

## Recommended architecture

Build this as a small set of layers with clear boundaries.

### 1. Protocol layer

Create a mower-focused protocol package inside the integration, for example:

- `custom_components/dreame_lawn_mower/dreame/auth.py`
- `custom_components/dreame_lawn_mower/dreame/cloud.py`
- `custom_components/dreame_lawn_mower/dreame/local.py`
- `custom_components/dreame_lawn_mower/dreame/models.py`
- `custom_components/dreame_lawn_mower/dreame/capabilities.py`
- `custom_components/dreame_lawn_mower/dreame/commands.py`
- `custom_components/dreame_lawn_mower/dreame/device.py`

Responsibilities:

- login and token handling
- fetch device list
- fetch one normalized device snapshot
- send mower commands
- detect capabilities from model and supported payloads
- parse schedule, profile, map summary, and protection settings

This layer should return typed Python objects, not Home Assistant entities or raw mixed dicts.

### 2. Domain model

Normalize raw data into mower-native objects, for example:

- `MowerSnapshot`
- `MowerState`
- `MowerCapabilities`
- `MowerSchedule`
- `MowerMapSummary`
- `MowerProfile`
- `MowerProtectionSettings`
- `MowerConsumables`

This is where you convert raw enums and payload fragments into meaningful mower concepts.

### 3. Home Assistant layer

Keep the HA side thin:

- `config_flow.py`
- `coordinator.py`
- `lawn_mower.py`
- `sensor.py`
- `binary_sensor.py`
- `switch.py`
- `select.py`
- `number.py`
- `button.py`
- `diagnostics.py`

The coordinator should fetch one current snapshot and fan it out to entities.

Do not let entities perform protocol logic directly.

### 4. Advanced feature modules

Split advanced features so they can evolve safely:

- `map.py` for mower map parsing only
- `schedule.py` for read/write schedule operations
- `actions.py` for non-core commands

This keeps the MVP simple while still giving you a place for richer functionality later.

## What to build first

Start with a clean MVP that proves the protocol and user experience.

### Phase 1: Discovery and fixtures

Use your A2 Pro as the reference device and capture:

- model string
- supported properties/actions
- current state payloads
- schedule payloads
- map/info payloads
- profile/config payloads

Store sanitized fixtures under `tests/fixtures/`.

This step is critical because it lets you write parsers against real mower data instead of inheriting vacuum assumptions.

### Phase 2: MVP integration

Ship only the essentials:

- config flow with Dreamehome and Mova login
- device setup and reauth
- one `lawn_mower` entity
- battery, error, and connectivity sensors
- start mowing
- pause
- dock/return
- core state mapping: `MOWING`, `DOCKED`, `PAUSED`, `RETURNING`, `ERROR`

This gets you a stable, useful integration quickly.

### Phase 3: Real mower features

Add features that are actually mower-specific and valuable:

- schedule read/write
- cut height
- mowing mode/profile
- edge mowing
- obstacle sensitivity / AI obstacle behavior
- child lock
- frost protection
- lift/theft protection
- voice volume / language if stable
- consumable or blade life if the payloads are trustworthy

### Phase 4: Map features

Only after the protocol is stable:

- mower map summary entity or camera
- mower area polygons
- forbidden zones
- docking station position
- manual refresh / fetch info
- backup/restore if reliable

Do not let map rendering block the MVP.

## What not to copy from the vacuum integration

Avoid carrying these assumptions into the new repo:

- vacuum-specific domain abstractions
- oversized entity matrix created before capabilities are proven
- room cleaning semantics unless they are confirmed to map cleanly to mower areas
- mop/no-mop language
- vacuum consumables and maintenance concepts unless confirmed on mower payloads
- large monolithic device and map files

## Entity strategy

Be conservative and capability-driven.

Recommended entity set:

- `lawn_mower`: main control entity
- `sensor`: battery, error, firmware, last task, current schedule name
- `binary_sensor`: online, child lock, frost protection enabled, lift alarm enabled
- `switch`: child lock, frost protection, edge mowing, lift/theft protection
- `select`: mowing mode, language, schedule selection if exposed that way
- `number`: cut height, volume, obstacle sensitivity if numeric
- `button`: locate mower, refresh map info, refresh schedules

If a feature is not available on a model, do not create the entity.

## A2 Pro specific recommendation

The current mower port maps:

- `dreame.mower.g2408` to `A2`
- `dreame.mower.g3255` to `unknown`

One of the first real tasks should be confirming what your A2 Pro reports and then fixing model mapping and capabilities around it.

Do not hardcode behavior by marketing name alone. Prefer:

- model id
- supported actions
- payload capability flags

## Home Assistant quality target

Aim for a structure that can realistically meet modern Home Assistant expectations:

- coordinator-based data updates
- UI config flow
- reauthentication support
- diagnostics
- strict capability gating
- tests with sanitized fixtures

That will make the project much easier to maintain and much easier to upstream later if you ever want that.

Relevant official docs:

- https://developers.home-assistant.io/docs/core/entity/lawn-mower/
- https://developers.home-assistant.io/docs/integration_fetching_data
- https://developers.home-assistant.io/docs/core/integration-quality-scale/

## Suggested file layout

```text
custom_components/
  dreame_lawn_mower/
    __init__.py
    manifest.json
    const.py
    config_flow.py
    coordinator.py
    diagnostics.py
    lawn_mower.py
    sensor.py
    binary_sensor.py
    switch.py
    select.py
    number.py
    button.py
    services.yaml
    strings.json
    translations/
    dreame/
      __init__.py
      auth.py
      cloud.py
      local.py
      commands.py
      models.py
      capabilities.py
      parsers.py
      device.py
      exceptions.py
      schedule.py
      map.py
```

## Concrete implementation order

1. Create the new integration skeleton under a new domain, preferably `dreame_lawn_mower`.
2. Implement login and device discovery only.
3. Add normalized `MowerSnapshot` parsing from your A2 Pro fixtures.
4. Add coordinator plus one `lawn_mower` entity.
5. Add start, pause, dock, and correct state mapping.
6. Add diagnostics and reauth.
7. Add mower-specific sensors and switches.
8. Add schedule support.
9. Add map support.
10. Add broader model capability tables once more mower payloads are known.

## Bottom line

The best path is a clean mower-native rewrite that borrows protocol knowledge from both upstream repos but keeps very little of their public surface and internal architecture.

If you want, the next step should be building Phase 1 and Phase 2 in this repo:

- scaffold the integration
- define the normalized mower models
- wire up config flow and coordinator
- get your A2 Pro online with start/pause/dock and clean state reporting
