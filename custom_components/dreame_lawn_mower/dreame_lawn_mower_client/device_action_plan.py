"""Shared device-action policy, separate from blocking or native transport."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from .device_types import ACTION_AVAILABILITY, DreameMowerAction, DreameMowerProperty
from .exceptions import (
    DeviceCommandRejectedException,
    DeviceUpdateFailedException,
    InvalidActionException,
)

_LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .map_manager import DreameMapMowerMapManager


@dataclass(frozen=True)
class ActionDelay:
    """Delay needed after a map selection before sending a cleaning action."""
    seconds: float


@dataclass(frozen=True)
class ActionRequest:
    """One device action with the existing model-specific service mapping."""
    siid: int
    aiid: int
    parameters: Any


@dataclass(frozen=True)
class PropertyRequest:
    """One property write with state and acknowledgement policy in the plan."""
    siid: int
    piid: int
    value: Any
    # Preserve explicit legacy transport policy; native commands never replay.
    legacy_retry_count: int | None = None
    # State plans distinguish unsent cancellation from uncertain device writes.
    on_dispatch: Callable[[], None] | None = field(
        default=None, compare=False, repr=False,
    )

@dataclass(frozen=True)
class PropertyReadRequest:
    """A selected property read whose response is applied by its state plan."""
    properties: list[dict[str, int | str]]
    deadline: float | None = None

@dataclass(frozen=True)
class PropertyResponse:
    """Apply read results using the driver's callback and locking contract."""
    results: object
    require_fresh_state: bool = False

@dataclass(frozen=True)
class MapProperties:
    """Deliver MQTT map properties before the complete message notification."""
    manager: DreameMapMowerMapManager
    properties: list[dict[str, Any]]

@dataclass(frozen=True)
class DeviceReconnect:
    """Reinitialize the same device through the transport's startup owner."""

type DevicePlanEffect = (
    ActionDelay | ActionRequest | PropertyRequest | PropertyReadRequest
    | PropertyResponse | MapProperties | DeviceReconnect
)


