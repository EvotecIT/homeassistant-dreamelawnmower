# Dreame model families and device-code catalogs

[Supported mowers](supported-mowers.md) records hardware and field validation.
This reference records app-derived family identity and the device-code differences
used by the client. Recognizing a family does not confirm every feature on every
variant or firmware.

## Family identity

The app selects a profile using the first five characters of the model component
in `dreame.mower.<model>`. The remaining characters identify variants. The client
uses the same family boundary, accepting alphanumeric suffixes without assuming
there is only one A2 or one A3. Exact field-backed retail names take precedence;
an explicit cloud product name takes precedence over the generic family name.

| Raw model family | App family name | Observed variant identifiers with valid EU plugin metadata |
| --- | --- | --- |
| `p2255` | A1 | `p2255` |
| `g2422` | A1 Pro | `g2422` |
| `g2540` | A1 Pro | `g2540a`, `g2540b`, `g2540c`, `g2540d` |
| `g2408` | A2 | `g2408` |
| `g2568` | A2 | `g2568a`, `g2568c`, `g2568d`, `g2568e` |
| `q2501` | A3 | `q2501a`, `q2501b`, `q2501c` |
| `g2541` | A3 Pro | `g2541c`, `g2541e`, `g2541g` |

These are observed identifiers, not a suffix allowlist or a claim that the vendor
has no other models. Bare `g2540`, `g2568`, `q2501` and `g2541` returned plugin
version `-1`; the suffixed IDs returned usable metadata. `g2568b` occurs in the
app's configuration but also returned `-1`, so a configuration entry alone does
not prove a released product.

The six retrieved builds contain eleven profiles, including `g2420`, `g2529`,
`g2538` and `g2566`. The app's Dreame naming map does not assign those four to
A1/A2/A3 families. Existing MOVA identities remain separate; the client does not
turn an unnamed profile into an invented Dreame product.

