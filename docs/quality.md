# Integration quality qualification

Dreame Lawn Mower targets the [Home Assistant quality rules](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/)
through Platinum as a custom integration. This is a self-assessment, not an official
HA rating. Qualification is incomplete. The rule index checked on 2026-10-05
contains 54 cumulative rules.

Partial means relevant implementation or tests exist, without complete qualification.
Gap records known missing work. Review requires a bounded applicability or behavior
audit. Exemptions need a rule-permitted explanation; passing unit tests alone does
not establish installed, cloud, native-runtime, or physical-device behavior.

## Bronze

| Rule | State | Evidence or next acceptance step |
| --- | --- | --- |
| action-setup | Gap | Actions register after entry setup and unregister after the last unload; move integration-wide registration to async_setup and validate entry state. |
| appropriate-polling | Partial | Coordinator polling, background metadata and performance instrumentation exist; measure normal, unavailable, and recovery request budgets. |
| brands | Review | Audit the current supported path and document concrete evidence or a rule-permitted exemption. |
| common-modules | Partial | Bundled client owns protocol behavior; integration owners cover maps, streams, reporting, and entities. Keep modernization in these existing owners. |
| config-flow-test-coverage | Review | Component flow tests run against real HA fixtures; measure the complete flow module and close meaningful uncovered outcomes. |
| config-flow | Partial | UI cloud/device selection and reauthentication exist; qualify installed onboarding and supported account regions. |
| dependency-transparency | Partial | Manifest and Python requirements declare protocol/native dependencies; reconcile artifact contents, optional runtime downloads, and network behavior. |
| docs-actions | Partial | Mowing controls, maps, and video guides exist; reconcile parameters with services.yaml and validate supported examples. |
| docs-triggers | Review | Audit the current supported path and document concrete evidence or a rule-permitted exemption. |
| docs-conditions | Review | Audit the current supported path and document concrete evidence or a rule-permitted exemption. |
| docs-high-level-description | Partial | README describes Dreame/MOVA support; tie advertised capabilities to the supported-model evidence matrix. |
| docs-installation-instructions | Partial | HACS and manual installation are documented; qualify the actual released artifact and upgrade path. |
| docs-removal-instructions | Review | Audit the current supported path and document concrete evidence or a rule-permitted exemption. |
| entity-event-setup | Partial | Shutdown and failed-setup cleanup exist with component tests; audit every push listener, executor task, stream, and native resource. |
| entity-unique-id | Partial | Entity IDs derive from selected device identity; verify account repair and upgrades preserve registry bindings. |
| has-entity-name | Review | Audit naming across all platforms, map cameras, and primary mower entities. |
| runtime-data | Gap | Coordinator ownership remains in hass.data[DOMAIN][entry_id]; migrate typed entry runtime_data across platforms, APIs, services, and diagnostics. |
| test-before-configure | Partial | Cloud/device selection validates candidate identity; cover failed credentials, region errors, unavailable devices, and cancellation. |
| test-before-setup | Partial | Coordinator setup gates platform forwarding and cleans failures; verify authentication versus transient retry behavior. |
| unique-config-entry | Partial | Flow sets the descriptor unique ID; verify duplicate selection across accounts, regions, and repair. |

## Silver

| Rule | State | Evidence or next acceptance step |
| --- | --- | --- |
| action-exceptions | Review | Audit service/API exception translation, cancellation, timeout, and unsupported-device behavior at existing boundaries. |
| config-entry-unloading | Partial | Platform unload precedes coordinator shutdown and API cache purge; complete repeated reload, cancellation, and native-stream resource proof. |
| docs-configuration-parameters | Partial | Configuration guide exists; reconcile polling, map, camera, and cache options with current flow. |
| docs-installation-parameters | Partial | Account, region, and device selection guidance exists; verify the installed user flow. |
| entity-unavailable | Review | Audit mower, map, video, and optional-capability unavailable/recovery states without stale success claims. |
| integration-owner | Partial | Maintainers and issue tracker are declared; verify support and security-reporting instructions. |
| log-when-unavailable | Review | Capture disconnect/reconnect behavior and confirm useful non-repeating logs without private data. |
| parallel-updates | Gap | Platforms have no explicit PARALLEL_UPDATES declaration; choose limits that respect protocol serialization and command safety. |
| reauthentication-flow | Partial | Reauthentication step exists; qualify replacement credentials, failed retries, and preserved device identity. |
| test-coverage | Gap | Combined unit/component baseline covers 67% of 37,149 statements, including the bundled client. Complete meaningful coverage above 95% and full flow coverage. |

## Gold

