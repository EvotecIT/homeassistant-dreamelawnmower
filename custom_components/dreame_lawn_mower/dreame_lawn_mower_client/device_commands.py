"""Commands and settings mutations for the legacy device."""

from __future__ import annotations
import asyncio
import logging
import time
import json
import re
import copy
import zlib
import base64
import traceback
from datetime import datetime
from random import randrange
from threading import RLock, Timer
from typing import TYPE_CHECKING, Any, Optional
from collections.abc import Callable, Generator

if TYPE_CHECKING:
    from .device_action_plan import ActionDelay, ActionRequest, PropertyRequest

from .app_protocol import mower_realtime_property_name
from .stream_commands import stream_action_parameters
from .device_privacy import AI_POLICY_PROPERTY, decode_ai_policy_acceptance
from .device_code_semantics import (
    MowerDeviceCodeTier,
    mower_device_code_definition,
    mower_device_code_name,
)
from .device_types import (
    PIID,
    DIID,
    ACTION_AVAILABILITY,
    PROPERTY_AVAILABILITY,
    DreameMowerProperty,
    DreameMowerAutoSwitchProperty,
    DreameMowerStrAIProperty,
    DreameMowerAIProperty,
    DreameMowerPropertyMapping,
    DreameMowerAction,
    DreameMowerActionMapping,
    DreameMowerChargingStatus,
    DreameMowerTaskStatus,
    DreameMowerState,
    DreameMowerStateOld,
    DreameMowerStatus,
    DreameMowerRelocationStatus,
    DreameMowerCleaningMode,
    DreameMowerStreamStatus,
    DreameMowerVoiceAssistantLanguage,
    DreameMowerWiderCornerCoverage,
    DreameMowerSecondCleaning,
    DreameMowerCleaningRoute,
    DreameMowerCleanGenius,
    DreameMowerTaskType,
    DreameMapRecoveryStatus,
    DreameMapBackupStatus,
    DreameMowerDeviceCapability,
    DirtyData,
    RobotType,
    Shortcut,
    ShortcutTask,
    ObstacleType,
    GoToZoneSettings,
    PathType,
    ATTR_ACTIVE_AREAS,
    ATTR_ACTIVE_POINTS,
    ATTR_ACTIVE_SEGMENTS,
    ATTR_PREDEFINED_POINTS,
    ATTR_ACTIVE_CRUISE_POINTS,
)
from .map_types import (
    CleaningHistory,
    CleanupMethod,
    Coordinate,
    MapData,
    Path,
    Segment,
)
from .const import (
    STATE_UNKNOWN,
    STATE_UNAVAILABLE,
    CLEANING_MODE_CODE_TO_NAME,
    CHARGING_STATUS_CODE_TO_NAME,
    RELOCATION_STATUS_CODE_TO_NAME,
    TASK_STATUS_CODE_TO_NAME,
    STATE_CODE_TO_STATE,
    STATUS_CODE_TO_NAME,
    STREAM_STATUS_TO_NAME,
    WIDER_CORNER_COVERAGE_TO_NAME,
    SECOND_CLEANING_TO_NAME,
    CLEANING_ROUTE_TO_NAME,
    CLEANGENIUS_TO_NAME,
    FLOOR_MATERIAL_CODE_TO_NAME,
    FLOOR_MATERIAL_DIRECTION_CODE_TO_NAME,
    SEGMENT_VISIBILITY_CODE_TO_NAME,
    VOICE_ASSISTANT_LANGUAGE_TO_NAME,
    TASK_TYPE_TO_NAME,
    CONSUMABLE_TO_LIFE_WARNING_DESCRIPTION,
    PROPERTY_TO_NAME,
    DEVICE_KEY,
    DREAME_MODEL_CAPABILITIES,
    ATTR_CHARGING,
    ATTR_MOWER_STATE,
    ATTR_DND,
    ATTR_SHORTCUTS,
    ATTR_CLEANING_SEQUENCE,
    ATTR_STARTED,
    ATTR_PAUSED,
    ATTR_RUNNING,
    ATTR_RETURNING_PAUSED,
    ATTR_RETURNING,
    ATTR_MAPPING,
    ATTR_MAPPING_AVAILABLE,
    ATTR_ZONES,
    ATTR_CURRENT_SEGMENT,
    ATTR_SELECTED_MAP,
    ATTR_ID,
    ATTR_NAME,
    ATTR_ICON,
    ATTR_ORDER,
    ATTR_STATUS,
    ATTR_DID,
    ATTR_CLEANING_MODE,
    ATTR_COMPLETED,
    ATTR_CLEANING_TIME,
    ATTR_TIMESTAMP,
    ATTR_CLEANED_AREA,
    ATTR_CLEANGENIUS,
    ATTR_CRUISING_TIME,
    ATTR_CRUISING_TYPE,
    ATTR_MAP_INDEX,
    ATTR_MAP_NAME,
    ATTR_NEGLECTED_SEGMENTS,
    ATTR_INTERRUPT_REASON,
    ATTR_CLEANUP_METHOD,
    ATTR_SEGMENT_CLEANING,
    ATTR_ZONE_CLEANING,
    ATTR_SPOT_CLEANING,
    ATTR_CRUSING,
    ATTR_HAS_SAVED_MAP,
    ATTR_HAS_TEMPORARY_MAP,
    ATTR_CAPABILITIES,
)
from .exceptions import (
    DeviceCommandRejectedException,
    DeviceUpdateFailedException,
    InvalidActionException,
    InvalidValueException,
)
from .protocol import DreameMowerProtocol
from .map_manager import DreameMapMowerMapManager
from .map_decoder import DreameMowerMapDecoder

_LOGGER = logging.getLogger(__name__)