def device_action_plan(
    self: Any, action: DreameMowerAction, parameters: Any = None, *,
    enforce_availability: bool = True,
) -> Generator[ActionDelay | ActionRequest, Any, dict[str, Any]]:
    """Preserve validation, state notifications and acknowledgement policy."""
    if not enforce_availability and action is not DreameMowerAction.STOP:
        raise InvalidActionException(
            "Availability can only be bypassed for an authoritative STOP"
        )
    if action not in self.action_mapping:
        raise InvalidActionException(f"Unable to find {action} in the action mapping")

    mapping = self.action_mapping[action]
    if "siid" not in mapping or "aiid" not in mapping:
        raise InvalidActionException(
            f"{action} is not an action (missing siid or aiid)"
        )

    map_action = (
        action is DreameMowerAction.REQUEST_MAP
        or action is DreameMowerAction.UPDATE_MAP_DATA
    )

    if not map_action:
        self.schedule_update(10, True)

    cleaning_action = bool(
        action
        in [
            DreameMowerAction.START_MOWING,
            DreameMowerAction.PAUSE,
            DreameMowerAction.DOCK,
        ]
    )

    if not cleaning_action and enforce_availability:
        available_fn = ACTION_AVAILABILITY.get(action.name)
        if available_fn and not available_fn(self):
            raise InvalidActionException("Action unavailable")
    elif self._map_select_time:
        elapsed = time.time() - self._map_select_time
        self._map_select_time = None
        if elapsed < 5:
            yield ActionDelay(5 - elapsed)

    # Reset consumable on memory
    if action is DreameMowerAction.RESET_BLADES:
        self._consumable_change = True
        self._update_property(DreameMowerProperty.BLADES_LEFT, 100)
        self._update_property(DreameMowerProperty.BLADES_TIME_LEFT, 300)
    elif action is DreameMowerAction.RESET_SIDE_BRUSH:
        self._consumable_change = True
        self._update_property(DreameMowerProperty.SIDE_BRUSH_LEFT, 100)
        self._update_property(DreameMowerProperty.SIDE_BRUSH_TIME_LEFT, 200)
    elif action is DreameMowerAction.RESET_FILTER:
        self._consumable_change = True
        self._update_property(DreameMowerProperty.FILTER_LEFT, 100)
        self._update_property(DreameMowerProperty.FILTER_TIME_LEFT, 150)
    elif action is DreameMowerAction.RESET_SENSOR:
        self._consumable_change = True
        self._update_property(DreameMowerProperty.SENSOR_DIRTY_LEFT, 100)
        self._update_property(DreameMowerProperty.SENSOR_DIRTY_TIME_LEFT, 30)
    elif action is DreameMowerAction.RESET_TANK_FILTER:
        self._consumable_change = True
        self._update_property(DreameMowerProperty.TANK_FILTER_LEFT, 100)
        self._update_property(DreameMowerProperty.TANK_FILTER_TIME_LEFT, 30)
    elif action is DreameMowerAction.RESET_SILVER_ION:
        self._consumable_change = True
        self._update_property(DreameMowerProperty.SILVER_ION_LEFT, 100)
        self._update_property(DreameMowerProperty.SILVER_ION_TIME_LEFT, 365)
    elif action is DreameMowerAction.RESET_LENSBRUSH:
        if parameters is None:
            parameters = {}
        if not isinstance(parameters, dict):
            raise InvalidActionException(
                "Lensbrush reset parameters must be a dictionary"
            )
        parameters['in'] = {
            "CMS": {
                "type": "set",
                "value": [
                    1,
                    0,
                    1
                ]
            }
        }
        self._consumable_change = True
        # The acknowledgement has no updated CMS counters. Keep the structured
        # readings until the property refresh scheduled after dispatch.
    elif action is DreameMowerAction.RESET_SQUEEGEE:
        self._consumable_change = True
        self._update_property(DreameMowerProperty.SQUEEGEE_LEFT, 100)
        self._update_property(DreameMowerProperty.SQUEEGEE_TIME_LEFT, 100)
    elif action is DreameMowerAction.CLEAR_WARNING:
        # Mower property 2.2 uses -1 for no active device code. Vacuum
        # clients used 0, but the A2 catalog assigns 0 to robot lifted.
        self._update_property(DreameMowerProperty.ERROR, -1)

    # Update listeners
    if cleaning_action or self._consumable_change:
        self._property_changed()

    try:
        result: dict[str, Any] | None = yield ActionRequest(
            mapping["siid"], mapping["aiid"], parameters,
        )
    except Exception as ex:
        _LOGGER.error("Send action failed %s: %s", action.name, ex)
        self.schedule_update(1, True)
        raise DeviceUpdateFailedException(
            f"Send action failed {action.name}: {ex}"
        ) from ex

    # Schedule update for retrieving new properties after action sent
    self.schedule_update(6, bool(not map_action and self._protocol.dreame_cloud))
    if result and result.get("code") == 0:
        _LOGGER.info("Send action %s %s", action.name, parameters)
        self._last_change = time.time()
        if not map_action:
            self._last_settings_request = 0
    else:
        _LOGGER.error("Send action failed %s (%s): %s", action.name, parameters, result)
        error_type = (
            DeviceCommandRejectedException
            if result is not None
            else DeviceUpdateFailedException
        )
        raise error_type(
            f"The mower did not acknowledge action {action.name}."
        )

    return result


def run_device_action(
    device: Any, action: DreameMowerAction, parameters: Any = None, *,
    enforce_availability: bool = True,
) -> dict[str, Any]:
    """Run the shared policy through the existing synchronous device protocol."""
    return run_device_plan(device, device_action_plan(
        device, action, parameters, enforce_availability=enforce_availability,
    ))


def run_device_plan[Result](
    device: Any, plan: Generator[DevicePlanEffect, Any, Result],
) -> Result:
    """Execute state policy with legacy sleeps and RPC calls."""
    try:
        effect = next(plan)
        while True:
            try:
                if isinstance(effect, ActionDelay):
                    time.sleep(effect.seconds)
                    response = None
                elif isinstance(effect, DeviceReconnect):
                    response = device.connect_device()
                elif isinstance(effect, MapProperties):
                    effect.manager.handle_properties(effect.properties)
                    response = None
                elif isinstance(effect, PropertyResponse):
                    from .device_property_read import apply_device_property_response

                    response = apply_device_property_response(
                        device, effect.results,
                        require_fresh_state=effect.require_fresh_state,
                    )
                elif isinstance(effect, PropertyReadRequest):
                    response = (
                        device._protocol.get_properties(effect.properties)
                        if effect.deadline is None else device._protocol.get_properties(
                            effect.properties, deadline=effect.deadline,
                        )
                    )
                elif isinstance(effect, PropertyRequest):
                    if effect.on_dispatch is not None:
                        effect.on_dispatch()
                    response = device._protocol.set_property(
                        effect.siid, effect.piid, effect.value,
                        **({"retry_count": effect.legacy_retry_count}
                           if effect.legacy_retry_count is not None else {}),
                    )
                else:
                    response = device._protocol.action(
                        effect.siid, effect.aiid, effect.parameters,
                    )
            except Exception as error:
                effect = plan.throw(error)
            else:
                effect = plan.send(response)
    except StopIteration as completed:
        return cast(Result, completed.value)
    finally:
        plan.close()
