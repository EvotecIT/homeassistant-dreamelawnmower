# Integration quality qualification

Dreame Lawn Mower targets the [Home Assistant quality rules](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/)
through Platinum as a custom integration. This is a self-assessment, not an official
HA rating. Qualification is incomplete. The rule index checked on 2026-10-09
contains 54 cumulative rules.

Source verified means the stated contract has direct source and synthetic HA
proof. It does not certify a released artifact, installed system or physical mower.
Partial means relevant implementation or tests exist, without complete qualification.
Gap records known missing work. Review requires a bounded applicability or behavior
audit. Exemptions need a rule-permitted explanation; passing unit tests alone does
not establish installed, cloud, native-runtime, or physical-device behavior.

## Bronze

| Rule | State | Evidence or next acceptance step |
| --- | --- | --- |
| action-setup | Source verified | Integration-wide actions register in async_setup and persist across entry unload. Real HA setup/registry tests cover discovery without an entry, target errors and last-entry retirement. See services.py and component test_actions.py/test_entry_runtime.py. |
| appropriate-polling | Partial | Coordinator polling, background metadata and performance instrumentation exist; measure normal, unavailable, and recovery request budgets. |
| brands | Partial | Six bundled PNGs meet the icon/logo geometry requirements. Actual HA 2026.9.4 loader and authenticated brand API return their exact bytes; component test_brand_assets.py protects that path on HA 2026.3+. Earlier supported HA uses the legacy CDN, whose Dreame icon/logo endpoints are currently missing. No branding exemption is claimed. |
| common-modules | Partial | Bundled client owns protocol behavior; integration owners cover maps, streams, reporting, and entities. Keep modernization in these existing owners. |
| config-flow-test-coverage | Source verified | The full config_flow.py module has 121/121 covered statements and 36/36 covered branches, with no excluded lines, on HA 2025.1.0 and 2026.9.4. Real HA flow/store tests cover options, credential retry, reauthentication, reconfiguration and preserved device identity. See component test_config_flow.py/test_reconfiguration.py and test_config_flow_auth.py. |
| config-flow | Partial | UI cloud/device selection, reauthentication and connection reconfiguration exist; qualify installed onboarding and supported account regions. |
| dependency-transparency | Partial | Manifest and Python requirements declare protocol/native dependencies; reconcile artifact contents, optional runtime downloads, and network behavior. |
| docs-actions | Partial | Mowing controls, maps, and video guides exist; reconcile parameters with services.yaml and validate supported examples. |
| docs-triggers | Source verified | No integration-specific trigger types are registered. Standard entity-state automation and person-detection occurrence guidance, editor setup and YAML examples are documented in notifications.md. |
| docs-conditions | Exempt | No integration-specific conditions are registered. Automations use HA's standard state/template conditions with mower entities; see notifications.md and the [rule-permitted exception](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-conditions/). |
| docs-high-level-description | Partial | README describes Dreame/MOVA support; tie advertised capabilities to the supported-model evidence matrix. |
| docs-installation-instructions | Partial | HACS and manual installation are documented; qualify the actual released artifact and upgrade path. |
| docs-removal-instructions | Source verified | README Removal describes entry deletion, HACS/manual uninstall, private cache removal and retained shared runtime files. The removal hook and test_observation_storage.py cover private storage retirement. Deleting an entry does not unpair the mower, alter onboard schedules or send a stop command; installed removal proof remains separate. |
| entity-event-setup | Partial | Shutdown and failed-setup cleanup exist with component tests; audit every push listener, executor task, stream, and native resource. |
| entity-unique-id | Partial | Entity IDs derive from selected device identity; verify account repair and upgrades preserve registry bindings. |
| has-entity-name | Review | Audit naming across all platforms, map cameras, and primary mower entities. |
| runtime-data | Source verified | Typed ConfigEntry.runtime_data owns each coordinator. Registry tests cover current ownership, failed setup, failed unload, stale-owner replacement and per-entry API retirement. Integration-global action/API registration stays in hass.data. |
| test-before-configure | Partial | Cloud/device selection validates candidate identity; cover failed credentials, region errors, unavailable devices, and cancellation. |
| test-before-setup | Partial | Coordinator setup gates platform forwarding and cleans failures; verify authentication versus transient retry behavior. |
| unique-config-entry | Partial | Flow sets the descriptor unique ID; verify duplicate selection across accounts, regions, and repair. |

## Silver

