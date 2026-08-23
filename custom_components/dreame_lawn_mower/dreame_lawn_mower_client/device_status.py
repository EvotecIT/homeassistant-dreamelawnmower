"""Status interpretation for the legacy mower device."""

from __future__ import annotations
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
from typing import Any, Optional

from .app_protocol import mower_realtime_property_name
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
    DeviceUpdateFailedException,
    InvalidActionException,
    InvalidValueException,
)
from .protocol import DreameMowerProtocol
from .map_manager import DreameMapMowerMapManager
from .map_decoder import DreameMowerMapDecoder

_LOGGER = logging.getLogger(__name__)

_RUNNING_STATUSES = frozenset(
    {
        DreameMowerStatus.CLEANING,
        DreameMowerStatus.BACK_HOME,
        DreameMowerStatus.PART_CLEANING,
        DreameMowerStatus.FOLLOW_WALL,
        DreameMowerStatus.REMOTE_CONTROL,
        DreameMowerStatus.SEGMENT_CLEANING,
        DreameMowerStatus.ZONE_CLEANING,
        DreameMowerStatus.SPOT_CLEANING,
        DreameMowerStatus.FAST_MAPPING,
        DreameMowerStatus.CRUISING_PATH,
        DreameMowerStatus.CRUISING_POINT,
        DreameMowerStatus.SUMMON_CLEAN,
        DreameMowerStatus.SHORTCUT,
        DreameMowerStatus.PERSON_FOLLOW,
    }
)
_ACTIVE_TASK_STATUSES = (_RUNNING_STATUSES - {DreameMowerStatus.BACK_HOME}) | {
    DreameMowerStatus.PAUSED
}



