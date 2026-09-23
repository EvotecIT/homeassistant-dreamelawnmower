# Supported mowers

[Back to the README](../README.md)

The integration follows the Dreamehome and MOVAhome mower protocol. Support is
based on real mower reports and repository fixtures, not only on a matching
brand name:

- **Validated** means the model has been exercised against real hardware and
  captured fixtures.
- **Field-reported** means owners have confirmed important paths, but the full
  feature set has not been validated yet.
- **Recognized** means the model identity and protocol family are known; treat
  unconfirmed features as experimental.

| Brand and model | Raw model identifier | Support | Confirmed coverage |
| --- | --- | --- | --- |
| Dreame A2 | `dreame.mower.g2408` | **Validated** | Primary development mower; core control, schedules, maps, remote control, guarded preferences, diagnostics, video, and 3D maps |
| Dreame A2 3000 | `dreame.mower.g2568d` | **Field-reported** | Home Assistant discovery, core state entities, and heartbeat docking state on firmware `4.3.6_0625`; broader controls and media still need live validation |
| Dreame A3 AWD 1000 | `dreame.mower.q2501a` | **Validated** | Core entities, mower state, maps, and cloud live video on firmware `4.3.6_0418` with an EU account |
| MOVA 600 | `mova.mower.g2405a` | **Recognized** | Model identity and shared mower state/device-code semantics; [MOVA documents dual maps, multi-zone mowing, and manual 2–6 cm cutting-height adjustment](https://www.mova.tech/products/mova-600-robot-lawn-mower); controls, maps, and media still need live validation |
| MOVA 600 Kit | `mova.mower.g2405b` | **Recognized** | Model identity and shared mower state/device-code semantics; uses the MOVA 600 manual-height control policy, while controls, maps, and media still need live validation |
| MOVA 1000 | `mova.mower.g2405c` | **Recognized** | Model identity, station-brush maintenance code, and mowing-start lifecycle semantics; [MOVA documents dual maps, multi-zone mowing, and manual 2–6 cm cutting-height adjustment](https://www.mova.tech/products/mova-600-robot-lawn-mower); login, controls, maps, and media still need live validation |
| MOVA LiDAX Ultra 800 | `mova.mower.g2529b` | **Field-reported** | MOVAhome EU login, core state and controls, maps, map selection, and mowing preferences on firmware `4.3.6_0453`; [the LiDAX Ultra family supports app-controlled 3–10 cm cutting height](https://www.mova.tech/products/lidax-ultra-series-robotic-lawn-mower); app-map connector corridors are distinguished from mowing zones |
| MOVA LiDAX Ultra 1000 | `mova.mower.g2529c` | **Field-reported** | MOVAhome EU login, commands, battery, and model-specific cloud property handling |
| MOVA LiDAX Ultra 2000 | `mova.mower.g2529f` | **Field-reported** | MOVAhome US login, core state and commands, maps, and cloud live video on firmware `4.3.6_0453` |
| MOVA LiDAX Ultra 2000 AWD | `mova.mower.g2584a` | **Field-reported** | MOVAhome US login, realtime state, zone mowing, docking, maps, and cloud live video on firmware `4.3.6_0439` |
| MOVA VIAX 250 | `mova.mower.g2552`, `mova.mower.g2420a` | **Recognized** | Model identity and shared mower device-code semantics; dual-map and multi-zone capabilities are [documented by MOVA](https://www.mova.tech/products/viax-250-smart-robot-mower), while cutting height is manual-only; login, controls, maps, and media still need owner validation |
| MOVA VIAX 300 | `mova.mower.g2420b` (community-reported) | **Field-reported** | Owner report on firmware `4.3.6_0482`: zero is reported for the mowing-preference height field. [MOVA documents manual 2–6 cm adjustment](https://pl.mova.tech/products/viax300-robot-lawn-mower). Other preference writes need owner validation after the height fix. |
| MOVA VIAX 500 | `mova.mower.g2583` | **Field-reported** | Integration state and app-map metadata on firmware `4.3.6_0260`; [MOVA documents manual 2–6 cm height, dual maps, LiDAR-assisted 3D mapping, and TrueGuard video](https://www.mova.tech/products/robot-lawn-mower-viax-500), but account video provisioning and [3D point-cloud download remain separate and unconfirmed](viax-point-cloud.md) |
| Dreame A3 AWD Pro 3500 | `dreame.mower.g2541e` | **Recognized** | Model mapping and diagnostics report; broader live confirmation is still needed |
| Dreame A1 | `dreame.mower.p2255` | **Field-reported** | EU account setup, core state, battery, error state, map camera, docking state, and a real docked-to-mowing start are confirmed; live video is explicitly unsupported |
| Dreame A1 Pro | `dreame.mower.g2422` | **Recognized** | Model mapping and mower-specific state semantics; needs fixtures and live validation |
| Dreame A1 Pro 2000 | `dreame.mower.g2540d` | **Field-reported** | EU account setup, core state, maps, and app map selection on firmware `4.3.6_0623`; SETTINGS slot alignment has regression coverage, while write controls and model-specific device codes still need supervised live validation |
| Newer A-series mower | `dreame.mower.g3255` | **Recognized** | Raw identifier observed; retail name and feature coverage are not yet confirmed |

MOVAhome account login is supported. Other MOVA-branded mowers, rebadges,
regional variants, and firmware revisions may be discovered, but should be
treated as experimental until their behavior is reported. A model being listed
does not guarantee that every vendor- or region-gated feature—especially live
video—will be available on every account.

If your mower is not fully validated, see [Help expand support](#help-expand-support)
for the small, sanitized report that helps turn recognition into confirmed
support.

## Current Limits

The integration deliberately keeps uncertain or potentially disruptive
operations behind clear boundaries:

- Model, firmware, region, and account provisioning can change which advanced
  features the vendor makes available.
- Live video is field-validated on the Dreame A2 and A3 AWD 1000. It currently
  requires a Linux x86_64 or aarch64 Home Assistant host, a provisioned account,
  and a mower state in which the vendor permits video.
- 3D point-cloud generation and download are validated on the Dreame A2; other
  mower families still need reports.
- Map rendering and map selection are supported, but editing no-go areas,
  virtual walls, and garden geometry is not.
- Charging-period, rain-protection, and three-field anti-theft controls are
  field-validated on the Dreame A2. Other models expose each group only when
  their native `CFG` record reports the matching setting. PIN check before
  power-off appears only on mowers that report the fourth anti-theft field.
- Mowing-preference writes use guarded paths and have the strongest live proof
  on the A2. Other models and firmware need broader confirmation.
- Height controls follow the known hardware families: MOVA 600/1000 and VIAX
  250/300/500 use a physical 2–6 cm knob and have no electronic height number
  entities; LiDAX Ultra and
  supported AWD families use electronic 3–10 cm adjustment. Unknown models keep
  the conservative 3–7 cm range until their identity is confirmed.
- Firmware updates use the app-approved target and confirmation flow. Release
  notes remain best-effort, and debug catalog candidates stay diagnostic rather
  than being presented as approved updates.
- Manual driving remains a supervised diagnostic action with strict mower-state
  and battery guards.

## Help Expand Support

Support across Dreame, MOVA, and rebadged mower variants will improve fastest
with real-world reports. If your mower is recognized but not yet validated, or
if it exposes a different raw model string than this README shows, please open a
GitHub issue or PR with:

- the retail product name and raw app/cloud model identifier
- account type (`dreame` or `mova`) and region
- a sanitized diagnostics capture or Home Assistant debug snapshot
- screenshots of the product page, app model name, or device information page
- notes about what works, what is missing, and any errors you see

Please redact credentials, tokens, serial numbers, exact coordinates, and any
other secrets before attaching files.