| Rule | State | Evidence or next acceptance step |
| --- | --- | --- |
| devices | Partial | Device descriptors and supported-model documentation exist; verify registry metadata against tested mower and firmware variants. |
| diagnostics | Partial | Diagnostics use shared debug/reporting builders; audit downloaded payload privacy, nonmutation, and behavior when the cloud is unavailable. |
| discovery-update-info | Review | Determine applicability for cloud-selected device identities and address changes; document any rule-permitted exemption. |
| discovery | Review | Determine supported automatic discovery paths and rule applicability for cloud account onboarding. |
| docs-data-update | Partial | Configuration and development guides describe update behavior; publish measured polling, metadata, map, and stream timing boundaries. |
| docs-examples | Partial | User guides and client examples exist; qualify supported user workflows separately from safety-gated research probes. |
| docs-known-limitations | Partial | Supported-mower and video-transport guides record limits; reconcile them with release-scoped device evidence. |
| docs-supported-devices | Partial | Supported-mower guide exists; distinguish tested models/firmware from inferred protocol support. |
| docs-supported-functions | Partial | Entity, mowing-control, map, notification, and live-video guides exist; reconcile capability-dependent behavior. |
| docs-troubleshooting | Partial | Troubleshooting guide exists; verify authentication, offline, map, video, and diagnostic instructions. |
| docs-use-cases | Partial | Mowing, schedule, map, and notification workflows are documented; qualify complete supported paths. |
| dynamic-devices | Review | Determine applicability for one selected mower per entry and changes in map/area entities. |
| entity-category | Review | Audit diagnostic/configuration categorization across every platform. |
| entity-device-class | Review | Audit device classes, units, and state classes for sensors and control entities. |
| entity-disabled-by-default | Review | Audit optional, noisy, privacy-sensitive, and resource-heavy entities against documented defaults. |
| entity-translations | Partial | Strings and translations exist and translation tests pass; verify complete entity names and installed fallback behavior. |
| exception-translations | Review | Inventory user-facing errors and add applicable translation keys without hiding protocol diagnostics. |
| icon-translations | Review | Audit device-class icons and state-aware custom icon translation needs. |
| reconfiguration-flow | Gap | No async_step_reconfigure is present; determine supported account/region/device repair contract and implement the applicable flow. |
| repair-issues | Review | Audit recoverable authentication, native-runtime, device-permission, and configuration problems that need user intervention. |
| stale-devices | Review | Verify entry/device removal and stale map/area entity cleanup; removal already purges private caches. |

## Platinum

| Rule | State | Evidence or next acceptance step |
| --- | --- | --- |
| async-dependency | Gap | The cloud protocol owns synchronous requests, queues, locks, and threads. Modernize the owning client with real async cancellation and resource ownership; executor wrapping alone does not qualify. |
| inject-websession | Gap | Cloud HTTP uses its own synchronous session. Design injected async session ownership in the reusable client, including standalone callers. |
| strict-typing | Gap | After platform and map-model annotation fixes, mypy 2.4.0 reports 2,829 errors in 89 of 245 production modules. Fix the full integration and bundled client before enabling a passing strict CI gate. |

## Qualification baseline

At source revision 5bd1e96 (version 0.2.120), on HA 2026.9.4/Python 3.14.5:

- 2,547 unit tests pass, with one skipped; 74 component/translation tests pass.
- Combined coverage is 67%, including the bundled protocol client.
- Strict typing checks 244 production modules and reports 3,581 errors.
- These results establish a source baseline, not a Platinum claim.

Platform metadata and update annotations use HA's declared types and canonical
imports. Map renderer models describe RGBA tuples and optional resources;
geometry and map-state annotations match the values produced by the decoder.
Map values follow Python's equality protocol for unrelated objects, and obstacle
object names use their vendor ID when available. Regression tests cover both
contracts. Strict typing remains incomplete at 2,829 errors in 89 modules;
`map_renderer_types.py` passes strict checking on its own. Renderer payload
annotations match the nested crop data, hidden-segment list, and per-segment
material/status dictionaries; 32 focused map tests pass after these declaration
corrections. This does not establish full renderer typing. Domain-model annotations also
express supported-model narrowing, optional timestamps, and tuple/list segment
sequences; 194 focused model and runtime tests pass. Full strict checking reports
no errors in `models.py`, while the remaining client and integration errors stay open.
Device settings and capability declarations reflect their existing optional defaults;
property-ID helpers accept typed mappings. These annotations also expose callers
that still need to express when optional fields are populated. Existing validators now
provide type narrowing for provisioning sequences, CMS counters, maintenance-point
records, and preference-cache payloads. Those four modules have no errors in the
full strict run, while their accepted payload shapes remain unchanged. A private
shared declaration base gives the state, command, and map mixins their concrete
status, capability, protocol, and property/action mapping types. The assembled
device retains initialization and mapping values; the base supplies no behavior.
Shared state declarations also cover existing identity, lifecycle flags, timestamps,
and optional timers. Startup cloud privacy readback updates the status flag used
by AI commands; regression tests cover both vendor keys and explicit rejection.

After these changes, 2,565 unit tests pass with one skipped. Component/translation
tests pass on HA 2025.1 (73 passed, one version-specific skip) and HA 2026.9.4
(74 passed). No physical-device or frontend-rendering claim follows from these
local tests.

## Release qualification

- [ ] Complete every applicable rule with evidence or a justified exemption.
- [ ] Modernize async protocol/session ownership without duplicating the client.
- [ ] Complete strict typing and meaningful coverage across the bundled client.
- [ ] Verify supported minimum/current HA versions and native runtime architectures.
- [ ] Install and upgrade published HACS artifacts while retaining configuration.
- [ ] Qualify actual HA UI, downloaded diagnostics, maps, and supported streams.
- [ ] Record model, firmware, account region, artifact identity, and evidence date.
- [ ] Run physical movement or settings proof only with explicit device authorization.

See [development](development.md) for repository ownership and test commands.