class DreameMowerDeviceStatus:
    """Helper class for device status and int enum type properties.
    This class is used for determining various states of the device by its properties.
    Determined states are used by multiple validation and rendering condition checks.
    Almost of the rules are extracted from mobile app that has a similar class with same purpose.
    """

    def __init__(self, device):
        self._device: DreameMowerDevice = device
        self._cleaning_history = None
        self._cleaning_history_attrs = None
        self._last_cleaning_time = None
        self._cruising_history = None
        self._cruising_history_attrs = None
        self._last_cruising_time = None
        self._history_map_data: dict[str, MapData] = {}
        self._previous_cleaning_sequence: dict[int, list[int]] = {}

        self.cleaning_mode_list = {v: k for k, v in CLEANING_MODE_CODE_TO_NAME.items()}
        self.wider_corner_coverage_list = {v: k for k, v in WIDER_CORNER_COVERAGE_TO_NAME.items()}
        self.second_cleaning_list = {v: k for k, v in SECOND_CLEANING_TO_NAME.items()}
        self.cleaning_route_list = {v: k for k, v in CLEANING_ROUTE_TO_NAME.items()}
        self.cleangenius_list = {v: k for k, v in CLEANGENIUS_TO_NAME.items()}
        self.floor_material_list = {v: k for k, v in FLOOR_MATERIAL_CODE_TO_NAME.items()}
        self.floor_material_direction_list = {v: k for k, v in FLOOR_MATERIAL_DIRECTION_CODE_TO_NAME.items()}
        self.visibility_list = {v: k for k, v in SEGMENT_VISIBILITY_CODE_TO_NAME.items()}
        self.voice_assistant_language_list = {v: k for k, v in VOICE_ASSISTANT_LANGUAGE_TO_NAME.items()}
        self.segment_cleaning_mode_list = {}
        self.segment_cleaning_route_list = {}
        self.cleaning_mode = None
        self.ai_policy_accepted = False
        self.go_to_zone: GoToZoneSettings = None
        self.cleanup_completed: bool = False
        self.cleanup_started: bool = False

        self.stream_status = None
        self.stream_session = None

        self.dnd_tasks = None
        self.off_peak_charging_config = None
        self.shortcuts = None

    def _get_property(self, prop: DreameMowerProperty) -> Any:
        """Helper function for accessing a property from device"""
        _LOGGER.debug("Getting property: %s", prop)
        result = self._device.get_property(prop)
        _LOGGER.debug("Result: %s", result)
        return result

    @property
    def _capability(self) -> DreameMowerDeviceCapability:
        """Helper property for accessing device capabilities"""
        return self._device.capability

    @property
    def _map_manager(self) -> DreameMapMowerMapManager | None:
        """Helper property for accessing map manager from device"""
        return self._device._map_manager

    @property
    def _device_connected(self) -> bool:
        """Helper property for accessing device connection status"""
        return self._device.device_connected

    @property
    def battery_level(self) -> int:
        """Return battery level of the device."""
        return self._get_property(DreameMowerProperty.BATTERY_LEVEL)

    @property
    def cleaning_mode_name(self) -> str:
        """Return cleaning mode as string for translation."""
        return CLEANING_MODE_CODE_TO_NAME.get(self.cleaning_mode, STATE_UNKNOWN)

    @property
    def status(self) -> DreameMowerStatus:
        """Return status of the device."""
        value = self._get_property(DreameMowerProperty.STATUS)
        if value is not None and value in DreameMowerStatus._value2member_map_:
            if self.go_to_zone and value == DreameMowerStatus.ZONE_CLEANING.value:
                return DreameMowerStatus.CRUISING_POINT
            if value == DreameMowerStatus.CHARGING.value and not self.charging:
                return DreameMowerStatus.IDLE
            return DreameMowerStatus(value)
        if value is not None:
            _LOGGER.debug("STATUS not supported: %s", value)
        return DreameMowerStatus.UNKNOWN

    @property
    def status_name(self) -> str:
        """Return status as string for translation."""
        return STATUS_CODE_TO_NAME.get(self.status, STATE_UNKNOWN)

    @property
    def task_status(self) -> DreameMowerTaskStatus:
        """Return task status of the device."""
        value = self._get_property(DreameMowerProperty.TASK_STATUS)
        if value is not None and value in DreameMowerTaskStatus._value2member_map_:
            if self.go_to_zone:
                if value == DreameMowerTaskStatus.ZONE_CLEANING.value:
                    return DreameMowerTaskStatus.CRUISING_POINT
                if value == DreameMowerTaskStatus.ZONE_CLEANING_PAUSED.value:
                    return DreameMowerTaskStatus.CRUISING_POINT_PAUSED
            return DreameMowerTaskStatus(value)
        if value is not None:
            _LOGGER.debug("TASK_STATUS not supported: %s", value)
        return DreameMowerTaskStatus.UNKNOWN

    @property
    def task_status_name(self) -> str:
        """Return task status as string for translation."""
        return TASK_STATUS_CODE_TO_NAME.get(self.task_status, STATE_UNKNOWN)

    @property
    def charging_status(self) -> DreameMowerChargingStatus:
        """Return charging status of the device."""
        value = self._get_property(DreameMowerProperty.CHARGING_STATUS)
        if value is not None and value in DreameMowerChargingStatus._value2member_map_:
            value = DreameMowerChargingStatus(value)
            # Charging status complete is not present on older firmwares
            if value is DreameMowerChargingStatus.CHARGING and self.battery_level == 100:
                return DreameMowerChargingStatus.CHARGING_COMPLETED
            return value
        if value is not None:
            _LOGGER.debug("CHARGING_STATUS not supported: %s", value)
        return DreameMowerChargingStatus.UNKNOWN

    @property
    def charging_status_name(self) -> str:
        """Return charging status as string for translation."""
        return CHARGING_STATUS_CODE_TO_NAME.get(self.charging_status, STATE_UNKNOWN)

    @property
    def relocation_status(self) -> DreameMowerRelocationStatus:
        """Return relocation status of the device."""
        value = self._get_property(DreameMowerProperty.RELOCATION_STATUS)
        if value is not None and value in DreameMowerRelocationStatus._value2member_map_:
            return DreameMowerRelocationStatus(value)
        if value is not None:
            _LOGGER.debug("RELOCATION_STATUS not supported: %s", value)
        return DreameMowerRelocationStatus.UNKNOWN

    @property
    def relocation_status_name(self) -> str:
        """Return relocation status as string for translation."""
        return RELOCATION_STATUS_CODE_TO_NAME.get(self.relocation_status, STATE_UNKNOWN)

    @property
    def state(self) -> DreameMowerState:
        """Return state of the device."""
        value = self._get_property(DreameMowerProperty.STATE)
        if (
            value is not None
            and int(value) > 18
            and not self._capability.new_state
            and value in DreameMowerStateOld._value2member_map_
        ):
            value = DreameMowerState[DreameMowerStateOld(value).name].value

        if value is not None and value in DreameMowerState._value2member_map_:
            if self.go_to_zone and (
                value == DreameMowerState.IDLE
                or value == DreameMowerState.MOWING.value
            ):
                if self.paused:
                    return DreameMowerState.MONITORING_PAUSED
                return DreameMowerState.MONITORING
            mower_state = DreameMowerState(value)

            ## Determine state as implemented on the app
            if mower_state is DreameMowerState.IDLE:
                if self.started or self.cleaning_paused or self.fast_mapping_paused:
                    return DreameMowerState.PAUSED
                elif self.docked:
                    if self.charging:
                        return DreameMowerState.CHARGING
                    ## This is for compatibility with various lovelace mower cards
                    ## Device will report idle when charging is completed and mower card will display return to dock icon even when robot is docked
                    if self.charging_status is DreameMowerChargingStatus.CHARGING_COMPLETED:
                        return DreameMowerState.CHARGING_COMPLETED
            return mower_state
        if value is not None:
            _LOGGER.debug("STATE not supported: %s", value)
        return DreameMowerState.UNKNOWN

    @property
    def state_name(self) -> str:
        """Return state as string for translation."""
        return STATE_CODE_TO_STATE.get(self.state, STATE_UNKNOWN)

    @property
    def stream_status_name(self) -> str:
        """Return camera stream status as string for translation."""
        return STREAM_STATUS_TO_NAME.get(self.stream_status, STATE_UNKNOWN)

    @property
    def wider_corner_coverage(self) -> DreameMowerWiderCornerCoverage:
        value = self._device.get_auto_switch_property(DreameMowerAutoSwitchProperty.WIDER_CORNER_COVERAGE)
        if value is not None and value < 0:
            value = 0
        if value is not None and value in DreameMowerWiderCornerCoverage._value2member_map_:
            return DreameMowerWiderCornerCoverage(value)
        if value is not None:
            _LOGGER.debug("WIDER_CORNER_COVERAGE not supported: %s", value)
        return DreameMowerWiderCornerCoverage.UNKNOWN

    @property
    def wider_corner_coverage_name(self) -> str:
        """Return wider corner coverage as string for translation."""
        wider_corner_coverage = 0 if self.wider_corner_coverage < 0 else self.wider_corner_coverage
        if (
            wider_corner_coverage is not None
            and wider_corner_coverage in DreameMowerWiderCornerCoverage._value2member_map_
        ):
            return WIDER_CORNER_COVERAGE_TO_NAME.get(
                DreameMowerWiderCornerCoverage(wider_corner_coverage), STATE_UNKNOWN
            )
        return STATE_UNKNOWN

    @property
    def cleaning_route(self) -> DreameMowerCleaningRoute:
        if self._capability.cleaning_route:
            value = self._device.get_auto_switch_property(DreameMowerAutoSwitchProperty.CLEANING_ROUTE)
            if value is not None and value < 0:
                value = 0
            if value is not None and value in DreameMowerCleaningRoute._value2member_map_:
                return DreameMowerCleaningRoute(value)
            if value is not None:
                _LOGGER.debug("CLEANING_ROUTE not supported: %s", value)
            return DreameMowerCleaningRoute.UNKNOWN

    @property
    def cleaning_route_name(self) -> str:
        """Return cleaning route as string for translation."""
        cleaning_route = 0 if self.cleaning_route < 0 else self.cleaning_route
        if cleaning_route is not None and cleaning_route in DreameMowerCleaningRoute._value2member_map_:
            return CLEANING_ROUTE_TO_NAME.get(DreameMowerCleaningRoute(cleaning_route), STATE_UNKNOWN)
        return STATE_UNKNOWN

    @property
    def cleangenius(self) -> DreameMowerCleanGenius:
        if self._capability.cleangenius:
            value = self._device.get_auto_switch_property(DreameMowerAutoSwitchProperty.CLEANGENIUS)
            if value is not None and value < 0:
                value = 0
            if value is not None and value in DreameMowerCleanGenius._value2member_map_:
                return DreameMowerCleanGenius(value)
            if value is not None:
                _LOGGER.debug("CLEANGENIUS not supported: %s", value)
        return DreameMowerCleanGenius.UNKNOWN

    @property
    def cleangenius_name(self) -> str:
        """Return CleanGenius as string for translation."""
        cleangenius = 0 if not self.cleangenius or self.cleangenius < 0 else self.cleangenius
        if cleangenius is not None and cleangenius in DreameMowerCleanGenius._value2member_map_:
            return CLEANGENIUS_TO_NAME.get(DreameMowerCleanGenius(cleangenius), STATE_UNKNOWN)
        return STATE_UNKNOWN

    @property
    def voice_assistant_language(self) -> DreameMowerVoiceAssistantLanguage:
        """Return voice assistant language of the device."""
        value = self._get_property(DreameMowerProperty.VOICE_ASSISTANT_LANGUAGE)
        if value is not None and value in DreameMowerVoiceAssistantLanguage._value2member_map_:
            return DreameMowerVoiceAssistantLanguage(value)
        if value is not None:
            _LOGGER.debug("VOICE_ASSISTANT_LANGUAGE not supported: %s", value)
        return DreameMowerVoiceAssistantLanguage.DEFAULT

    @property
    def voice_assistant_language_name(self) -> str:
        """Return voice assistant language as string for translation."""
        return VOICE_ASSISTANT_LANGUAGE_TO_NAME.get(self.voice_assistant_language, STATE_UNKNOWN)

    @property
    def task_type(self) -> DreameMowerTaskType:
        """Return drainage status of the device."""
        value = self._get_property(DreameMowerProperty.TASK_TYPE)
        if value is not None and value in DreameMowerTaskType._value2member_map_:
            return DreameMowerTaskType(value)
        if value is not None:
            _LOGGER.debug("TASK_TYPE not supported: %s", value)
        return DreameMowerTaskType.UNKNOWN

    @property
    def task_type_name(self) -> str:
        """Return drainage status as string for translation."""
        return TASK_TYPE_TO_NAME.get(self.task_type, STATE_UNKNOWN)

    @property
    def faults(self) -> str:
        faults = self._get_property(DreameMowerProperty.FAULTS)
        return 0 if faults == "" or faults == " " else faults

    @property
    def device_code(self) -> int | None:
        """Return the raw mower device code from property 2.2."""
        value = self._get_property(DreameMowerProperty.ERROR)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            _LOGGER.debug("DEVICE_CODE not numeric: %s", value)
            return None

    @property
    def error(self) -> int | None:
        """Deprecated raw alias for :attr:`device_code`."""
        return self.device_code

    @property
    def error_name(self) -> str:
        """Return a mower-native device-code name."""
        value = self.device_code
        if value in (None, -1):
            return "no_error"
        return mower_device_code_name(value, model=self._device_model) or STATE_UNKNOWN

    @property
    def error_description(self) -> str:
        """Return a mower-native device-code description."""
        name = self.error_name
        return [name.replace("_", " ").capitalize(), ""] if name else [STATE_UNKNOWN, ""]

    @property
    def error_image(self) -> str:
        """Return no image; bundled images belong to vacuum fault meanings."""
        return None

    @property
    def robot_status(self) -> int:  # TODO: Convert to enum
        """Device status for robot icon rendering."""
        value = 0
        if self.running and not self.returning and not self.fast_mapping and not self.cruising:
            value = 1
        elif self.charging:
            value = 2
        elif self.sleeping:
            value = 3
        if self.has_error:
            value += 10
        return value

    @property
    def has_error(self) -> bool:
        """Return whether the mower-native code is a hard fault."""
        value = self.device_code
        definition = mower_device_code_definition(value, model=self._device_model)
        if definition is not None:
            return definition.tier is MowerDeviceCodeTier.ERROR
        if value in (None, -1):
            return False
        return self.state is DreameMowerState.ERROR

    @property
    def has_warning(self) -> bool:
        """Return whether the mower-native code is an alert/attention item."""
        value = self.device_code
        definition = mower_device_code_definition(value, model=self._device_model)
        return bool(
            definition is not None
            and definition.tier
            in {MowerDeviceCodeTier.ALERT, MowerDeviceCodeTier.ATTENTION}
        )

    @property
    def _device_model(self) -> str | None:
        """Return the current cloud model used for device-code overrides."""
        return getattr(self._device.info, "model", None) if self._device.info else None

    @property
    def scheduled_clean(self) -> bool:
        if self.started:
            value = self._get_property(DreameMowerProperty.SCHEDULED_CLEAN)
            return bool(value == 1 or value == 2 or value == 4)
        return False

    @property
    def camera_light_brightness(self) -> int:
        if self._capability.camera_streaming:
            brightness = self._get_property(DreameMowerProperty.CAMERA_LIGHT_BRIGHTNESS)
            if brightness and str(brightness).isnumeric():
                return int(brightness)

    @property
    def dnd_remaining(self) -> bool:
        """Returns remaining seconds to DND period to end."""
        if self.dnd:
            dnd_start = self.dnd_start
            dnd_end = self.dnd_end
            if dnd_start and dnd_end:
                end_time = dnd_end.split(":")
                if len(end_time) == 2:
                    now = datetime.now()
                    hour = now.hour
                    minute = now.minute
                    if minute < 10:
                        minute = f"0{minute}"

                    time = int(f"{hour}{minute}")
                    start = int(dnd_start.replace(":", ""))
                    end = int(dnd_end.replace(":", ""))
                    current_seconds = hour * 3600 + int(minute) * 60
                    end_seconds = int(end_time[0]) * 3600 + int(end_time[1]) * 60

                    if (
                        start < end
                        and start < time
                        and time < end
                        or end < start
                        and (2400 > time and time > start or end > time and time > 0)
                        or time == start
                        or time == end
                    ):
                        return (
                            (end_seconds + 86400 - current_seconds)
                            if current_seconds > end_seconds
                            else (end_seconds - current_seconds)
                        )
                return 0
        return None

    @property
    def located(self) -> bool:
        """Returns true when robot knows its position on current map."""
        relocation_status = self.relocation_status
        return bool(
            relocation_status is DreameMowerRelocationStatus.LOCATED
            or relocation_status is DreameMowerRelocationStatus.UNKNOWN
            or self.fast_mapping
        )

    @property
    def sweeping(self) -> bool:
        """Returns true when cleaning mode is sweeping."""
        cleaning_mode = self.cleaning_mode
        return 1

    @property
    def zone_cleaning(self) -> bool:
        """Returns true when device is currently performing a zone cleaning task."""
        task_status = self.task_status
        return bool(
            self._device_connected
            and self.started
            and (
                task_status is DreameMowerTaskStatus.ZONE_CLEANING
                or task_status is DreameMowerTaskStatus.ZONE_CLEANING_PAUSED
                or task_status is DreameMowerTaskStatus.ZONE_DOCKING_PAUSED
            )
        )

    @property
    def spot_cleaning(self) -> bool:
        """Returns true when device is currently performing a spot cleaning task."""
        task_status = self.task_status
        return bool(
            self._device_connected
            and self.started
            and (
                task_status is DreameMowerTaskStatus.SPOT_CLEANING
                or task_status is DreameMowerTaskStatus.SPOT_CLEANING_PAUSED
                or self.status is DreameMowerStatus.SPOT_CLEANING
            )
        )

    @property
    def segment_cleaning(self) -> bool:
        """Returns true when device is currently performing a custom segment cleaning task."""
        task_status = self.task_status
        return bool(
            self._device_connected
            and self.started
            and (
                task_status is DreameMowerTaskStatus.SEGMENT_CLEANING
                or task_status is DreameMowerTaskStatus.SEGMENT_CLEANING_PAUSED
                or task_status is DreameMowerTaskStatus.SEGMENT_DOCKING_PAUSED
            )
        )

    @property
    def auto_cleaning(self) -> bool:
        """Returns true when device is currently performing a complete map cleaning task."""
        task_status = self.task_status
        return bool(
            self._device_connected
            and self.started
            and (
                task_status is DreameMowerTaskStatus.AUTO_CLEANING
                or task_status is DreameMowerTaskStatus.AUTO_CLEANING_PAUSED
                or task_status is DreameMowerTaskStatus.AUTO_DOCKING_PAUSED
            )
        )

    @property
    def fast_mapping(self) -> bool:
        """Returns true when device is creating a new map."""
        return bool(
            self._device_connected
            and (
                self.task_status is DreameMowerTaskStatus.FAST_MAPPING
                or self.status is DreameMowerStatus.FAST_MAPPING
                or self.fast_mapping_paused
            )
        )

    @property
    def fast_mapping_paused(self) -> bool:
        """Returns true when creating a new map paused by user.
        Used for resuming fast cleaning on start because standard start action can not be used for resuming fast mapping.
        """

        state = self._get_property(DreameMowerProperty.STATE)
        task_status = self.task_status
        return bool(
            (
                task_status is DreameMowerTaskStatus.FAST_MAPPING
                or task_status is DreameMowerTaskStatus.MAP_CLEANING_PAUSED
            )
            and (
                state == DreameMowerState.PAUSED.value
                or state == DreameMowerState.ERROR.value
                or state == DreameMowerState.IDLE.value
            )
        )

    @property
    def cruising(self) -> bool:
        """Returns true when device is cruising."""
        if self._capability.cruising:
            task_status = self.task_status
            status = self.status
            return bool(
                task_status is DreameMowerTaskStatus.CRUISING_PATH
                or task_status is DreameMowerTaskStatus.CRUISING_POINT
                or task_status is DreameMowerTaskStatus.CRUISING_PATH_PAUSED
                or task_status is DreameMowerTaskStatus.CRUISING_POINT_PAUSED
                or status is DreameMowerStatus.CRUISING_PATH
                or status is DreameMowerStatus.CRUISING_POINT
            )
        return bool(self.go_to_zone)

    @property
    def cruising_paused(self) -> bool:
        """Returns true when cruising paused."""
        if self._capability.cruising:
            task_status = self.task_status
            return bool(
                task_status is DreameMowerTaskStatus.CRUISING_PATH_PAUSED
                or task_status is DreameMowerTaskStatus.CRUISING_POINT_PAUSED
            )
        if self.go_to_zone:
            status = self.status
            if self.started and (
                status is DreameMowerStatus.PAUSED
                or status is DreameMowerStatus.SLEEPING
                or status is DreameMowerStatus.IDLE
                or status is DreameMowerStatus.STANDBY
            ):
                return True
        return False

    @property
    def resume_cleaning(self) -> bool:
        """Returns true when resume_cleaning is enabled."""
        return bool(
            self._get_property(DreameMowerProperty.RESUME_CLEANING) == (2 if self._capability.auto_charging else 1)
        )

    @property
    def cleaning_paused(self) -> bool:
        """Returns true when device battery is too low for resuming its task and needs to be charged before continuing."""
        return bool(self._get_property(DreameMowerProperty.CLEANING_PAUSED))

    @property
    def charging(self) -> bool:
        """Returns true when device is currently charging."""
        return bool(self.charging_status is DreameMowerChargingStatus.CHARGING)

    @property
    def docked(self) -> bool:
        """Returns true when device is docked."""
        return bool(
            (
                self.charging
                or self.charging_status is DreameMowerChargingStatus.CHARGING_COMPLETED
            )
            and not (self.running and not self.returning and not self.fast_mapping and not self.cruising)
        )

    @property
    def sleeping(self) -> bool:
        """Returns true when device is sleeping."""
        return bool(self.status is DreameMowerStatus.SLEEPING)

    @property
    def returning_paused(self) -> bool:
        """Returns true when returning to dock is paused."""
        task_status = self.task_status
        return bool(
            self._device_connected
            and task_status is DreameMowerTaskStatus.DOCKING_PAUSED
            or task_status is DreameMowerTaskStatus.AUTO_DOCKING_PAUSED
            or task_status is DreameMowerTaskStatus.SEGMENT_DOCKING_PAUSED
            or task_status is DreameMowerTaskStatus.ZONE_DOCKING_PAUSED
        )

    @property
    def returning(self) -> bool:
        """Returns true when returning to dock for charging."""
        return bool(self._device_connected and (self.status is DreameMowerStatus.BACK_HOME))

    @property
    def started(self) -> bool:
        """Returns true when device has an active task.
        Used for preventing updates on settings that relates to currently performing task.
        """
        status = self.status
        task_status = self.task_status
        return bool(
            (
                task_status is not DreameMowerTaskStatus.UNKNOWN
                and task_status is not DreameMowerTaskStatus.COMPLETED
                and task_status is not DreameMowerTaskStatus.DOCKING_PAUSED
            )
            or self.cleaning_paused
            or status in _ACTIVE_TASK_STATUSES
        )

    @property
    def paused(self) -> bool:
        """Returns true when device has an active paused task."""
        status = self.status
        return bool(
            self.cleaning_paused
            or self.cruising_paused
            or (
                self.started
                and (
                    status is DreameMowerStatus.PAUSED
                    or status is DreameMowerStatus.SLEEPING
                    or status is DreameMowerStatus.IDLE
                    or status is DreameMowerStatus.STANDBY
                )
            )
        )

    @property
    def active(self) -> bool:
        """Returns true when device is moving or not sleeping."""
        return self.status is DreameMowerStatus.STANDBY or self.running

    @property
    def running(self) -> bool:
        """Returns true when device is moving."""
        status = self.status
        return bool(
            not (
                self.charging
                or self.charging_status is DreameMowerChargingStatus.CHARGING_COMPLETED
            )
            and status in _RUNNING_STATUSES
        )

    @property
    def shortcut_task(self) -> bool:
        """Returns true when device has an active shortcut task."""
        if self.started and self.shortcuts:
            for k, v in self.shortcuts.items():
                if v.running:
                    return True
        return False

    @property
    def customized_cleaning(self) -> bool:
        """Returns true when customized cleaning feature is enabled."""
        return bool(
            self._get_property(DreameMowerProperty.CUSTOMIZED_CLEANING)
            and self.has_saved_map
            and not self.cleangenius_cleaning
        )

    @property
    def cleangenius_cleaning(self) -> bool:
        """Returns true when CleanGenius feature is enabled."""
        return bool(
            self._capability.cleangenius
            and self._get_property(DreameMowerAutoSwitchProperty.CLEANGENIUS)
            and not self.zone_cleaning
            and not self.spot_cleaning
        )

    @property
    def max_suction_power(self) -> bool:
        """Returns true when max suction power feature is enabled."""
        return bool(
            self._capability.max_suction_power and self._get_property(DreameMowerAutoSwitchProperty.MAX_SUCTION_POWER)
        )

    @property
    def multi_map(self) -> bool:
        """Returns true when multi floor map feature is enabled."""
        return bool(self._get_property(DreameMowerProperty.MULTI_FLOOR_MAP))

    @property
    def last_cleaning_time(self) -> datetime | None:
        if self._cleaning_history:
            return self._last_cleaning_time

    @property
    def last_cruising_time(self) -> datetime | None:
        if self._cruising_history:
            return self._last_cruising_time

    @property
    def cleaning_history(self) -> dict[str, Any] | None:
        """Returns the cleaning history list as dict."""
        if self._cleaning_history:
            if self._cleaning_history_attrs is None:
                list = {}
                for history in self._cleaning_history:
                    date = time.strftime("%m-%d %H:%M", time.localtime(history.date.timestamp()))
                    list[date] = {
                        ATTR_TIMESTAMP: history.date.timestamp(),
                        ATTR_CLEANING_TIME: f"{history.cleaning_time} min",
                        ATTR_CLEANED_AREA: f"{history.cleaned_area} m²",
                    }
                    if history.status is not None:
                        list[date][ATTR_STATUS] = (
                            STATUS_CODE_TO_NAME.get(history.status, STATE_UNKNOWN).replace("_", " ").capitalize()
                        )
                    if history.completed is not None:
                        list[date][ATTR_COMPLETED] = history.completed
                    if history.neglected_segments:
                        list[date][ATTR_NEGLECTED_SEGMENTS] = {
                            k: v.name.replace("_", " ").capitalize() for k, v in history.neglected_segments.items()
                        }
                    if history.cleanup_method is not None:
                        list[date][ATTR_CLEANUP_METHOD] = history.cleanup_method.name.replace("_", " ").capitalize()
                    if history.task_interrupt_reason is not None:
                        list[date][ATTR_INTERRUPT_REASON] = history.task_interrupt_reason.name.replace(
                            "_", " "
                        ).capitalize()
                self._cleaning_history_attrs = list
            return self._cleaning_history_attrs

    @property
    def cruising_history(self) -> dict[str, Any] | None:
        """Returns the cruising history list as dict."""
        if self._cruising_history:
            if self._cruising_history_attrs is None:
                list = {}
                for history in self._cruising_history:
                    date = time.strftime("%m-%d %H:%M", time.localtime(history.date.timestamp()))
                    list[date] = {
                        ATTR_CRUISING_TIME: f"{history.cleaning_time} min",
                    }
                    if history.status is not None:
                        list[date][ATTR_STATUS] = (
                            STATUS_CODE_TO_NAME.get(history.status, STATE_UNKNOWN).replace("_", " ").capitalize()
                        )
                    if history.cruise_type is not None:
                        list[date][ATTR_CRUISING_TYPE] = history.cruise_type
                    if history.map_index is not None:
                        list[date][ATTR_MAP_INDEX] = history.map_index
                    if history.map_name is not None and len(history.map_name) > 1:
                        list[date][ATTR_MAP_NAME] = history.map_name
                    if history.completed is not None:
                        list[date][ATTR_COMPLETED] = history.completed
                self._cruising_history_attrs = list
            return self._cruising_history_attrs

    @property
    def maximum_maps(self) -> int:
        return (
            1 if not self._capability.lidar_navigation or not self.multi_map else 4 if self._capability.wifi_map else 3
        )

    @property
    def mapping_available(self) -> bool:
        """Returns true when creating a new map is possible."""
        return bool(
            not self.started
            and not self.fast_mapping
            and (not self._device.capability.map or self.maximum_maps > len(self.map_list))
        )

    @property
    def second_cleaning_available(self) -> bool:
        if self._cleaning_history and self.current_map:
            history = self._cleaning_history[0]
            if history.object_name:
                map_data = self._history_map_data.get(history.object_name)
                return bool(
                    (map_data is not None and self.current_map.map_id == map_data.map_id)
                    and (
                        bool(history.neglected_segments)
                        or bool(
                            history.cleanup_method.value == 2
                            and map_data.cleaned_segments
                            and map_data.cleaning_map_data is not None
                            and map_data.cleaning_map_data.has_dirty_area
                        )
                    )
                )
        return False

    @property
    def blades_life(self) -> int:
        """Returns blade remaining life in percent."""
        return self._get_property(DreameMowerProperty.BLADES_LEFT)

    @property
    def side_brush_life(self) -> int:
        """Returns side brush remaining life in percent."""
        return self._get_property(DreameMowerProperty.SIDE_BRUSH_LEFT)

    @property
    def filter_life(self) -> int:
        """Returns filter remaining life in percent."""
        return self._get_property(DreameMowerProperty.FILTER_LEFT)

    @property
    def sensor_dirty_life(self) -> int:
        """Returns sensor clean remaining time in percent."""
        return self._get_property(DreameMowerProperty.SENSOR_DIRTY_LEFT)

    @property
    def tank_filter_life(self) -> int:
        """Returns tank filter remaining life in percent."""
        return self._get_property(DreameMowerProperty.TANK_FILTER_LEFT)

    @property
    def silver_ion_life(self) -> int:
        """Returns silver-ion life in percent."""
        return self._get_property(DreameMowerProperty.SILVER_ION_LEFT)

    @property
    def lensbrush_life(self) -> int:
        """Returns lensbrush life in percent."""
        return 30000 - self._get_property(DreameMowerProperty.LENSBRUSH_LEFT)['CMS'][0]

    @property
    def squeegee_life(self) -> int:
        """Returns squeegee life in percent."""
        return self._get_property(DreameMowerProperty.SQUEEGEE_LEFT)

    @property
    def dnd(self) -> bool | None:
        """Returns DND is enabled."""
        if self._capability.dnd:
            return (
                bool(self._get_property(DreameMowerProperty.DND))
                if not self._capability.dnd_task
                else self.dnd_tasks[0].get("en") if self.dnd_tasks and len(self.dnd_tasks) else False
            )

    @property
    def dnd_start(self) -> str | None:
        """Returns DND start time."""
        if self._capability.dnd:
            return (
                self._get_property(DreameMowerProperty.DND_START)
                if not self._capability.dnd_task
                else self.dnd_tasks[0].get("st") if self.dnd_tasks and len(self.dnd_tasks) else "22:00"
            )

    @property
    def dnd_end(self) -> str | None:
        """Returns DND end time."""
        if self._capability.dnd:
            return (
                self._get_property(DreameMowerProperty.DND_END)
                if not self._capability.dnd_task
                else self.dnd_tasks[0].get("et") if self.dnd_tasks and len(self.dnd_tasks) else "08:00"
            )

    @property
    def off_peak_charging(self) -> bool | None:
        """Returns Off-Peak charging is enabled."""
        if self._capability.off_peak_charging:
            return bool(
                self._capability.off_peak_charging
                and len(self.off_peak_charging_config)
                and self.off_peak_charging_config.get("enable")
            )

    @property
    def off_peak_charging_start(self) -> str | None:
        """Returns Off-Peak charging start time."""
        if self._capability.off_peak_charging:
            return (
                self.off_peak_charging_config.get("startTime")
                if self.off_peak_charging_config and len(self.off_peak_charging_config)
                else "22:00"
            )

    @property
    def off_peak_charging_end(self) -> str | None:
        """Returns Off-Peak charging end time."""
        if self._capability.off_peak_charging:
            return (
                self.off_peak_charging_config.get("endTime")
                if self.off_peak_charging_config and len(self.off_peak_charging_config)
                else "08:00"
            )

    @property
    def ai_obstacle_detection(self) -> bool:
        return self._device.get_ai_property(DreameMowerAIProperty.AI_OBSTACLE_DETECTION)

    @property
    def ai_obstacle_image_upload(self) -> bool:
        return self._device.get_ai_property(DreameMowerAIProperty.AI_OBSTACLE_IMAGE_UPLOAD)

    @property
    def ai_pet_detection(self) -> bool:
        return self._device.get_ai_property(DreameMowerAIProperty.AI_PET_DETECTION)

    @property
    def ai_furniture_detection(self) -> bool:
        return self._device.get_ai_property(DreameMowerAIProperty.AI_FURNITURE_DETECTION)

    @property
    def ai_fluid_detection(self) -> bool:
        return self._device.get_ai_property(DreameMowerAIProperty.AI_FLUID_DETECTION)

    @property
    def ai_obstacle_picture(self) -> bool:
        return self._device.get_ai_property(DreameMowerAIProperty.AI_OBSTACLE_PICTURE)

    @property
    def fill_light(self) -> bool:
        return self._device.get_auto_switch_property(DreameMowerAutoSwitchProperty.FILL_LIGHT)

    @property
    def stain_avoidance(self) -> bool:
        return bool(self._device.get_auto_switch_property(DreameMowerAutoSwitchProperty.STAIN_AVOIDANCE) == 2)

    @property
    def pet_focused_cleaning(self) -> bool:
        return self._device.get_auto_switch_property(DreameMowerAutoSwitchProperty.PET_FOCUSED_CLEANING)

    @property
    def map_backup_status(self) -> int | None:
        return self._get_property(DreameMowerProperty.MAP_BACKUP_STATUS)

    @property
    def map_recovery_status(self) -> int | None:
        return self._get_property(DreameMowerProperty.MAP_RECOVERY_STATUS)

    @property
    def custom_order(self) -> bool:
        """Returns true when custom cleaning sequence is set."""
        if self.cleangenius_cleaning:
            return False
        segments = self.current_segments
        if segments:
            for v in segments.values():
                if v.order:
                    return True
        return False

    @property
    def segment_order(self) -> list[int] | None:
        """Returns cleaning order list."""
        segments = self.current_segments
        if segments:
            return (
                list(
                    sorted(
                        segments,
                        key=lambda segment_id: segments[segment_id].order if segments[segment_id].order else 99,
                    )
                )
                if self.custom_order
                else None
            )
        return [] if self.custom_order else None

    @property
    def has_saved_map(self) -> bool:
        """Returns true when device has saved map and knowns its location on saved map."""
        if self._map_manager is None:
            return True

        current_map = self.current_map
        return bool(
            current_map is not None
            and current_map.saved_map_status == 2
            and not self.has_temporary_map
            and not self.has_new_map
            and not current_map.empty_map
        )

    @property
    def has_temporary_map(self) -> bool:
        """Returns true when device cannot store the newly created map and waits prompt for restoring or discarding it."""
        if self._map_manager is None:
            return False

        current_map = self.current_map
        return bool(current_map is not None and current_map.temporary_map and not current_map.empty_map)

    @property
    def has_new_map(self) -> bool:
        """Returns true when fast mapping from empty map."""
        if self._map_manager is None:
            return False

        current_map = self.current_map
        return bool(
            current_map is not None
            and not current_map.temporary_map
            and not current_map.empty_map
            and current_map.new_map
        )

    @property
    def selected_map(self) -> MapData | None:
        """Return the selected map data"""
        if self._map_manager and not self.has_temporary_map and not self.has_new_map:
            return self._map_manager.selected_map

    @property
    def current_map(self) -> MapData | None:
        """Return the current map data"""
        if self._map_manager:
            return self._map_manager.get_map()

    @property
    def map_list(self) -> list[int] | None:
        """Return the saved map id list if multi floor map is enabled"""
        if self._map_manager:
            if self.multi_map:
                return self._map_manager.map_list

            selected_map = self._map_manager.selected_map
            if selected_map:
                return [selected_map.map_id]
        return []

    @property
    def map_data_list(self) -> dict[int, MapData] | None:
        """Return the saved map data list if multi floor map is enabled"""
        if self._map_manager:
            if self.multi_map:
                return self._map_manager.map_data_list
            selected_map = self.selected_map
            if selected_map:
                return {selected_map.map_id: selected_map}
        return {}

    @property
    def current_segments(self) -> dict[int, Segment] | None:
        """Return the segments of current map"""
        current_map = self.current_map
        if current_map and current_map.segments and not current_map.empty_map:
            return current_map.segments
        return {}

    @property
    def segments(self) -> dict[int, Segment] | None:
        """Return the segments of selected map"""
        current_map = self.selected_map
        if current_map and current_map.segments and not current_map.empty_map:
            return current_map.segments
        return {}

    @property
    def current_zone(self) -> Segment | None:
        """Return the segment that device is currently on"""
        if self._capability.lidar_navigation:
            current_map = self.current_map
            if current_map and current_map.segments and current_map.robot_segment and not current_map.empty_map:
                return current_map.segments[current_map.robot_segment]

    @property
    def cleaning_sequence(self) -> list[int] | None:
        """Returns custom segment cleaning sequence list."""
        if self._map_manager:
            return self._map_manager.cleaning_sequence

    @property
    def previous_cleaning_sequence(self):
        if self.current_map and self.current_map.map_id in self._previous_cleaning_sequence:
            return self._previous_cleaning_sequence[self.current_map.map_id]

    @property
    def active_segments(self) -> list[int] | None:
        map_data = self.current_map
        if map_data and self.started and not self.fast_mapping:
            if self.segment_cleaning:
                if map_data.active_segments:
                    return map_data.active_segments
            elif (
                not self.zone_cleaning
                and not self.spot_cleaning
                and map_data.segments
                and not self.docked
                and not self.returning
                and not self.returning_paused
            ):
                return list(map_data.segments.keys())
            return []

    @property
    def job(self) -> dict[str, Any] | None:
        attributes = {
            ATTR_STATUS: self.status.name,
        }
        if self._device._protocol.cloud:
            attributes[ATTR_DID] = self._device._protocol.cloud.device_id
        if self._capability.custom_cleaning_mode:
            attributes[ATTR_CLEANING_MODE] = self.cleaning_mode.name

        if self.cleanup_completed:
            attributes.update(
                {
                    ATTR_CLEANED_AREA: self._get_property(DreameMowerProperty.CLEANED_AREA),
                    ATTR_CLEANING_TIME: self._get_property(DreameMowerProperty.CLEANING_TIME),
                    ATTR_COMPLETED: True,
                }
            )
        else:
            attributes[ATTR_COMPLETED] = False

        map_data = self.current_map
        if map_data:
            if map_data.active_segments:
                attributes[ATTR_ACTIVE_SEGMENTS] = map_data.active_segments
            elif map_data.active_areas is not None:
                if self.go_to_zone:
                    attributes[ATTR_ACTIVE_CRUISE_POINTS] = {
                        1: Coordinate(self.go_to_zone.x, self.go_to_zone.y, False, 0)
                    }
                else:
                    attributes[ATTR_ACTIVE_AREAS] = map_data.active_areas
            elif map_data.active_points is not None:
                attributes[ATTR_ACTIVE_POINTS] = map_data.active_points
            elif map_data.predefined_points is not None:
                attributes[ATTR_PREDEFINED_POINTS] = map_data.predefined_points
            elif map_data.active_cruise_points is not None:
                attributes[ATTR_ACTIVE_CRUISE_POINTS] = map_data.active_cruise_points
        return attributes

    @property
    def attributes(self) -> dict[str, Any] | None:
        """Return the attributes of the device."""
        properties = [
            DreameMowerProperty.STATUS,
            DreameMowerProperty.CLEANING_MODE,
            DreameMowerProperty.ERROR,
            DreameMowerProperty.CLEANING_TIME,
            DreameMowerProperty.CLEANED_AREA,
            DreameMowerProperty.VOICE_PACKET_ID,
            DreameMowerProperty.TIMEZONE,
            DreameMowerProperty.BLADES_TIME_LEFT,
            DreameMowerProperty.BLADES_LEFT,
            DreameMowerProperty.SIDE_BRUSH_TIME_LEFT,
            DreameMowerProperty.SIDE_BRUSH_LEFT,
            DreameMowerProperty.FILTER_LEFT,
            DreameMowerProperty.FILTER_TIME_LEFT,
            DreameMowerProperty.TANK_FILTER_LEFT,
            DreameMowerProperty.TANK_FILTER_TIME_LEFT,
            DreameMowerProperty.SILVER_ION_LEFT,
            DreameMowerProperty.SILVER_ION_TIME_LEFT,
            DreameMowerProperty.LENSBRUSH_LEFT,
            DreameMowerProperty.LENSBRUSH_TIME_LEFT,
            DreameMowerProperty.SQUEEGEE_LEFT,
            DreameMowerProperty.SQUEEGEE_TIME_LEFT,
            DreameMowerProperty.TOTAL_CLEANED_AREA,
            DreameMowerProperty.TOTAL_CLEANING_TIME,
            DreameMowerProperty.CLEANING_COUNT,
            DreameMowerProperty.CUSTOMIZED_CLEANING,
            DreameMowerProperty.SERIAL_NUMBER,
            DreameMowerProperty.NATION_MATCHED,
            DreameMowerProperty.TOTAL_RUNTIME,
            DreameMowerProperty.TOTAL_CRUISE_TIME,
            DreameMowerProperty.CLEANING_PROGRESS,
            DreameMowerProperty.INTELLIGENT_RECOGNITION,
            DreameMowerProperty.MULTI_FLOOR_MAP,
            DreameMowerProperty.SCHEDULED_CLEAN,
            DreameMowerProperty.VOICE_ASSISTANT_LANGUAGE,
        ]

        if not self._capability.disable_sensor_cleaning:
            properties.extend(
                [
                    DreameMowerProperty.SENSOR_DIRTY_LEFT,
                    DreameMowerProperty.SENSOR_DIRTY_TIME_LEFT,
                ]
            )

        if not self._capability.dnd_task:
            properties.extend(
                [
                    DreameMowerProperty.DND_START,
                    DreameMowerProperty.DND_END,
                ]
            )

        attributes = {}

        for prop in properties:
            value = self._get_property(prop)
            if value is not None:
                prop_name = PROPERTY_TO_NAME.get(prop.name)
                if prop_name:
                    prop_name = prop_name[0]
                else:
                    prop_name = prop.name.lower()

                if prop is DreameMowerProperty.ERROR:
                    value = self.error_name.replace("_", " ").capitalize()
                elif prop is DreameMowerProperty.STATUS:
                    value = self.status_name.replace("_", " ").capitalize()
                elif prop is DreameMowerProperty.CLEANING_MODE:
                    value = self.cleaning_mode_name.replace("_", " ").capitalize()
                    attributes[f"{prop_name}_list"] = (
                        [v.replace("_", " ").capitalize() for v in self.cleaning_mode_list.keys()]
                        if PROPERTY_AVAILABILITY[prop.name](self._device)
                        else []
                    )
                elif prop is DreameMowerProperty.VOICE_ASSISTANT_LANGUAGE:
                    if not self._capability.voice_assistant:
                        continue
                    value = self.voice_assistant_language_name.replace("_", " ").capitalize()
                    attributes[f"{prop_name}_list"] = [
                        v.replace("_", " ").capitalize() for v in self.voice_assistant_language_list.keys()
                    ]
                elif prop is DreameMowerAutoSwitchProperty.CLEANING_ROUTE:
                    value = self.cleaning_route_name.replace("_", " ").capitalize()
                    attributes[f"{prop_name}_list"] = (
                        [v.replace("_", " ").capitalize() for v in self.cleaning_route_list.keys()]
                        if PROPERTY_AVAILABILITY[prop.name](self._device)
                        else []
                    )
                elif prop is DreameMowerAutoSwitchProperty.CLEANGENIUS:
                    value = self.cleangenius_name.replace("_", " ").capitalize()
                    attributes[f"{prop_name}_list"] = (
                        [v.replace("_", " ").capitalize() for v in self.cleangenius_list.keys()]
                        if PROPERTY_AVAILABILITY[prop.name](self._device)
                        else []
                    )
                elif prop is DreameMowerProperty.CUSTOMIZED_CLEANING:
                    value = value and not self.zone_cleaning and not self.spot_cleaning
                elif prop is DreameMowerProperty.SCHEDULED_CLEAN:
                    value = bool(value == 1 or value == 2 or value == 4)
                elif (
                    prop is DreameMowerProperty.MULTI_FLOOR_MAP
                    or prop is DreameMowerProperty.INTELLIGENT_RECOGNITION
                ):
                    value = bool(value > 0)
                attributes[prop_name] = value

        if self._capability.dnd_task and self.dnd_tasks is not None:
            attributes[ATTR_DND] = {}
            for dnd_task in self.dnd_tasks:
                attributes[ATTR_DND][dnd_task["id"]] = {
                    "enabled": dnd_task.get("en"),
                    "start": dnd_task.get("st"),
                    "end": dnd_task.get("et"),
                }
        if self._capability.shortcuts and self.shortcuts is not None:
            attributes[ATTR_SHORTCUTS] = {}
            for id, shortcut in self.shortcuts.items():
                attributes[ATTR_SHORTCUTS][id] = {
                    "name": shortcut.name,
                    "running": shortcut.running,
                    "tasks": shortcut.tasks,
                }

        attributes[ATTR_CLEANING_SEQUENCE] = self.segment_order
        attributes[ATTR_CHARGING] = self.docked
        attributes[ATTR_STARTED] = self.started
        attributes[ATTR_PAUSED] = self.paused
        attributes[ATTR_RUNNING] = self.running
        attributes[ATTR_RETURNING_PAUSED] = self.returning_paused
        attributes[ATTR_RETURNING] = self.returning
        attributes[ATTR_SEGMENT_CLEANING] = self.segment_cleaning
        attributes[ATTR_ZONE_CLEANING] = self.zone_cleaning
        attributes[ATTR_SPOT_CLEANING] = self.spot_cleaning
        attributes[ATTR_CRUSING] = self.cruising
        attributes[ATTR_MOWER_STATE] = self.state_name.lower()
        attributes[ATTR_HAS_SAVED_MAP] = self._map_manager is not None and self.has_saved_map
        attributes[ATTR_HAS_TEMPORARY_MAP] = self.has_temporary_map

        if self._capability.lidar_navigation:
            attributes[ATTR_MAPPING] = self.fast_mapping
            attributes[ATTR_MAPPING_AVAILABLE] = self.mapping_available

        if self._capability.cleangenius:
            attributes[ATTR_CLEANGENIUS] = bool(self.cleangenius_cleaning)

        if self.map_list:
            attributes[ATTR_ACTIVE_SEGMENTS] = self.active_segments
            if self._capability.lidar_navigation:
                attributes[ATTR_CURRENT_SEGMENT] = self.current_zone.segment_id if self.current_zone else 0
            attributes[ATTR_SELECTED_MAP] = self.selected_map.map_name if self.selected_map else None
            attributes[ATTR_ZONES] = {}
            for k, v in self.map_data_list.items():
                attributes[ATTR_ZONES][v.map_name] = [
                    {ATTR_ID: j, ATTR_NAME: s.name, ATTR_ICON: s.icon} for (j, s) in sorted(v.segments.items())
                ]
        attributes[ATTR_CAPABILITIES] = self._capability.list
        return attributes

    def consumable_life_warning_description(self, consumable_property) -> str:
        description = CONSUMABLE_TO_LIFE_WARNING_DESCRIPTION.get(consumable_property)
        if description:
            value = self._get_property(consumable_property)
            if value is not None and value >= 0 and value <= 5:
                if value != 0 and len(description) > 1:
                    return description[1]
                return description[0]

    def segment_order_list(self, segment) -> list[int] | None:
        order = []
        if self.current_segments:
            order = [
                v.order
                for k, v in sorted(
                    self.current_segments.items(),
                    key=lambda s: s[1].order if s[1].order != None else 0,
                )
                if v.order
            ]
            if not segment.order and len(order):
                order = order + [max(order) + 1]
        return list(map(str, order))