class _DreameMowerDeviceCommandMixin:
    def _set_go_to_zone(self, x, y, size):
        current_cleaning_mode = int(self.status.cleaning_mode.value)

        new_cleaning_mode = None

        cleaning_mode = DreameMowerCleaningMode.MOWING.value

        if current_cleaning_mode != cleaning_mode:
            new_cleaning_mode = cleaning_mode
            current_cleaning_mode = DreameMowerCleaningMode.MOWING.value

        self.status.go_to_zone = GoToZoneSettings(
            x=x,
            y=y,
            stop=True,
            cleaning_mode=current_cleaning_mode,
            size=size,
        )

    def _restore_go_to_zone(self, stop: bool = False) -> None:
        from .device_action_plan import run_device_plan

        run_device_plan(self, self._restore_go_to_zone_plan(stop))

    def _restore_go_to_zone_plan(
        self, stop: bool = False,
    ) -> Generator[ActionDelay | ActionRequest | PropertyRequest, Any, None]:
        from .device_action_plan import ActionRequest

        if self.status.go_to_zone is not None:
            if self.status.go_to_zone:
                stop = stop and self.status.go_to_zone.stop
                cleaning_mode = self.status.go_to_zone.cleaning_mode
                self.status.go_to_zone = None
                if stop:
                    self.schedule_update(10, True)
                    try:
                        mapping = self.action_mapping[DreameMowerAction.STOP]
                        yield ActionRequest(mapping["siid"], mapping["aiid"], None)
                    except Exception:
                        pass

                try:
                    self._cleaning_history_update = time.time()
                    current_mode = self.status.cleaning_mode
                    if cleaning_mode is not None and current_mode is not None and current_mode.value != cleaning_mode:
                        if cleaning_mode != DreameMowerCleaningMode.MOWING.value:
                            raise InvalidValueException("Unsupported mower cleaning mode")
                        yield from self._set_property_plan(DreameMowerProperty.CLEANING_MODE, cleaning_mode)

                    if stop and self.status.started:
                        self._update_status(DreameMowerTaskStatus.COMPLETED, DreameMowerStatus.STANDBY)
                except Exception:
                    pass

                if self._protocol.dreame_cloud:
                    self.schedule_update(3, True)
            else:
                self.status.go_to_zone = None

    def set_property_value(self, prop: str, value: Any):
        if prop is not None and value is not None:
            set_fn = "set_" + prop.lower()
            if hasattr(self, set_fn):
                set_fn = getattr(self, set_fn)
            else:
                set_fn = None

            prop = prop.upper()
            if prop in DreameMowerProperty.__members__:
                prop = DreameMowerProperty(DreameMowerProperty[prop])
                if prop not in self._read_write_properties:
                    raise InvalidActionException("Invalid property")
            elif prop in DreameMowerAutoSwitchProperty.__members__:
                prop = DreameMowerAutoSwitchProperty(DreameMowerAutoSwitchProperty[prop])
            elif prop in DreameMowerAIProperty.__members__:
                prop = DreameMowerAIProperty(DreameMowerAIProperty[prop])
            elif prop in DreameMowerStrAIProperty.__members__:
                prop = DreameMowerStrAIProperty(DreameMowerStrAIProperty[prop])
            elif set_fn is None:
                raise InvalidActionException("Invalid property")

            if set_fn is None and self.get_property(prop) is None:
                raise InvalidActionException("Invalid property")

            prop_name = prop.lower() if isinstance(prop, str) else prop.name

            if (
                (
                    self.status.started
                    or not (
                        prop is DreameMowerProperty.CLEANING_MODE
                        or prop is DreameMowerAutoSwitchProperty.CLEANING_ROUTE
                    )
                )
                and prop_name in PROPERTY_AVAILABILITY
                and not PROPERTY_AVAILABILITY[prop_name](self)
            ):
                raise InvalidActionException("Property unavailable")

            def get_int_value(enum, value, enum_list=None):
                if isinstance(value, str):
                    value = value.upper()
                    if value.isnumeric():
                        value = int(value)
                    elif value in enum.__members__:
                        value = enum[value].value
                        if enum_list is None:
                            return value

                if isinstance(value, int):
                    if enum_list is None:
                        if value in enum._value2member_map_:
                            return value
                    elif value in enum_list.values():
                        return value

            if prop is DreameMowerProperty.CLEANING_MODE:
                value = get_int_value(DreameMowerCleaningMode, value)
            elif prop is DreameMowerProperty.VOICE_ASSISTANT_LANGUAGE:
                value = get_int_value(
                    DreameMowerVoiceAssistantLanguage, value, self.status.voice_assistant_language_list
                )
            elif prop is DreameMowerAutoSwitchProperty.WIDER_CORNER_COVERAGE:
                value = get_int_value(DreameMowerWiderCornerCoverage, value)
            elif prop is DreameMowerAutoSwitchProperty.CLEANING_ROUTE:
                value = get_int_value(DreameMowerCleaningRoute, value, self.status.cleaning_route_list)
            elif prop is DreameMowerAutoSwitchProperty.CLEANGENIUS:
                value = get_int_value(DreameMowerCleanGenius, value)
            elif isinstance(value, bool):
                value = int(value)
            elif isinstance(value, str):
                value = value.upper()
                if value == "TRUE" or value == "1":
                    value = 1
                elif value == "FALSE" or value == "0":
                    value = 0
                elif value.isnumeric():
                    value = int(value)
                else:
                    value = None

            if value is None or not isinstance(value, int):
                raise InvalidActionException("Invalid value")

            if prop == DreameMowerProperty.VOLUME:
                if value < 0 or value > 100:
                    value = None
            elif prop == DreameMowerProperty.CAMERA_LIGHT_BRIGHTNESS:
                if value < 40 or value > 100:
                    value = None

            if value is None:
                raise InvalidActionException("Invalid value")

            if not self.device_connected:
                raise InvalidActionException("Device unavailable")

            if set_fn:
                return set_fn(value)

            if self.get_property(prop) == value or self.set_property(prop, value):
                return
            raise InvalidActionException("Property not updated")
        raise InvalidActionException("Invalid property or value")

    def call_action_value(self, action: str):
        if action is not None:
            if hasattr(self, action):
                action_fn = getattr(self, action)
            else:
                action_fn = None

            action = action.upper()
            if action in DreameMowerAction.__members__:
                action = DreameMowerAction(DreameMowerAction[action])
            elif action_fn is None:
                raise InvalidActionException("Invalid action")

            action_name = action.lower() if isinstance(action, str) else action.name

            if action_name in ACTION_AVAILABILITY and not ACTION_AVAILABILITY[action_name](self):
                raise InvalidActionException("Action unavailable")

            if not self.device_connected:
                raise InvalidActionException("Device unavailable")

            if action_fn:
                return action_fn()

            result = self.call_action(action)
            if result and result.get("code") == 0:
                return
            raise InvalidActionException("Unable to call action")
        raise InvalidActionException("Invalid action")

    def set_property(
        self,
        prop: (
            DreameMowerProperty | DreameMowerAutoSwitchProperty | DreameMowerStrAIProperty | DreameMowerAIProperty
        ),
        value: Any,
    ) -> bool:
        """Sets property value using the existing property mapping and notify listeners
        Property must be set on memory first and notify its listeners because device does not return new value immediately.
        """
        if value is None:
            return False

        if isinstance(prop, DreameMowerAutoSwitchProperty):
            return self.set_auto_switch_property(prop, value)
        if isinstance(prop, DreameMowerStrAIProperty) or isinstance(prop, DreameMowerAIProperty):
            return self.set_ai_property(prop, value)

        from .device_action_plan import run_device_plan

        return run_device_plan(self, self._set_property_plan(prop, value))

    def _set_property_plan(
        self, prop: DreameMowerProperty, value: Any,
    ) -> Generator[PropertyRequest, Any, bool]:
        """Share optimistic state, rollback and acknowledgement across transports."""
        from .device_action_plan import PropertyRequest

        self.schedule_update(10)
        previous_dirty = self._dirty_data.get(prop.value)
        current_value = self._update_property(prop, value)
        if current_value is not None:
            pending_write = None
            if prop not in self._discarded_properties:
                pending_write = DirtyData(value, current_value, time.time())
                self._dirty_data[prop.value] = pending_write

            self._last_change = time.time()
            self._last_settings_request = 0
            dispatched = False

            def mark_dispatched() -> None:
                nonlocal dispatched
                dispatched = True

            def rollback() -> None:
                if pending_write is not None and self._dirty_data.get(prop.value) is not pending_write:
                    return
                self._update_property(prop, current_value)
                if previous_dirty is None:
                    self._dirty_data.pop(prop.value, None)
                else:
                    self._dirty_data[prop.value] = previous_dirty

            try:
                mapping = self.property_mapping[prop]
                result = yield PropertyRequest(
                    mapping["siid"], mapping["piid"], value,
                    on_dispatch=mark_dispatched,
                )

                if result is None or result[0]["code"] != 0:
                    _LOGGER.error(
                        "Property not updated: %s: %s -> %s",
                        prop.name,
                        current_value,
                        value,
                    )
                    rollback()
                    self._property_changed()

                    self.schedule_update(2)
                    return False
                else:
                    _LOGGER.info("Update Property: %s: %s -> %s", prop.name, current_value, value)
                    if pending_write is not None and self._dirty_data.get(prop.value) is pending_write:
                        pending_write.update_time = time.time()

                    self.schedule_update(2)
                    return True
            except (GeneratorExit, asyncio.CancelledError):
                if not dispatched:
                    rollback()
                    self.schedule_update(1)
                raise
            except Exception as ex:
                rollback()
                self.schedule_update(1)
                raise DeviceUpdateFailedException("Set property failed %s: %s", prop.name, ex) from None

        self.schedule_update(1)
        return False

    def call_stream_audio_action(self, property: DreameMowerProperty, parameters=None):
        return self.call_stream_action(DreameMowerAction.STREAM_AUDIO, property, parameters)

    def call_stream_video_action(self, property: DreameMowerProperty, parameters=None):
        return self.call_stream_action(DreameMowerAction.STREAM_VIDEO, property, parameters)

    def call_stream_property_action(self, property: DreameMowerProperty, parameters=None):
        return self.call_stream_action(DreameMowerAction.STREAM_PROPERTY, property, parameters)

    def call_stream_action(
        self,
        action: DreameMowerAction,
        property: DreameMowerProperty,
        parameters=None,
    ):
        return self.call_action(
            action,
            stream_action_parameters(
                PIID(property), parameters, session=self.status.stream_session,
            ),
        )

    def call_shortcut_action(self, command: str, parameters={}):
        return self.call_action(
            DreameMowerAction.SHORTCUTS,
            [
                {
                    "piid": PIID(DreameMowerProperty.CLEANING_PROPERTIES),
                    "value": str(
                        json.dumps(
                            {"cmd": command, "params": parameters},
                            separators=(",", ":"),
                        )
                    ).replace(" ", ""),
                }
            ],
        )

    def call_action(
        self,
        action: DreameMowerAction,
        parameters: dict[str, Any] = None,
        *,
        enforce_availability: bool = True,
    ) -> dict[str, Any] | None:
        """Call an action."""
        from .device_action_plan import run_device_action

        return run_device_action(
            self, action, parameters, enforce_availability=enforce_availability,
        )

    def send_command(self, command: str, parameters: dict[str, Any] = None) -> dict[str, Any] | None:
        """Send a raw command to the device. This is mostly useful when trying out
        commands which are not implemented by a given device instance. (Not likely)"""

        if command == "":
            raise InvalidActionException(f"Invalid Command: ({command}).")

        self.schedule_update(10, True)
        response = self._protocol.send(command, parameters, 0)
        if response:
            _LOGGER.info("Send command response: %s", response)
        else:
            raise DeviceUpdateFailedException(
                f"The mower did not acknowledge command {command}."
            )
        self.schedule_update(2, True)
        return response

    def set_cleaning_mode(self, cleaning_mode: int) -> bool:
        """Set cleaning mode."""
        if self.status.cleaning_mode is None:
            raise InvalidActionException("Cleaning mode is not supported on this device")

        if self.status.cruising:
            raise InvalidActionException("Cannot set cleaning mode when cruising")

        if self.status.scheduled_clean or self.status.shortcut_task:
            raise InvalidActionException("Cannot set cleaning mode when scheduled cleaning or shortcut task")

        if (
            self.status.started
            and self.capability.custom_cleaning_mode
            and (self.status.customized_cleaning and not (self.status.zone_cleaning or self.status.spot_cleaning))
        ):
            raise InvalidActionException("Cannot set cleaning mode when customized cleaning is enabled")

        cleaning_mode = int(cleaning_mode)

        if self.status.started and not PROPERTY_AVAILABILITY[DreameMowerProperty.CLEANING_MODE.name](self):
            raise InvalidActionException("Cleaning mode unavailable")

        return self._update_cleaning_mode(cleaning_mode)

    def set_dnd_task(self, enabled: bool, dnd_start: str, dnd_end: str) -> bool:
        """Set do not disturb task"""
        if dnd_start is None or dnd_start == "":
            dnd_start = "22:00"

        if dnd_end is None or dnd_end == "":
            dnd_end = "08:00"

        time_pattern = re.compile("([0-1][0-9]|2[0-3]):[0-5][0-9]$")
        if not re.match(time_pattern, dnd_start):
            raise InvalidValueException("DnD start time is not valid: (%s).", dnd_start)
        if not re.match(time_pattern, dnd_end):
            raise InvalidValueException("DnD end time is not valid: (%s).", dnd_end)
        if dnd_start == dnd_end:
            raise InvalidValueException(
                "DnD Start time must be different from DnD end time: (%s == %s).",
                dnd_start,
                dnd_end,
            )

        if self.status.dnd_tasks is None:
            self.status.dnd_tasks = []

        if len(self.status.dnd_tasks) == 0:
            self.status.dnd_tasks.append(
                {
                    "id": 1,
                    "en": enabled,
                    "st": dnd_start,
                    "et": dnd_end,
                    "wk": 127,
                    "ss": 0,
                }
            )
        else:
            self.status.dnd_tasks[0]["en"] = enabled
            self.status.dnd_tasks[0]["st"] = dnd_start
            self.status.dnd_tasks[0]["et"] = dnd_end
        return self.set_property(
            DreameMowerProperty.DND_TASK,
            str(json.dumps(self.status.dnd_tasks, separators=(",", ":"))).replace(" ", ""),
        )

    def set_dnd(self, enabled: bool) -> bool:
        """Set do not disturb function"""
        return (
            self.set_property(DreameMowerProperty.DND, bool(enabled))
            if not self.capability.dnd_task
            else self.set_dnd_task(bool(enabled), self.status.dnd_start, self.status.dnd_end)
        )

    def set_dnd_start(self, dnd_start: str) -> bool:
        """Set do not disturb function"""
        return (
            self.set_property(DreameMowerProperty.DND_START, dnd_start)
            if not self.capability.dnd_task
            else self.set_dnd_task(self.status.dnd, str(dnd_start), self.status.dnd_end)
        )

    def set_dnd_end(self, dnd_end: str) -> bool:
        """Set do not disturb function"""
        if not self.capability.dnd_task:
            return self.set_property(DreameMowerProperty.DND_END, dnd_end)
        return self.set_dnd_task(self.status.dnd, self.status.dnd_start, str(dnd_end))

    def set_off_peak_charging_config(self, enabled: bool, start: str, end: str) -> bool:
        """Set of peak charging config"""
        if start is None or start == "":
            start = "22:00"

        if end is None or end == "":
            end = "08:00"

        time_pattern = re.compile("([0-1][0-9]|2[0-3]):[0-5][0-9]$")
        if not re.match(time_pattern, start):
            raise InvalidValueException("Start time is not valid: (%s).", start)
        if not re.match(time_pattern, end):
            raise InvalidValueException("End time is not valid: (%s).", end)
        if start == end:
            raise InvalidValueException("Start time must be different from end time: (%s == %s).", start, end)

        self.status.off_peak_charging_config = {
            "enable": enabled,
            "startTime": start,
            "endTime": end,
        }
        return self.set_property(
            DreameMowerProperty.OFF_PEAK_CHARGING,
            str(json.dumps(self.status.off_peak_charging_config, separators=(",", ":"))).replace(" ", ""),
        )

    def set_off_peak_charging(self, enabled: bool) -> bool:
        """Set off peak charging function"""
        return self.set_off_peak_charging_config(
            bool(enabled),
            self.status.off_peak_charging_start,
            self.status.off_peak_charging_end,
        )

    def set_off_peak_charging_start(self, off_peak_charging_start: str) -> bool:
        """Set off peak charging function"""
        return self.set_off_peak_charging_config(
            self.status.off_peak_charging,
            str(off_peak_charging_start),
            self.status.off_peak_charging_end,
        )

    def set_off_peak_charging_end(self, off_peak_charging_end: str) -> bool:
        """Set off peak charging function"""
        return self.set_off_peak_charging_config(
            self.status.off_peak_charging,
            self.status.off_peak_charging_start,
            str(off_peak_charging_end),
        )

    def set_voice_assistant_language(self, voice_assistant_language: str) -> bool:
        if (
            self.get_property(DreameMowerProperty.VOICE_ASSISTANT_LANGUAGE) is None
            or voice_assistant_language is None
            or len(voice_assistant_language) < 2
            or voice_assistant_language.upper() not in DreameMowerVoiceAssistantLanguage.__members__
        ):
            raise InvalidActionException(f"Voice assistant language ({voice_assistant_language}) is not supported")
        return self.set_property(
            DreameMowerProperty.VOICE_ASSISTANT_LANGUAGE,
            DreameMowerVoiceAssistantLanguage[voice_assistant_language.upper()],
        )

    def locate(self) -> dict[str, Any] | None:
        """Locate the mower cleaner."""
        return self.call_action(DreameMowerAction.LOCATE)

    def start_mowing(self) -> dict[str, Any] | None:
        """Start or resume the cleaning task."""
        from .device_action_plan import run_device_plan

        return run_device_plan(self, self._start_mowing_plan())

    def _start_mowing_plan(
        self,
    ) -> Generator[ActionDelay | ActionRequest | PropertyRequest, Any, dict[str, Any] | None]:
        """Select the start/resume branch before yielding its transport effects."""
        from .device_action_plan import device_action_plan

        if self.status.fast_mapping_paused:
            return (yield from self._start_custom_plan(DreameMowerStatus.FAST_MAPPING.value))

        if self.status.returning_paused:
            return (yield from self._dock_plan())

        if self.capability.cruising:
            if self.status.cruising_paused:
                return (yield from self._start_custom_plan(self.status.status.value))
        elif not self.status.paused:
            yield from self._restore_go_to_zone_plan()


        self.schedule_update(10, True)

        if not self.status.started:
            self._update_status(DreameMowerTaskStatus.AUTO_CLEANING, DreameMowerStatus.CLEANING)
        elif (
            self.status.paused
            and not self.status.cleaning_paused
            and not self.status.cruising
            and not self.status.scheduled_clean
        ):
            self._update_property(DreameMowerProperty.STATUS, DreameMowerStatus.CLEANING.value)
            if self.status.task_status is not DreameMowerTaskStatus.COMPLETED:
                new_state = DreameMowerState.MOWING
                self._update_property(DreameMowerProperty.STATE, new_state.value)

        if self._map_manager:
            if not self.status.started:
                self._map_manager.editor.clear_path()
            self._map_manager.editor.refresh_map()

        response: dict[str, Any] | None = yield from device_action_plan(
            self, DreameMowerAction.START_MOWING,
        )
        return response

    def start(self) -> dict[str, Any] | None:
        """Start or resume the cleaning task."""
        return self.start_mowing()

    def start_custom(self, status, parameters: dict[str, Any] = None) -> dict[str, Any] | None:
        """Start custom cleaning task."""
        from .device_action_plan import run_device_plan

        return run_device_plan(self, self._start_custom_plan(status, parameters))

    def _start_custom_plan(
        self, status: int, parameters: Any = None,
    ) -> Generator[ActionDelay | ActionRequest | PropertyRequest, Any, dict[str, Any] | None]:
        """Share custom-task payload and availability across transports."""
        from .device_action_plan import device_action_plan

        if not self.capability.cruising and status != DreameMowerStatus.ZONE_CLEANING.value:
            yield from self._restore_go_to_zone_plan()

        if status is not DreameMowerStatus.FAST_MAPPING.value and self.status.fast_mapping:
            raise InvalidActionException("Cannot start cleaning while fast mapping")

        payload = [
            {
                "piid": PIID(DreameMowerProperty.STATUS, self.property_mapping),
                "value": status,
            }
        ]

        if parameters is not None:
            payload.append(
                {
                    "piid": PIID(DreameMowerProperty.CLEANING_PROPERTIES, self.property_mapping),
                    "value": parameters,
                }
            )

        response: dict[str, Any] | None = yield from device_action_plan(
            self, DreameMowerAction.START_CUSTOM, payload,
        )
        return response

    def stop(
        self,
        *,
        authoritative_task_active: bool = False,
    ) -> dict[str, Any] | None:
        """Stop the mower cleaner.

        ``authoritative_task_active`` is reserved for the client cancellation
        flow after a fresh combined property and heartbeat read. It bypasses a
        stale legacy-property availability decision, but never fast mapping.
        """
        if self.status.fast_mapping:
            if authoritative_task_active:
                raise InvalidActionException(
                    "Cannot cancel the current task while fast mapping"
                )
            return self.return_to_base()

        from .device_action_plan import run_device_plan

        return run_device_plan(
            self, _DreameMowerDeviceCommandMixin._stop_plan(
                self, authoritative_task_active=authoritative_task_active,
            ),
        )

    def _stop_plan(
        self, *, authoritative_task_active: bool = False,
    ) -> Generator[ActionDelay | ActionRequest | PropertyRequest, Any, dict[str, Any] | None]:
        """Share STOP acknowledgement and subsequent local task cleanup."""
        from .device_action_plan import device_action_plan

        if self.status.fast_mapping:
            raise InvalidActionException(
                "Cannot cancel the current task while fast mapping"
            )

        self.schedule_update(10, True)
        # Dispatch before updating the optimistic local state. Updating the
        # status first makes ``ACTION_AVAILABILITY`` see an idle mower and
        # reject the STOP action locally, so the command never reaches the
        # device.
        response: dict[str, Any] | None = yield from device_action_plan(
            self, DreameMowerAction.STOP,
            enforce_availability=not authoritative_task_active,
        )

        if self.status.started:
            self._update_status(DreameMowerTaskStatus.COMPLETED, DreameMowerStatus.STANDBY)

            # Clear active segments on current map data
            if self._map_manager:
                if self.status.go_to_zone:
                    self._map_manager.editor.set_active_areas([])
                self._map_manager.editor.set_cruise_points([])
                self._map_manager.editor.set_active_segments([])

        return response

    def pause(self) -> dict[str, Any] | None:
        """Pause the cleaning task."""


        from .device_action_plan import run_device_plan

        return run_device_plan(self, self._pause_plan())

    def _pause_plan(self) -> Generator[ActionDelay | ActionRequest | PropertyRequest, Any, Any]:
        """Share pause state transitions and action policy across transports."""
        from .device_action_plan import device_action_plan

        self.schedule_update(10, True)

        if not self.status.paused and self.status.started:
            if self.status.cruising and not self.capability.cruising:
                self._update_property(
                    DreameMowerProperty.STATE,
                    DreameMowerState.MONITORING_PAUSED.value,
                )
            else:
                self._update_property(DreameMowerProperty.STATE, DreameMowerState.PAUSED.value)
            self._update_property(DreameMowerProperty.STATUS, DreameMowerStatus.PAUSED.value)
            if self.status.go_to_zone:
                self._update_property(
                    DreameMowerProperty.TASK_STATUS,
                    DreameMowerTaskStatus.CRUISING_POINT_PAUSED.value,
                )

        return (yield from device_action_plan(self, DreameMowerAction.PAUSE))

    def return_to_base(self) -> dict[str, Any] | None:
        """Set the mower cleaner to return to the dock."""
        from .device_action_plan import run_device_plan

        return run_device_plan(self, self._dock_plan())

    def _dock_plan(
        self,
    ) -> Generator[ActionDelay | ActionRequest | PropertyRequest, Any, dict[str, Any] | None]:
        """Share return-to-base state preparation and device acknowledgement."""
        from .device_action_plan import device_action_plan

        if self._map_manager:
            self._map_manager.editor.set_cruise_points([])

        # if self.status.started:
        if not self.status.docked:
            self._update_property(DreameMowerProperty.STATUS, DreameMowerStatus.BACK_HOME.value)
            self._update_property(DreameMowerProperty.STATE, DreameMowerState.RETURNING.value)

        # Clear active segments on current map data
        # if self._map_manager:
        #    self._map_manager.editor.set_active_segments([])

        if not self.capability.cruising:
            yield from self._restore_go_to_zone_plan()
        response: dict[str, Any] | None = yield from device_action_plan(
            self, DreameMowerAction.DOCK,
        )
        return response

    def dock(self) -> dict[str, Any] | None:
        """Set the mower cleaner to return to the dock."""
        return self.return_to_base()

    def _ordinary_stop_plan(self) -> Generator[ActionDelay | ActionRequest | PropertyRequest, Any, Any]:
        """Preserve ordinary STOP's mapping return-to-base behavior."""
        if self.status.fast_mapping:
            return (yield from self._dock_plan())
        return (yield from self._stop_plan())

    def start_pause(self) -> dict[str, Any] | None:
        """Start or resume the cleaning task."""
        if (
            not self.status.started
            or self.status.state is DreameMowerState.PAUSED
            or self.status.status is DreameMowerStatus.BACK_HOME
        ):
            return self.start()
        return self.pause()

    def clean_zone(
        self,
        zones: list[int] | list[list[int]],
        cleaning_times: int | list[int],
    ) -> dict[str, Any] | None:
        """Clean selected area."""

        if not isinstance(zones, list) or not zones:
            raise InvalidActionException(f"Invalid zone coordinates: %s", zones)

        if not isinstance(zones[0], list):
            zones = [zones]

        if cleaning_times is None or cleaning_times == "":
            cleaning_times = 1

        cleanlist = []
        index = 0
        for zone in zones:
            if not isinstance(zone, list) or len(zone) != 4:
                raise InvalidActionException(f"Invalid zone coordinates: %s", zone)

            if isinstance(cleaning_times, list):
                if index < len(cleaning_times):
                    repeat = cleaning_times[index]
                else:
                    repeat = 1
            else:
                repeat = cleaning_times

            index = index + 1

            x_coords = sorted([zone[0], zone[2]])
            y_coords = sorted([zone[1], zone[3]])

            grid_size = self.status.current_map.dimensions.grid_size if self.status.current_map else 50
            w = (x_coords[1] - x_coords[0]) / grid_size
            h = (y_coords[1] - y_coords[0]) / grid_size

            if h <= 1.0 or w <= 1.0:
                raise InvalidActionException(f"Zone {index} is smaller than minimum zone size ({h}, {w})")

            cleanlist.append(
                [
                    int(round(zone[0])),
                    int(round(zone[1])),
                    int(round(zone[2])),
                    int(round(zone[3])),
                    max(1, repeat),
                ]
            )

        self.schedule_update(10, True)
        if not self.capability.cruising:
            self._restore_go_to_zone()
        if not self.status.started or self.status.paused:
            self._update_status(DreameMowerTaskStatus.ZONE_CLEANING, DreameMowerStatus.ZONE_CLEANING)

            if self._map_manager:
                # Set active areas on current map data is implemented on the app
                if not self.status.started:
                    self._map_manager.editor.clear_path()
                self._map_manager.editor.set_active_areas(zones)

        return self.start_custom(
            DreameMowerStatus.ZONE_CLEANING.value,
            str(json.dumps({"areas": cleanlist}, separators=(",", ":"))).replace(" ", ""),
        )

    def clean_segment(
        self,
        selected_segments: int | list[int],
        cleaning_times: int | list[int] | None = None,
        timestamp: int | None = None,
    ) -> dict[str, Any] | None:
        """Clean selected segment using id."""

        if self.status.current_map and not self.status.has_saved_map:
            raise InvalidActionException("Cannot clean segments on current map")

        if not isinstance(selected_segments, list):
            selected_segments = [selected_segments]

        if cleaning_times is None or cleaning_times == "":
            cleaning_times = 1

        cleanlist = []
        index = 0
        segments = self.status.current_segments

        for segment_id in selected_segments:
            if isinstance(cleaning_times, list):
                if index < len(cleaning_times):
                    repeat = cleaning_times[index]
                else:
                    if segments and segment_id in segments and self.status.customized_cleaning:
                        repeat = segments[segment_id].cleaning_times
                    else:
                        repeat = 1
            else:
                repeat = cleaning_times


            index = index + 1
            cleanlist.append([segment_id, max(1, repeat), index])

        self.schedule_update(10, True)
        if not self.status.started or self.status.paused:
            self._update_status(
                DreameMowerTaskStatus.SEGMENT_CLEANING,
                DreameMowerStatus.SEGMENT_CLEANING,
            )

            if self._map_manager:
                if not self.status.started:
                    self._map_manager.editor.clear_path()

                # Set active segments on current map data is implemented on the app
                self._map_manager.editor.set_active_segments(selected_segments)

        data = {"selects": cleanlist}
        if timestamp is not None:
            data["timestamp"] = timestamp

        return self.start_custom(
            DreameMowerStatus.SEGMENT_CLEANING.value,
            str(json.dumps(data, separators=(",", ":"))).replace(" ", ""),
        )

    def clean_spot(
        self,
        points: list[int] | list[list[int]],
        cleaning_times: int | list[int] | None,
    ) -> dict[str, Any] | None:
        """Clean 1.5 square meters area of selected points."""

        if not isinstance(points, list) or not points:
            raise InvalidActionException(f"Invalid point coordinates: %s", points)

        if not isinstance(points[0], list):
            points = [points]

        if cleaning_times is None or cleaning_times == "":
            cleaning_times = 1

        cleanlist = []
        index = 0
        for point in points:
            if isinstance(cleaning_times, list):
                if index < len(cleaning_times):
                    repeat = cleaning_times[index]
                else:
                    repeat = 1
            else:
                repeat = cleaning_times


            index = index + 1

            if self.status.current_map and not self.status.current_map.check_point(point[0], point[1]):
                raise InvalidActionException(f"Coordinate ({point[0]}, {point[1]}) is not inside the map")

            cleanlist.append(
                [
                    int(round(point[0])),
                    int(round(point[1])),
                    repeat,
                ]
            )

        self.schedule_update(10, True)
        if not self.status.started or self.status.paused:
            self._update_status(DreameMowerTaskStatus.SPOT_CLEANING, DreameMowerStatus.SPOT_CLEANING)

            if self._map_manager:
                if not self.status.started:
                    self._map_manager.editor.clear_path()

                # Set active points on current map data is implemented on the app
                self._map_manager.editor.set_active_points(points)

        return self.start_custom(
            DreameMowerStatus.SPOT_CLEANING.value,
            str(json.dumps({"points": cleanlist}, separators=(",", ":"))).replace(" ", ""),
        )

    def go_to(self, x, y) -> dict[str, Any] | None:
        """Go to a point and take pictures around."""
        if self.status.current_map and not self.status.current_map.check_point(x, y):
            raise InvalidActionException("Coordinate is not inside the map")

        if self.status.battery_level < 15:
            raise InvalidActionException(
                "Low battery capacity. Please start the robot for working after it being fully charged."
            )

        if not self.capability.cruising:
            size = self.status.current_map.dimensions.grid_size if self.status.current_map else 50
            if self.status.current_map and self.status.current_map.robot_position:
                position = self.status.current_map.robot_position
                if abs(x - position.x) <= size and abs(y - position.y) <= size:
                    raise InvalidActionException(f"Robot is already on selected coordinate")
            self._set_go_to_zone(x, y, size)
            zone = [
                x - int(size / 2),
                y - int(size / 2),
                x + int(size / 2),
                y + int(size / 2),
            ]

        if not (self.status.started or self.status.paused):
            self._update_property(DreameMowerProperty.STATE, DreameMowerState.MONITORING.value)
            self._update_property(DreameMowerProperty.STATUS, DreameMowerStatus.CRUISING_POINT.value)
            self._update_property(
                DreameMowerProperty.TASK_STATUS,
                DreameMowerTaskStatus.CRUISING_POINT.value,
            )

            if self._map_manager:
                # Set active cruise points on current map data is implemented on the app
                self._map_manager.editor.set_cruise_points([[x, y, 0, 0]])

        if self.capability.cruising:
            return self.start_custom(
                DreameMowerStatus.CRUISING_POINT.value,
                str(
                    json.dumps(
                        {"tpoint": [[x, y, 0, 0]]},
                        separators=(",", ":"),
                    )
                ).replace(" ", ""),
            )
        else:
            cleanlist = [
                int(round(zone[0])),
                int(round(zone[1])),
                int(round(zone[2])),
                int(round(zone[3])),
                1,
                0,
                1,
            ]

            response = self.start_custom(
                DreameMowerStatus.ZONE_CLEANING.value,
                str(json.dumps({"areas": [cleanlist]}, separators=(",", ":"))).replace(" ", ""),
            )
            if not response:
                self._restore_go_to_zone()

            return response

    def follow_path(self, points: list[int] | list[list[int]]) -> dict[str, Any] | None:
        """Start a survaliance job."""
        if not self.capability.cruising:
            raise InvalidActionException("Follow path is supported on this device")

        if self.status.stream_status != DreameMowerStreamStatus.IDLE:
            raise InvalidActionException(f"Follow path only works with live camera streaming")

        if self.status.battery_level < 15:
            raise InvalidActionException(
                "Low battery capacity. Please start the robot for working after it being fully charged."
            )

        if not points:
            points = []

        if points and not isinstance(points[0], list):
            points = [points]

        if self.status.current_map:
            for point in points:
                if not self.status.current_map.check_point(point[0], point[1]):
                    raise InvalidActionException(f"Coordinate ({point[0]}, {point[1]}) is not inside the map")

        path = []
        for point in points:
            path.append([int(round(point[0])), int(round(point[1])), 0, 1])

        predefined_points = []
        if self.status.current_map and self.status.current_map.predefined_points:
            for point in self.status.current_map.predefined_points.values():
                predefined_points.append([int(round(point.x)), int(round(point.y)), 0, 1])

        if len(path) == 0:
            path.extend(predefined_points)

        if len(path) == 0:
            raise InvalidActionException("At least one valid or saved coordinate is required")

        if not self.status.started or self.status.paused:
            self._update_property(DreameMowerProperty.STATE, DreameMowerState.MONITORING.value)
            self._update_property(DreameMowerProperty.STATUS, DreameMowerStatus.CRUISING_PATH.value)
            self._update_property(
                DreameMowerProperty.TASK_STATUS,
                DreameMowerTaskStatus.CRUISING_PATH.value,
            )

            if self._map_manager:
                # Set active cruise points on current map data is implemented on the app
                self._map_manager.editor.set_cruise_points(path[:20])

        return self.start_custom(
            DreameMowerStatus.CRUISING_PATH.value,
            str(
                json.dumps(
                    {"tpoint": path[:20]},
                    separators=(",", ":"),
                )
            ).replace(" ", ""),
        )

    def start_shortcut(self, shortcut_id: int) -> dict[str, Any] | None:
        """Start shortcut job."""

        if not self.status.started:
            if self.status.status is DreameMowerStatus.STANDBY:
                self._update_property(DreameMowerProperty.STATE, DreameMowerState.IDLE.value)

            self._update_property(DreameMowerProperty.STATUS, DreameMowerStatus.SEGMENT_CLEANING.value)
            self._update_property(
                DreameMowerProperty.TASK_STATUS,
                DreameMowerTaskStatus.SEGMENT_CLEANING.value,
            )

        if self.status.shortcuts and shortcut_id in self.status.shortcuts:
            self.status.shortcuts[shortcut_id].running = True

        return self.start_custom(
            DreameMowerStatus.SHORTCUT.value,
            str(shortcut_id),
        )

    def start_fast_mapping(self) -> dict[str, Any] | None:
        """Fast map."""
        if self.status.fast_mapping:
            return

        if self.status.battery_level < 15:
            raise InvalidActionException(
                "Low battery capacity. Please start the robot for working after it being fully charged."
            )

        self.schedule_update(10, True)
        self._update_status(DreameMowerTaskStatus.FAST_MAPPING, DreameMowerStatus.FAST_MAPPING)

        if self._map_manager:
            self._map_manager.editor.refresh_map()

        return self.start_custom(DreameMowerStatus.FAST_MAPPING.value)

    def start_mapping(self) -> dict[str, Any] | None:
        """Create a new map by cleaning whole floor."""
        self.schedule_update(10, True)
        if self._map_manager:
            self._update_status(DreameMowerTaskStatus.AUTO_CLEANING, DreameMowerStatus.CLEANING)
            self._map_manager.editor.reset_map()

        return self.start_custom(DreameMowerStatus.CLEANING.value, "3")

    def clear_warning(self) -> dict[str, Any] | None:
        """Clear an actionable device-code notice from the mower."""
        device_code = self.status.device_code
        if self.status.has_warning and device_code is not None:
            return self.call_action(
                DreameMowerAction.CLEAR_WARNING,
                [
                    {
                        "piid": PIID(
                            DreameMowerProperty.CLEANING_PROPERTIES,
                            self.property_mapping,
                        ),
                        "value": f"[{device_code}]",
                    }
                ],
            )

    def remote_control_move_step(
        self, rotation: int = 0, velocity: int = 0, prompt: bool | None = None
    ) -> dict[str, Any] | None:
        """Send remote control command to device."""
        siid, piid, payload = self._prepare_remote_control_step(
            rotation, velocity, prompt
        )
        return self._protocol.set_property(siid, piid, payload, 1)

    def _prepare_remote_control_step(
        self, rotation: int, velocity: int, prompt: bool | None,
    ) -> tuple[int, int, str]:
        """Prepare the shared remote-control payload and local activity flag."""
        if self.status.fast_mapping:
            raise InvalidActionException("Cannot remote control mower while fast mapping")

        payload = '{"spdv":%(velocity)d,"spdw":%(rotation)d,"audio":"%(audio)s","random":%(random)d}' % {
            "velocity": velocity,
            "rotation": rotation,
            "audio": (
                "true"
                if prompt == True
                else (
                    "false"
                    if prompt == False or self._remote_control or self.status.status is DreameMowerStatus.SLEEPING
                    else "true"
                )
            ),
            "random": randrange(65535),
        }
        self._remote_control = True
        mapping = self.property_mapping[DreameMowerProperty.REMOTE_CONTROL]
        return mapping["siid"], mapping["piid"], payload

    def install_voice_pack(self, lang_id: int, url: str, md5: str, size: int) -> dict[str, Any] | None:
        """install a custom language pack"""
        payload = '{"id":"%(lang_id)s","url":"%(url)s","md5":"%(md5)s","size":%(size)d}' % {
            "lang_id": lang_id,
            "url": url,
            "md5": md5,
            "size": size,
        }
        mapping = self.property_mapping[DreameMowerProperty.VOICE_CHANGE]
        return self._protocol.set_property(mapping["siid"], mapping["piid"], payload, 3)

    def set_ai_detection(self, settings: dict[str, bool] | int) -> dict[str, Any] | None:
        """Send ai detection parameters to the device."""
        if self.capability.ai_detection:
            if isinstance(settings, int):
                enabled = settings & (
                    DreameMowerAIProperty.AI_OBSTACLE_DETECTION
                    | DreameMowerAIProperty.AI_OBSTACLE_IMAGE_UPLOAD
                )
                requires_acceptance = bool(enabled)
            else:
                requires_acceptance = bool(
                    settings.get(DreameMowerStrAIProperty.AI_OBSTACLE_DETECTION.value)
                    or settings.get(DreameMowerStrAIProperty.AI_OBSTACLE_IMAGE_UPLOAD.value)
                )
            self._require_ai_policy_acceptance(requires_acceptance)
            return self._send_ai_detection(settings)

    def _require_ai_policy_acceptance(self, requires_acceptance: bool) -> None:
        """Validate explicit consent without changing device settings."""
        if requires_acceptance and not self.status.ai_policy_accepted:
            if self._protocol.cloud:
                response = self._protocol.cloud.get_batch_device_datas([AI_POLICY_PROPERTY])
                accepted = decode_ai_policy_acceptance(response)
                if accepted is not None:
                    self.status.ai_policy_accepted = accepted
            if not self.status.ai_policy_accepted:
                raise InvalidActionException(
                    "You need to accept privacy policy from the App before enabling AI obstacle detection feature"
                )

    def _send_ai_detection(self, settings: dict[str, bool] | int) -> dict[str, Any] | None:
        """Send settings after the caller has validated the requested edit."""
        mapping = self.property_mapping[DreameMowerProperty.AI_DETECTION]
        if isinstance(settings, int):
            return self._protocol.set_property(mapping["siid"], mapping["piid"], settings, 3)
        return self._protocol.set_property(
            mapping["siid"], mapping["piid"],
            str(json.dumps(settings, separators=(",", ":"))).replace(" ", ""), 3,
        )

    def set_ai_property(
        self, prop: DreameMowerStrAIProperty | DreameMowerAIProperty, value: bool
    ) -> dict[str, Any] | None:
        if self.capability.ai_detection:
            if prop.name not in self.ai_data:
                raise InvalidActionException("Not supported")
            current_value = self.get_ai_property(prop)
            previous_dirty = self._dirty_ai_data.get(prop.name)

            def rollback() -> None:
                if previous_dirty is None:
                    self._dirty_ai_data.pop(prop.name, None)
                else:
                    self._dirty_ai_data[prop.name] = previous_dirty
                self.ai_data[prop.name] = current_value
                self._property_changed()

            self._dirty_ai_data[prop.name] = DirtyData(value, current_value, time.time())
            self.ai_data[prop.name] = value
            ai_value = self.get_property(DreameMowerProperty.AI_DETECTION)
            self._property_changed()
            try:
                self._require_ai_policy_acceptance(bool(value) and prop.name in {
                    DreameMowerAIProperty.AI_OBSTACLE_DETECTION.name,
                    DreameMowerAIProperty.AI_OBSTACLE_IMAGE_UPLOAD.name,
                })
                if isinstance(ai_value, int):
                    # Preserve acknowledged edits that are newer than raw readback.
                    for cached_prop in DreameMowerAIProperty:
                        if cached_prop.name in self.ai_data:
                            bit = cached_prop.value
                            ai_value = (ai_value | bit) if self.ai_data[cached_prop.name] else (ai_value & ~bit)
                    result = self._send_ai_detection(ai_value)
                else:
                    result = self._send_ai_detection({DreameMowerStrAIProperty[prop.name].value: bool(value)})

                if result is None or result[0]["code"] != 0:
                    _LOGGER.error(
                        "AI Property not updated: %s: %s -> %s",
                        prop.name,
                        current_value,
                        value,
                    )
                    rollback()
            except Exception:
                rollback()
                raise
            return result

    def set_auto_switch_settings(self, settings) -> dict[str, Any] | None:
        from .device_action_plan import run_device_plan

        return run_device_plan(self, self._set_auto_switch_settings_plan(settings))

    def _set_auto_switch_settings_plan(
        self, settings: Any, *, on_dispatch: Callable[[], None] | None = None,
    ) -> Generator[PropertyRequest, Any, Any]:
        from .device_action_plan import PropertyRequest

        if self.capability.auto_switch_settings:
            mapping = self.property_mapping[DreameMowerProperty.AUTO_SWITCH_SETTINGS]
            return (yield PropertyRequest(
                mapping["siid"],
                mapping["piid"],
                str(json.dumps(settings, separators=(",", ":"))).replace(" ", ""),
                legacy_retry_count=1,
                on_dispatch=on_dispatch,
            ))

    def set_auto_switch_property(self, prop: DreameMowerAutoSwitchProperty, value: int) -> dict[str, Any] | None:
        from .device_action_plan import run_device_plan

        return run_device_plan(self, self._set_auto_switch_property_plan(prop, value))

    def _set_auto_switch_property_plan(
        self, prop: DreameMowerAutoSwitchProperty, value: int,
    ) -> Generator[PropertyRequest, Any, Any]:
        if self.capability.auto_switch_settings:
            if prop.name not in self.auto_switch_data:
                raise InvalidActionException("Not supported")
            current_value = self.get_auto_switch_property(prop)
            if current_value != value:
                previous_dirty = self._dirty_auto_switch_data.get(prop.name)
                pending_write = DirtyData(value, current_value, time.time())
                self._dirty_auto_switch_data[prop.name] = pending_write
                self.auto_switch_data[prop.name] = value
                self._property_changed()
                result = None
                dispatched = False

                def mark_dispatched() -> None:
                    nonlocal dispatched
                    dispatched = True

                def rollback() -> None:
                    if self._dirty_auto_switch_data.get(prop.name) is not pending_write:
                        return
                    if previous_dirty is None:
                        self._dirty_auto_switch_data.pop(prop.name, None)
                    else:
                        self._dirty_auto_switch_data[prop.name] = previous_dirty
                    self.auto_switch_data[prop.name] = current_value
                    self._property_changed()

                try:
                    result = yield from self._set_auto_switch_settings_plan(
                        {"k": prop.value, "v": int(value)},
                        on_dispatch=mark_dispatched,
                    )
                    if result is None or result[0]["code"] != 0:
                        _LOGGER.error(
                            "Auto Switch Property not updated: %s: %s -> %s",
                            prop.name,
                            current_value,
                            value,
                        )
                        rollback()
                    else:
                        _LOGGER.info("Update Property: %s: %s -> %s", prop.name, current_value, value)
                        if self._dirty_auto_switch_data.get(prop.name) is pending_write:
                            pending_write.update_time = time.time()
                except (GeneratorExit, asyncio.CancelledError):
                    if not dispatched:
                        rollback()
                    raise
                except Exception:
                    rollback()
                return result

    def set_camera_light_brightness(self, brightness: int) -> dict[str, Any] | None:
        if self.capability.auto_switch_settings:
            if brightness < 40:
                brightness = 40
            current_value = self.status.camera_light_brightness
            self._update_property(DreameMowerProperty.CAMERA_LIGHT_BRIGHTNESS, str(brightness))
            result = self.call_stream_property_action(
                DreameMowerProperty.CAMERA_LIGHT_BRIGHTNESS, {"value": str(brightness)}
            )
            if result is None or result.get("code") != 0:
                self._update_property(DreameMowerProperty.CAMERA_LIGHT_BRIGHTNESS, str(current_value))
            return result

    def set_wider_corner_coverage(self, value: int) -> dict[str, Any] | None:
        if self.capability.auto_switch_settings:
            current_value = self.get_auto_switch_property(DreameMowerAutoSwitchProperty.WIDER_CORNER_COVERAGE)
            if current_value is not None and current_value > 0 and value <= 0:
                value = -current_value
            return self.set_auto_switch_property(DreameMowerAutoSwitchProperty.WIDER_CORNER_COVERAGE, value)

    def set_resume_cleaning(self, value: int) -> dict[str, Any] | None:
        if self.capability.auto_charging and bool(value):
            value = 2
        return self.set_property(DreameMowerProperty.RESUME_CLEANING, value)

    def set_multi_floor_map(self, enabled: bool) -> bool:
        if self.set_property(DreameMowerProperty.MULTI_FLOOR_MAP, int(enabled)):
            if (
                self.capability.auto_switch_settings
                and not enabled
                and self.get_property(DreameMowerProperty.INTELLIGENT_RECOGNITION) == 1
            ):
                self.set_property(DreameMowerProperty.INTELLIGENT_RECOGNITION, 0)
            return True
        return False

    def rename_shortcut(self, shortcut_id: int, shortcut_name: str = "") -> dict[str, Any] | None:
        """Rename a shortcut"""
        if self.status.started:
            raise InvalidActionException("Cannot rename a shortcut while mower is running")

        if not self.capability.shortcuts or not self.status.shortcuts:
            raise InvalidActionException("Shortcuts are not supported on this device")

        if shortcut_id not in self.status.shortcuts:
            raise InvalidActionException(f"Shortcut {shortcut_id} not found")

        if shortcut_name and len(shortcut_name) > 0:
            current_name = self.status.shortcuts[shortcut_id]
            if current_name != shortcut_name:
                counter = 1
                for id, shortcut in self.status.shortcuts.items():
                    if shortcut.name == shortcut_name and shortcut.id != shortcut_id:
                        counter = counter + 1

                if counter > 1:
                    shortcut_name = f"{shortcut_name}{counter}"

                self.status.shortcuts[shortcut_id].name = shortcut_name
                shortcut_name = base64.b64encode(shortcut_name.encode("utf-8")).decode("utf-8")
                shortcuts = self.get_property(DreameMowerProperty.SHORTCUTS)
                if shortcuts and shortcuts != "":
                    shortcuts = json.loads(shortcuts)
                    if shortcuts:
                        for shortcut in shortcuts:
                            if shortcut["id"] == shortcut_id:
                                shortcut["name"] = shortcut_name
                                break
                self._update_property(
                    DreameMowerProperty.SHORTCUTS,
                    str(json.dumps(shortcuts, separators=(",", ":"))).replace(" ", ""),
                )
                self._property_changed()

                success = False
                response = self.call_shortcut_action(
                    "EDIT_COMMAND",
                    {"id": shortcut_id, "name": shortcut_name, "type": 3},
                )
                if response and "out" in response:
                    data = response["out"]
                    if data and len(data):
                        if "value" in data[0] and data[0]["value"] != "":
                            success = data[0]["value"] == "0"
                if not success:
                    self.status.shortcuts[shortcut_id].name = current_name
                    self._property_changed()
                return response

    def set_obstacle_ignore(self, x, y, obstacle_ignored) -> dict[str, Any] | None:
        if not self.capability.ai_detection:
            raise InvalidActionException("Obstacle detection is not available on this device")

        if not self._map_manager:
            raise InvalidActionException("Obstacle ignore requires cloud connection")

        if self.status.started:
            raise InvalidActionException("Cannot set obstacle ignore status while mower is running")

        if not self.status.current_map and not self.status.current_map.obstacles:
            raise InvalidActionException("Obstacle not found")

        if self.status.current_map.obstacles is None or (
            len(self.status.current_map.obstacles)
            and next(iter(self.status.current_map.obstacles.values())).ignore_status is None
        ):
            raise InvalidActionException("Obstacle ignore is not supported on this device")

        found = False
        obstacle_type = 142
        for k, v in self.status.current_map.obstacles.items():
            if int(v.x) == int(x) and int(v.y) == int(y):
                if v.ignore_status.value == 2:
                    raise InvalidActionException("Cannot ignore a dynamically ignored obstacle")
                obstacle_type = v.type.value
                found = True
                break

        if not found:
            raise InvalidActionException("Obstacle not found")

        self._map_manager.editor.set_obstacle_ignore(x, y, obstacle_ignored)
        return self.update_map_data_async(
            {
                "obstacleignore": [
                    int(x),
                    int(y),
                    obstacle_type,
                    1 if bool(obstacle_ignored) else 0,
                ]
            }
        )

    def set_router_position(self, x, y):
        if not self.capability.wifi_map:
            raise InvalidActionException("WiFi map is not available on this device")

        if self.status.started:
            raise InvalidActionException("Cannot set router position while mower is running")

        if self._map_manager:
            self._map_manager.editor.set_router_position(x, y)
        return self.update_map_data_async({"wrp": [int(x), int(y)]})