| Rule | State | Evidence or next acceptance step |
| --- | --- | --- |
| action-exceptions | Partial | All seven domain actions share loaded-entry validation with translated ServiceValidationError. Existing movement/write guards remain. Complete the audit of entity actions, device errors, timeouts and unsupported capabilities. |
| config-entry-unloading | Partial | Platform unload precedes coordinator shutdown and API cache purge; complete repeated reload, cancellation, and native-stream resource proof. |
| docs-configuration-parameters | Partial | Configuration guide exists; reconcile polling, map, camera, and cache options with current flow. |
| docs-installation-parameters | Partial | Account, region, and device selection guidance exists; verify the installed user flow. |
| entity-unavailable | Review | Audit mower, map, video, and optional-capability unavailable/recovery states without stale success claims. |
| integration-owner | Partial | Maintainers and issue tracker are declared; verify support and security-reporting instructions. |
| log-when-unavailable | Review | Capture disconnect/reconnect behavior and confirm useful non-repeating logs without private data. |
| parallel-updates | Source verified | All twelve platforms declare limits. Read-only and interruption-capable adapters use 0; number, select, switch, time and update use 1. Component test_platform_parallel_updates.py covers a charging-window service batch and the firmware HA request owner on minimum/current HA. HA 2025.1 single-target service calls bypass its platform semaphore; these declarations do not establish global client serialization or physical request budgets. |
| reauthentication-flow | Partial | Real HA flow tests cover credential failures, retries, matching-device selection and retained entry identity. Tests with the registered update listener verify one reload for changed or unchanged credentials. Installed account recovery and supported-region proof remain. |
| test-coverage | Gap | The last whole-source measurement covers 81.69% of statements and 64.69% of branches, including the bundled client. It predates the latest action/config-flow changes. Full flow coverage is verified; whole-source coverage still needs meaningful improvement above 95%. |

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
| exception-translations | Partial | Four shared action-target errors have matching messages/placeholders in strings.json and all nine shipped locales, loaded through HA translation tests. Audit the remaining user-facing errors across platforms and APIs. |
| icon-translations | Review | Audit device-class icons and state-aware custom icon translation needs. |
| reconfiguration-flow | Source verified | Reconfigure validates updated credentials/region against the saved mower through the shared HA session before persisting. The registered update listener owns reloads when data changes; unchanged credentials and entries without a listener schedule one explicit reload. Actual HA flow and registry tests cover Dreame/MOVA accounts, unchanged entry/device/entity identity and options, no password prefill, failed discovery, retry, identity mismatch and cancellation. Account type stays fixed; a different mower uses a new entry. Installed UI and cloud/region qualification remain separate. See test_reconfiguration.py and the configuration guide. |
| repair-issues | Review | Audit recoverable authentication, native-runtime, device-permission, and configuration problems that need user intervention. |
| stale-devices | Review | Verify entry/device removal and stale map/area entity cleanup; removal already purges private caches. |

## Platinum

| Rule | State | Evidence or next acceptance step |
| --- | --- | --- |
| async-dependency | Partial | Cloud HTTP uses awaiting aiohttp requests; callback, LAN and video adapters own asynchronous operations and worker deadlines. Complete supported native-runtime/model cancellation and resource proof. See cloud_session.py and async adapter tests. |
| inject-websession | Source verified | The reusable client accepts an aiohttp.ClientSession; the HA coordinator supplies async_get_clientsession(hass). Client/session tests cover borrowed-session lifetime. Standalone owned sessions close within their explicit scope. |
| strict-typing | Source verified | python -m mypy --strict custom_components/dreame_lawn_mower passes all 336 production modules, including the bundled client, on HA 2025.1.0 and 2026.9.4. The maintained CI gate uses mypy 2.4.0; documented third-party typing exceptions remain narrow. |

## Reproducible source evidence

The 2026-10-09 connection-repair qualification uses the existing minimum and
current HA environments. Standalone tests and component tests are separate
commands, so credential-flow helper tests run once in the component lane.

| Environment | HA component, translation and flow tests | Strict typing |
| --- | --- | --- |
| HA 2025.1.0 / Python 3.13.14 | 135 passed; one version-specific skip | 336 modules; no errors |
| HA 2026.9.4 / Python 3.14.5 | 136 passed | 336 modules; no errors |

Configured Ruff checks pass in both environments. Test skips remain explicit;
they do not supply evidence for an unsupported version-dependent feature.

Run the owning checks from the repository root with development requirements
installed in an isolated environment. Set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
for the standalone command only, as in the maintained CI workflow; allow
the HA test plugin to load for the component command:

```sh
python -m pytest tests --ignore=tests/components --ignore=tests/test_translations.py --ignore=tests/test_config_flow_auth.py
python -m pytest tests/components tests/test_translations.py tests/test_config_flow_auth.py
python -m mypy --strict custom_components/dreame_lawn_mower
python -m ruff check .
```

The action setup test exercises HA integration setup and its service registry,
with HTTP/stream dependency processing stubbed at that unrelated boundary.
Native decoding and complete installed setup need their own proof. The
configuration-flow coverage measurement is scoped to config_flow.py, rather
than inferred from total test counts. It runs setup/options tests, connection
repair tests and credential-error helper tests together. HA's translation loader
checks the new form and abort messages in all nine shipped locales. These local
checks do not certify rendered forms or successful account repair on an installed
system.

The earlier whole-source coverage measurement covers all 336 production
modules and includes the protocol client: 35,064 of 42,925 statements and
9,891 of 15,290 branches. Existing exclusions number 644 lines; these changes
add no coverage exclusions. Refresh that whole-source measurement after the
remaining meaningful coverage work. A passing typing gate or one fully covered
module does not satisfy the whole-integration coverage requirement.

## Remaining qualification

- [ ] Resolve every Partial, Gap and Review row with evidence or a rule-permitted exemption.
- [ ] Complete whole-integration coverage above 95%, including the reusable client and consequential failure paths.
- [ ] Qualify released HACS/manual artifacts, isolated installation and upgrade from the previous stable version.
- [ ] Verify installed setup, options, reauthentication, reconfiguration, removal and repeated reload while preserving device/entity identity.
- [ ] Establish supported model, firmware, region, OS and architecture evidence for cloud, LAN and native video paths.
- [ ] Measure unavailable/recovery logs, polling requests, cancellation, task and worker retirement, and normal runtime budgets.
- [ ] Complete applicable metadata, translated errors, repairs, reconfiguration, discovery and documentation audits.

Physical mower actions and deployment use separate, explicitly authorized
validation. Synthetic HA fixtures, source tests, released artifacts and installed
device evidence retain distinct meanings throughout this qualification.
