# Entities

[Back to the README](../README.md) · [Configuration](configuration.md)

The primary entity is:

- `lawn_mower.<device>`

Its `feature_capabilities` attribute exposes stable optional-feature support as
`supported`, `unsupported`, or `unknown`, together with the evidence source.
Only confirmed model facts are listed explicitly; unlisted models and features
remain unknown so runtime discovery can continue. The live-video camera is not
created for a model known not to support it, while unknown models retain normal
capability detection.

Common user-facing helpers include:

- `sensor.<device>_activity`
- `sensor.<device>_state_name`
- `sensor.<device>_error`
- `sensor.<device>_battery`
- `sensor.<device>_mowing_progress`
- `sensor.<device>_observed_mowing_time`
- `sensor.<device>_last_observed_run_duration`
- `sensor.<device>_selected_mowing_action`
- `sensor.<device>_selected_map`
- `sensor.<device>_selected_target`
- `sensor.<device>_selected_zone_mowing_height`
- `sensor.<device>_selected_zone_efficiency_mode`
- `sensor.<device>_selected_zone_direction_mode`
- `sensor.<device>_selected_zone_obstacle_avoidance`
- `sensor.<device>_selected_zone_obstacle_distance`
- `sensor.<device>_selected_zone_obstacle_height`
- `sensor.<device>_selected_zone_obstacle_classes`
- `sensor.<device>_runtime_mission_progress`
- `sensor.<device>_runtime_current_area`
- `sensor.<device>_runtime_total_area`
- `sensor.<device>_runtime_live_track_length`
- `sensor.<device>_runtime_live_track_point_count`
- `sensor.<device>_weather_protection_status`
- `select.<device>_map`
- `select.<device>_mowing_action`
- `select.<device>_edge`
- `select.<device>_zone`
- `select.<device>_spot`
- `select.<device>_rain_delay`
- `switch.<device>_charging_period`
- `switch.<device>_rain_protection`
- `time.<device>_charging_period_start`
- `time.<device>_charging_period_end`
- `binary_sensor.<device>_docked`
- `binary_sensor.<device>_charging`
- `binary_sensor.<device>_bluetooth_connected`
- `binary_sensor.<device>_mowing`
- `binary_sensor.<device>_task_active`
- `binary_sensor.<device>_task_resumable`
- `binary_sensor.<device>_rain_delay_active`
- `binary_sensor.<device>_returning`
- `calendar.<device>_schedule`
- `camera.<device>_live_video` on supported Linux hosts

## Mowing time

**Current Mowing Time** is reported by the mower. It remains unavailable when
the firmware supplies no duration; a completed work-log total is not a current
session measurement.

**Observed Mowing Time** is a separate, integration-measured duration in minutes.
It accumulates between successful observations of the mowing state, excludes
observed pauses and docking, and freezes across connection failures. It is a
sampled state duration, not a measurement of blade activity. A new confirmed
session resets it; before mowing is observed it has no value.

Its `partial` attribute is true if observation began mid-session or a connection
gap left time unobserved. After a restart, saved time continues only when fresh
telemetry identifies the same firmware task within 24 hours. Time while Home
Assistant was offline is excluded, and the restored measurement is partial.
If task identity is missing or changed, the saved observation becomes an
interrupted previous-run summary instead of being added to a new session.
The mower entity also exposes `observed_mowing_time` and
`observed_mowing_time_details` attributes for consumers that do not use the sensor.

**Last Observed Run Duration** retains the previous observation after a session
ends or is replaced. Its `measurement_state` distinguishes an observed ending
from an interruption; neither is proof that the mower completed its target area.
Saved summaries older than 30 days are not restored.

### Restart storage

The integration overwrites one private checkpoint per mower, limited to 16 KiB
including storage metadata. It contains only the current observation, one
previous-run summary, and minimal retained position/map identity evidence. It
does not contain routes, point clouds, images, credentials, or raw telemetry.

Progress is checkpointed on a 60-second schedule while observations change.
State transitions are coalesced and rate-limited, and orderly shutdown or reload
flushes the latest observation. A sudden crash can lose progress since the last
checkpoint; it cannot make offline time count as mowing. Atomic replacement
protects the previous file if a write fails. Removing the integration entry
removes its checkpoint. Corrupt or incompatible saved data does not block normal
mower operation.

The changing observation timestamp is excluded from Recorder history, while
the numeric duration remains available for normal HA history. This checkpoint
limit is separate from Recorder retention and the optional saved map preview.

## Optional diagnostics

Many reverse-engineering and validation helpers are disabled by default. Enable
them from the entity registry only when troubleshooting:

- live-path and all-map cameras
- map diagnostics camera
- runtime pose / heading / segment-count sensors
- all-schedules calendar
- rain delay end time sensor
- last schedule probe/write sensors
- last task-status, weather, and preference probe sensors
- raw vendor flag sensors
- manual-drive safety diagnostics