The app's suffix-dependent `maxArea` values configure its UI. They are not a
reliable source for retail names or rated lawn coverage. Product variants can
also differ by region. For commercial naming, consult the vendor's
[European mower catalog](https://de.dreametech.com/collections/roboticmower) and
[US mower catalog](https://yardcare.dreametech.com/collections/robot-lawn-mowers),
then confirm the raw ID through cloud/device metadata.

## Code selection and compatibility

The app selects each family's current fault table, then looks up a code in this
order: IoT, heartbeat, legacy fallback. `g2540` shares `g2422`'s fault-table
selection. The A3 and A3 Pro tables differ even when delivered in the same bundle.

| Code | A2 (`g2408`, `g2568`) | A3 (`q2501`) | A3 Pro (`g2541`) |
| --- | --- | --- | --- |
| 19 | Location lost | Left front drive wheel error | Left front drive wheel error |
| 25 | Startup or map data error; version-dependent | Startup or map data error; version-dependent | Startup or map data error; version-dependent |
| 39 | Camera dirty | Edge blades worn | Camera dirty |
| 45 | Automatic boundary side detection stopped | Right front drive wheel error | Right front drive wheel error |
| 46 | Boundary detection completed | Boundary detection completed | Right blade disc blocked |
| 47 | New map created | Edge blade disc blocked | New map created |

Code 25 is a real version conflict. Builds 911, 920, 926, 977 and 978 select
`FAULT_MAP_FILE_CRACK` through the legacy fallback. Build 983 selects
`FAULT_ROBOT_BOOT_ABNORMAL` from IoT/heartbeat. The client reports
`startup_or_map_data_error` for the A2/A3 families and retains the existing hard
fault tier. It does not guess an installed catalog or downgrade the fault from
the newer app's working-message category.

Code 47's family differences above are consistent across all six retrieved
builds. A2 previously reported task cancellation for this code; the app catalogs
select the new-map event. An A3 edge-cutter fault must not inherit that
informational interpretation.

The A2 overrides for low-light scheduling (16), person detection (27), low battery
temperature (59), maintenance-point notices (75–77) and LiDAR cooling (80) apply
to its family variants. A3 uses the shared modern notices and its own wheel/cutter
overrides. Person detection keeps the existing state-dependent field policy:
confirmed models can continue mowing with a person-detection notice, while a
paused A3 retains its hard-fault behavior. A camera UI flag does not expand that
field-confirmed policy to every model.

The existing exact A1/A1 Pro code meanings remain unchanged. In particular,
the existing interpretations of 19, 73, 53 and 70 differ from these app tables.
The available evidence does not identify a firmware transition, so extending
those interpretations to every A1 Pro suffix would be a guess. Family naming
and family code selection deliberately have different proof requirements.

The plugin's `MIN_SUPPORTED_VERSION` values feed a firmware-upgrade prompt by
comparing the numeric suffix after `_`. They do not establish catalog-change
boundaries and are not used as device-code gates. Plugin build numbers, Android
app versions and mower firmware versions are separate version domains.

## Capability evidence and variant diagnostics

The compatibility report distinguishes a recognized family from an exact retail
name and from hardware validation. A new suffix can be recognized without claiming
that it is a tested SKU. An explicit cloud name or a known retail mapping remains
more specific than the family fallback. `g3255` is an observed identifier without
a confirmed family name or usable EU app-plugin entry.

The report also includes the app profile's camera, PIN, map-learning, dual-camera,
cruise-history, AWD, built-in cellular, and AI-mapping flags. Their source is
`app_profile`, their operational support remains `unknown`, and absent flags
remain distinct from explicit false values. A camera flag does not establish
live-video provisioning, and an app flag does not authorize a device command.
Runtime and device-advertised evidence retain their stronger precedence.

The researched local configuration modules explicitly set the map-learning,
dual-camera and cruise-history flags to false outside the two-eye profiles.
Those values are recorded explicitly; omitted fields in a new or unresearched
profile stay unknown.

For a previously unnamed variant, a useful sanitized report includes the full
raw model ID, vendor product name, account region, firmware and hardware
versions, advertised capabilities and the affected read-only response. Exclude
account IDs, device IDs, tokens, signed URLs and garden geometry. A marketing
name or matching suffix alone does not establish the device's API contract.

The app's `isOnboardRemote` flag is used by the cellular/Link-module status and
subscription screens. It is reported as built-in cellular evidence, not as
permission for manual driving. `needMapLearn` controls an additional workflow
for unlearned areas on `g2420` and `g2566`. The existence of that workflow and
the newer AI-mapping flag does not establish a safe automatic mapping command
for an unvalidated mower.

Fresh authenticated EU queries on 16 October 2026 returned usable entries for
`g2541g` and `g2568e`, sharing researched builds 977 and 978. Queries for `g3255`,
bare `g2538`/`g2566`, and their inspected `a` variants returned version `-1`.
Unauthenticated plugin queries in EU, US and SG returned HTTP 401. Those results
do not establish regional absence or a worldwide product inventory; regional
account and device captures remain the source of field validation.

## Preferences and maintenance contracts

The A3/A3 Pro app settings offer Standard (`0`), Efficient (`1`), and Rush (`3`).
Index `2` is not offered by that selector. The integration exposes Rush on the
recognized `q2501` and `g2541` variants and rejects unsupported write values.
Suffix-specific mowing-efficiency arrays describe the app's displayed estimates;
they do not identify a retail SKU or guarantee a daily mowing area.

The A3 edge-trimmer preference uses numeric modes: `0` means unavailable, `1`
closes/disables trimming, and `2` selects edge trimming. The client preserves
the numeric field when another preference changes. The EdgeMaster switch maps
off/on to `1`/`2` on `q2501`, and is unavailable when installation is unproven.
Existing boolean service arguments remain supported; direct decoded preference
records retain the numeric attachment value rather than collapsing it to a bool.

The app's maintenance table keeps blade, brush and robot counters in CMS slots
0, 1 and 2. A3/A3 Pro profiles omit the brush item and use a 3,000-minute
(50-hour) robot-maintenance interval. The app's two-eye maintenance branch
(`g2420`, `g2566`, including observed MOVA variants) also omits the brush, but
retains the 3,600-minute robot interval and CMS index 2. The other existing
profiles retain the 3,600-minute interval. Negative counters represent
unavailable data rather than fresh consumables.

On `q2501`, CMS slot 3 is a 6,000-minute edge-trimmer counter. The client confirms
the installed attachment using validated preference reads on the active map
before exposing or resetting that counter. This optional context read has a
five-second budget. Global mode uses area 0; custom mode can confirm installation
through any current, validated zone record with mode 1 or 2. Unknown, wrong-map
or stale-revision records do not establish installation. If context fails,
ordinary CMS counters remain available and
the extra slot stays diagnostic. Resetting a counter preserves every other
slot, including unknown extensions. No maintenance reset is performed by
discovery or by a read-only probe.

## Error explanations and unresolved firmware differences

Current diagnostic code context includes the selected name and tier, firmware,
catalog source, possible alternative meanings, and original recovery guidance.
Active condition sensors expose the same guidance while the current snapshot
still confirms that exact condition. Cleared history is not relabeled using a
later firmware's catalog.

The A1/A1 Pro catalog conflicts and code 25's startup/map conflict remain
explicit. The client preserves its existing operational classification rather
than guessing a firmware transition from an app build or minimum-version field.
App popup, persistent-message and suppression categories do not clear a hard
fault or trigger automatic recovery. Unknown codes remain visible and request
diagnostic evidence instead of inheriting an unrelated device's fault meaning.

Resolving an ambiguous meaning requires an observation tying the raw code and
actual condition to a full model and mower firmware. An app update or plugin
build number does not provide that transition. Extra map-learning and AI-map
controls likewise require validated wire data and command/readback evidence
before they can become operational features.

## Source identity and limits

Research on 16 October 2026 used Dreamehome `com.dreame.smartlife` version
2.6.6.3, Android code 102060603, and authenticated, read-only EU plugin metadata.
The APK manifest identifies the app; the mirror's signing certificate was not
independently authenticated. No app execution, mower command or HA deployment
was used. Vendor metadata MD5 values matched each downloaded JavaScript bundle.

| Metadata query | Model plugin version | Common build | JavaScript bundle SHA-256 |
| --- | --- | --- | --- |
| `p2255` | 361 | 920 | `01d882a5eb66b08981cd90a2711300f1a814cfd482d302ecabc1a99d708163a1` |
| `g2422` | 420 | 911 | `5ef09af7b5a56fe494042bac2865d7ed180070601447ac2d5835bdf267e12895` |
| `g2540a` | 123 | 926 | `91f4ae22ab78ab7c84cc063f870e2b18979c0d6616e584632b89680de7964c34` |
| `g2408` | 562 | 983 | `aeaced402689c85a2b5fece4ab934bf5535eb4f62f5f8ac0399e048fdb7e1420` |
| `g2568a` | 169 | 978 | `8451b1afd9856fe7e62487e943c0b13131b59db6ead1473c5305316ae7eb8a3b` |
| `q2501a` | 237 | 977 | `a8188b4fcf83ee9770ee01aaadf394ae6bf98b19d740b072674bdc381c5f566f` |

The remaining confirmed suffixes share the bundle hashes recorded above for
their families; `g2541` variants share build 977 with `q2501` variants. A1/A1 Pro
effective catalogs contain 78 codes and A2/A3 catalogs contain 79 in build 983.
Those counts describe that build, not a universal firmware inventory.

REA 6.5.0 parsed the selected model/fault modules as inert JavaScript. The extended
catalog pass parsed six sources and 20,175 AST nodes without parse failures. A
later contract pass against the unchanged build-983 bundle parsed five named
sources and 13,721 AST nodes, also without parse failures. It traced the
`comm/api/control.ts` backup getters, `components/mowing/setting/content/defs.tsx`
choices, `data/const/setting/defs` maintenance intervals,
`components/header/connection/status/cellular/status` and
`screens/settings/maintain/consume/maintain` consumers. Dynamic Metro
dependency calls are not fully resolved by its generic graph; family selection
and fallback order were also traced directly through named modules and their
dependency arrays. Full proprietary bundles, credentials and account identifiers
are excluded from the repository. Static catalogs establish client metadata,
not physical-device behavior on every firmware or a worldwide product census.
