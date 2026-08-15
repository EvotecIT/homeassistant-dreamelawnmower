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



from .device_commands import _DreameMowerDeviceCommandMixin
from .device_info import DreameMowerDeviceInfo
from .device_map import _DreameMowerDeviceMapMixin
from .device_state import _DreameMowerDeviceStateMixin
from .device_status import DreameMowerDeviceStatus

class DreameMowerDevice(
    _DreameMowerDeviceStateMixin,
    _DreameMowerDeviceCommandMixin,
    _DreameMowerDeviceMapMixin,
):
    """Support for Dreame Mower"""

    property_mapping: dict[DreameMowerProperty, dict[str, int]] = DreameMowerPropertyMapping
    action_mapping: dict[DreameMowerAction, dict[str, int]] = DreameMowerActionMapping

    def __init__(
        self,
        name: str,
        host: str,
        token: str,
        mac: str = None,
        username: str = None,
        password: str = None,
        country: str = None,
        prefer_cloud: bool = True,
        account_type: str = "dreame",
        device_id: str = None,
    ) -> None:
        # Used for easy filtering the device from cloud device list and generating unique ids
        self.info = None
        self.mac: str = None
        self.token: str = None  # Local api token
        self.host: str = None  # IP address or host name of the device
        # Dictionary for storing the current property values
        self.data: dict[DreameMowerProperty, Any] = {}
        self.unknown_properties: dict[int, dict[str, Any]] = {}
        self.realtime_properties: dict[str, dict[str, Any]] = {}
        self.last_realtime_message: dict[str, Any] | None = None
        self._state_lock = RLock()
        self.auto_switch_data: dict[DreameMowerAutoSwitchProperty, Any] = None
        self.ai_data: dict[DreameMowerStrAIProperty | DreameMowerAIProperty, Any] = None
        self.available: bool = False  # Last update is successful or not
        self.disconnected: bool = False

        self._update_running: bool = False  # Update is running
        self._previous_cleaning_mode: DreameMowerCleaningMode = None
        # Device do not request properties that returned -1 as result. This property used for overriding that behavior at first connection
        self._ready: bool = False
        # Last settings properties requested time
        self._last_settings_request: float = 0
        self._last_map_list_request: float = 0  # Last map list property requested time
        self._last_map_request: float = 0  # Last map request trigger time
        self._last_change: float = 0  # Last property change time
        self._last_update_failed: float = 0  # Last update failed time
        self._cleaning_history_update: float = 0  # Cleaning history update time
        self._update_fail_count: int = 0  # Update failed counter
        self._map_select_time: float = None
        # Map Manager object. Only available when cloud connection is present
        self._map_manager: DreameMapMowerMapManager = None
        self._update_callback = None  # External update callback for device
        self._error_callback = None  # External update failed callback
        # External update callbacks for specific device property
        self._property_update_callback = {}
        self._update_timer: Timer = None  # Update schedule timer
        # Used for requesting consumable properties after reset action otherwise they will only requested when cleaning completed
        self._consumable_change: bool = False
        self._remote_control: bool = False
        self._dirty_data: dict[DreameMowerProperty, DirtyData] = {}
        self._dirty_auto_switch_data: dict[DreameMowerAutoSwitchProperty, DirtyData] = {}
        self._dirty_ai_data: dict[DreameMowerStrAIProperty | DreameMowerAIProperty, Any] = None
        self._discard_timeout = 5
        self._restore_timeout = 15

        self._name = name
        self.mac = mac
        self.token = token
        self.host = host
        self.two_factor_url = None
        self.account_type = account_type
        self.status = DreameMowerDeviceStatus(self)
        self.capability = DreameMowerDeviceCapability(self)

        # Remove write only and response only properties from default list
        self._default_properties = [
            DreameMowerProperty.STATE,
            DreameMowerProperty.ERROR,
            DreameMowerProperty.BATTERY_LEVEL,
            DreameMowerProperty.CHARGING_STATUS,
            DreameMowerProperty.STATUS,
        ]
        self._discarded_properties = [
            DreameMowerProperty.ERROR,
            DreameMowerProperty.STATE,
            DreameMowerProperty.STATUS,
            DreameMowerProperty.TASK_STATUS,
            DreameMowerProperty.ERROR,
            DreameMowerProperty.AUTO_SWITCH_SETTINGS,
            DreameMowerProperty.CAMERA_LIGHT_BRIGHTNESS,
            DreameMowerProperty.AI_DETECTION,
            DreameMowerProperty.SHORTCUTS,
            DreameMowerProperty.MAP_BACKUP_STATUS,
            DreameMowerProperty.MAP_RECOVERY_STATUS,
            DreameMowerProperty.OFF_PEAK_CHARGING,
        ]
        self._read_write_properties = [
            DreameMowerProperty.RESUME_CLEANING,
            DreameMowerProperty.OBSTACLE_AVOIDANCE,
            DreameMowerProperty.AI_DETECTION,
            DreameMowerProperty.CLEANING_MODE,
            DreameMowerProperty.INTELLIGENT_RECOGNITION,
            DreameMowerProperty.CUSTOMIZED_CLEANING,
            DreameMowerProperty.CHILD_LOCK,
            DreameMowerProperty.DND_TASK,
            DreameMowerProperty.MULTI_FLOOR_MAP,
            DreameMowerProperty.VOLUME,
            DreameMowerProperty.VOICE_PACKET_ID,
            DreameMowerProperty.TIMEZONE,
            DreameMowerProperty.MAP_SAVING,
            DreameMowerProperty.AUTO_SWITCH_SETTINGS,
            DreameMowerProperty.SHORTCUTS,
            DreameMowerProperty.VOICE_ASSISTANT,
            DreameMowerProperty.CRUISE_SCHEDULE,
            DreameMowerProperty.CAMERA_LIGHT_BRIGHTNESS,
            DreameMowerProperty.STREAM_PROPERTY,
            DreameMowerProperty.STREAM_SPACE,
            DreameMowerProperty.VOICE_ASSISTANT_LANGUAGE,
            DreameMowerProperty.OFF_PEAK_CHARGING,
        ]

        self.listen(self._task_status_changed, DreameMowerProperty.TASK_STATUS)
        self.listen(self._status_changed, DreameMowerProperty.STATUS)
        self.listen(self._charging_status_changed, DreameMowerProperty.CHARGING_STATUS)
        self.listen(self._cleaning_mode_changed, DreameMowerProperty.CLEANING_MODE)
        self.listen(self._ai_obstacle_detection_changed, DreameMowerProperty.AI_DETECTION)
        self.listen(
            self._auto_switch_settings_changed,
            DreameMowerProperty.AUTO_SWITCH_SETTINGS,
        )
        self.listen(self._dnd_task_changed, DreameMowerProperty.DND_TASK)
        self.listen(self._stream_status_changed, DreameMowerProperty.STREAM_STATUS)
        self.listen(self._shortcuts_changed, DreameMowerProperty.SHORTCUTS)
        self.listen(
            self._voice_assistant_language_changed,
            DreameMowerProperty.VOICE_ASSISTANT_LANGUAGE,
        )
        self.listen(self._off_peak_charging_changed, DreameMowerProperty.OFF_PEAK_CHARGING)
        self.listen(self._error_changed, DreameMowerProperty.ERROR)
        self.listen(
            self._map_recovery_status_changed,
            DreameMowerProperty.MAP_RECOVERY_STATUS,
        )

        self._protocol = DreameMowerProtocol(
            self.host,
            self.token,
            username,
            password,
            country,
            prefer_cloud,
            account_type,
            device_id,
        )
        if self._protocol.cloud:
            self._map_manager = DreameMapMowerMapManager(self._protocol)

            self.listen(self._map_list_changed, DreameMowerProperty.MAP_LIST)
            self.listen(self._recovery_map_list_changed, DreameMowerProperty.RECOVERY_MAP_LIST)
            self.listen(self._battery_level_changed, DreameMowerProperty.BATTERY_LEVEL)
            self.listen(self._map_property_changed, DreameMowerProperty.CUSTOMIZED_CLEANING)
            self.listen(self._map_property_changed, DreameMowerProperty.STATE)
            self.listen(
                self._map_backup_status_changed,
                DreameMowerProperty.MAP_BACKUP_STATUS,
            )
            self._map_manager.listen(self._map_changed, self._property_changed)
            self._map_manager.listen_error(self._update_failed)






































    @staticmethod
    def split_group_value(value: int, mop_pad_lifting: bool = False) -> list[int]:
        if value is not None:
            value_list = []
            value_list.append((value & 3) if mop_pad_lifting else (value & 1))
            byte1 = value >> 8
            byte1 = byte1 & -769
            value_list.append(byte1)
            value_list.append(value >> 16)
            return value_list

    @staticmethod
    def combine_group_value(values: list[int]) -> int:
        if values and len(values) == 3:
            return ((((0 ^ values[2]) << 8) ^ values[1]) << 8) ^ values[0]

    def connect_device(self) -> None:
        """Connect to the device api."""
        _LOGGER.debug("Connecting to device")
        info = self._protocol.connect(self._message_callback, self._connected_callback)
        if info:
            self.info = DreameMowerDeviceInfo(info)
            if self.mac is None:
                self.mac = self.info.mac_address
            _LOGGER.info(
                "Connected to device: %s %s",
                self.info.model,
                self.info.firmware_version,
            )

            self._last_settings_request = time.time()
            self._last_map_list_request = self._last_settings_request
            self._dirty_data = {}
            self._dirty_auto_switch_data = {}
            self._dirty_ai_data = {}
            self._request_properties()
            self._last_update_failed = None

            if self.device_connected and self._protocol.cloud is not None and (not self._ready or not self.available):
                if self._map_manager:
                    model = self.info.model.split(".")
                    if len(model) == 3:
                        for k, v in json.loads(
                            zlib.decompress(base64.b64decode(DEVICE_KEY), zlib.MAX_WBITS | 32)
                        ).items():
                            if model[2] in v:
                                self._map_manager.set_aes_iv(k)
                                break
                    self._map_manager.set_capability(self.capability)
                    self._map_manager.set_update_interval(self._map_update_interval)
                    self._map_manager.set_device_running(
                        self.status.running,
                        self.status.docked and not self.status.started,
                    )

                    # set_update_interval starts the existing map-manager worker.
                    # Do not also run its cloud map request synchronously in the
                    # first state snapshot; app-map metadata hydrates separately.
                    if self.status.current_map is not None:
                        self.update_map()

                if self.cloud_connected:
                    self._cleaning_history_update = -1
                    if (self.capability.ai_detection and not self.status.ai_policy_accepted) or True:
                        try:
                            prop = "prop.s_ai_config"
                            response = self._protocol.cloud.get_batch_device_datas([prop])
                            if response and prop in response and response[prop]:
                                value = json.loads(response[prop])
                                self.status.ai_policy_acepted = (
                                    value.get("privacyAuthed")
                                    if "privacyAuthed" in value
                                    else value.get("aiPrivacyAuthed")
                                )
                        except:
                            pass

            if not self.available:
                self.available = True

            if not self._ready:
                self._ready = True
            else:
                self._property_changed()

    def connect_cloud(self) -> None:
        """Connect to the cloud api."""
        if self._protocol.cloud and not self._protocol.cloud.logged_in:
            self._protocol.cloud.login()
            if self._protocol.cloud.logged_in is False:
                if self._protocol.cloud.two_factor_url:
                    self.two_factor_url = self._protocol.cloud.two_factor_url
                    self._property_changed()
                self._map_manager.schedule_update(-1)
            elif self._protocol.cloud.logged_in:
                if self.two_factor_url:
                    self.two_factor_url = None
                    self._property_changed()

                if self._protocol.connected:
                    self._map_manager.schedule_update(5)

                self.token, self.host = self._protocol.cloud.get_info(self.mac)
                if not self._protocol.dreame_cloud:
                    self._protocol.set_credentials(self.host, self.token, self.mac, self.account_type)

    def disconnect(self) -> None:
        """Disconnect from device and cancel timers"""
        _LOGGER.info("Disconnect")
        self.disconnected = True
        self.schedule_update(-1)
        if self._map_manager:
            self._map_manager.disconnect()
        self._protocol.disconnect()
        self._property_changed()

    def listen(self, callback, property: DreameMowerProperty = None) -> None:
        """Set callback functions for external listeners"""
        if callback is None:
            self._update_callback = None
            self._property_update_callback = {}
            return

        if property is None:
            self._update_callback = callback
        else:
            if property.value not in self._property_update_callback:
                self._property_update_callback[property.value] = []
            self._property_update_callback[property.value].append(callback)

    def listen_error(self, callback) -> None:
        """Set error callback function for external listeners"""
        self._error_callback = callback

    def schedule_update(self, wait: float = None, force_request_properties=False) -> None:
        """Schedule a device update for future"""
        if wait == None:
            wait = self._update_interval

        if self._update_timer is not None:
            self._update_timer.cancel()
            del self._update_timer
            self._update_timer = None

        if wait >= 0:
            self._update_timer = Timer(
                wait, self._action_update_task if force_request_properties else self._update_task
            )
            self._update_timer.start()

    def get_property(
        self,
        prop: (
            DreameMowerProperty | DreameMowerAutoSwitchProperty | DreameMowerStrAIProperty | DreameMowerAIProperty
        ),
    ) -> Any:
        """Get a device property from memory"""
        if isinstance(prop, DreameMowerAutoSwitchProperty):
            return self.get_auto_switch_property(prop)
        if isinstance(prop, DreameMowerStrAIProperty) or isinstance(prop, DreameMowerAIProperty):
            return self.get_ai_property(prop)
        if prop is not None and prop.value in self.data:
            return self.data[prop.value]
        return None

    def get_auto_switch_property(self, prop: DreameMowerAutoSwitchProperty) -> int:
        """Get a device auto switch property from memory"""
        if self.capability.auto_switch_settings and self.auto_switch_data:
            if prop is not None and prop.name in self.auto_switch_data:
                return int(self.auto_switch_data[prop.name])
        return None

    def get_ai_property(self, prop: DreameMowerStrAIProperty | DreameMowerAIProperty) -> bool:
        """Get a device AI property from memory"""
        if self.capability.ai_detection and self.ai_data:
            if prop is not None and prop.name in self.ai_data:
                return bool(self.ai_data[prop.name])
        return None







    def update(self, force_request_properties=False) -> None:
        """Get properties from the device."""
        _LOGGER.debug("Device update: %s", self._update_interval)

        if self._update_running:
            return

        if not self.cloud_connected:
            self.connect_cloud()
            self.connect_device()

        # Read-only properties
        properties = [
            DreameMowerProperty.STATE,
            DreameMowerProperty.ERROR,
            DreameMowerProperty.BATTERY_LEVEL,
            DreameMowerProperty.CHARGING_STATUS,
            DreameMowerProperty.STATUS,
            DreameMowerProperty.TASK_STATUS,
            DreameMowerProperty.WARN_STATUS,
            DreameMowerProperty.RELOCATION_STATUS,
            DreameMowerProperty.CLEANING_PAUSED,
            DreameMowerProperty.CLEANING_CANCEL,
            DreameMowerProperty.SCHEDULED_CLEAN,
            DreameMowerProperty.TASK_TYPE,
            DreameMowerProperty.MAP_RECOVERY_STATUS,
        ]

        if self.capability.backup_map:
            properties.append(DreameMowerProperty.MAP_BACKUP_STATUS)

        now = time.time()
        if self.status.active:
            # Only changed when robot is active
            properties.extend([DreameMowerProperty.CLEANED_AREA, DreameMowerProperty.CLEANING_TIME])

        if self._consumable_change:
            # Consumable properties
            properties.extend(
                [
                    DreameMowerProperty.BLADES_TIME_LEFT,
                    DreameMowerProperty.BLADES_LEFT,
                    DreameMowerProperty.SIDE_BRUSH_TIME_LEFT,
                    DreameMowerProperty.SIDE_BRUSH_LEFT,
                    DreameMowerProperty.FILTER_LEFT,
                    DreameMowerProperty.FILTER_TIME_LEFT,
                    DreameMowerProperty.LENSBRUSH_LEFT,
                    DreameMowerProperty.LENSBRUSH_TIME_LEFT,
                    DreameMowerProperty.SQUEEGEE_LEFT,
                    DreameMowerProperty.SQUEEGEE_TIME_LEFT,
                    DreameMowerProperty.SILVER_ION_LEFT,
                    DreameMowerProperty.SILVER_ION_TIME_LEFT,
                    DreameMowerProperty.TANK_FILTER_LEFT,
                    DreameMowerProperty.TANK_FILTER_TIME_LEFT,
                ]
            )

            if not self.capability.disable_sensor_cleaning:
                properties.extend(
                    [
                        DreameMowerProperty.SENSOR_DIRTY_LEFT,
                        DreameMowerProperty.SENSOR_DIRTY_TIME_LEFT,
                    ]
                )

        if now - self._last_settings_request > 9.5:
            self._last_settings_request = now

            if not self._consumable_change:
                properties.extend(
                    [
                        DreameMowerProperty.LENSBRUSH_LEFT,
                        DreameMowerProperty.LENSBRUSH_TIME_LEFT,
                        DreameMowerProperty.SQUEEGEE_LEFT,
                        DreameMowerProperty.SQUEEGEE_TIME_LEFT,
                    ]
                )

            properties.extend(self._read_write_properties)

            if not self.capability.dnd_task:
                properties.extend(
                    [
                        DreameMowerProperty.DND,
                        DreameMowerProperty.DND_START,
                        DreameMowerProperty.DND_END,
                    ]
                )

        if self._map_manager and not self.status.running and now - self._last_map_list_request > 60:
            properties.extend([DreameMowerProperty.MAP_LIST, DreameMowerProperty.RECOVERY_MAP_LIST])
            self._last_map_list_request = time.time()

        try:
            if self._protocol.dreame_cloud and (not self.device_connected or not self.cloud_connected):
                force_request_properties = True

            if not self._protocol.dreame_cloud or force_request_properties:
                self._request_properties(properties)
            elif self.status.map_backup_status:
                self._request_properties([DreameMowerProperty.MAP_BACKUP_STATUS])
            elif self.status.map_recovery_status:
                self._request_properties([DreameMowerProperty.MAP_RECOVERY_STATUS])
        except Exception as ex:
            self._update_running = False
            raise DeviceUpdateFailedException(ex) from None

        if self._dirty_data:
            for k, v in copy.deepcopy(self._dirty_data).items():
                if time.time() - v.update_time >= self._restore_timeout:
                    if v.previous_value is not None:
                        value = self.data.get(k)
                        if value is None or v.value == value:
                            _LOGGER.info(
                                "Property %s Value Restored: %s <- %s",
                                self._property_name(k),
                                v.previous_value,
                                value,
                            )
                            self.data[k] = v.previous_value
                            if k in self._property_update_callback:
                                for callback in self._property_update_callback[k]:
                                    callback(v.previous_value)

                            self._property_changed()
                            self.schedule_update(1, True)
                    del self._dirty_data[k]

        if self._dirty_auto_switch_data:
            for k, v in copy.deepcopy(self._dirty_auto_switch_data).items():
                if time.time() - v.update_time >= self._restore_timeout:
                    if v.previous_value is not None:
                        value = self.auto_switch_data.get(k)
                        ## TODO
                        # if value is None or v.value == value:
                        #    _LOGGER.info(
                        #        "Property %s Value Restored: %s <- %s",
                        #        k,
                        #        v.previous_value,
                        #        value,
                        #    )
                        #    self.auto_switch_data[k] = v.previous_value
                        #    self._property_changed()
                        #    self.schedule_update(1, True)
                    del self._dirty_auto_switch_data[k]

        if self._dirty_ai_data:
            for k, v in copy.deepcopy(self._dirty_ai_data).items():
                if time.time() - v.update_time >= self._restore_timeout:
                    if v.previous_value is not None:
                        value = self.ai_data.get(k)
                        ## TODO
                        # if value is None or v.value == value:
                        #    _LOGGER.info(
                        #        "AI Property %s Value Restored: %s <- %s",
                        #        k,
                        #        v.previous_value,
                        #        value,
                        #    )
                        #    self.ai_data[k] = v.previous_value
                        #    self._property_changed()
                        #    self.schedule_update(1, True)
                    del self._dirty_ai_data[k]

        if self._consumable_change:
            self._consumable_change = False

        if self._map_manager:
            self._map_manager.set_update_interval(self._map_update_interval)
            self._map_manager.set_device_running(self.status.running, self.status.docked and not self.status.started)

        self._update_running = False





















































































    @property
    def _update_interval(self) -> float:
        """Dynamic update interval of the device for the timer."""
        now = time.time()
        if self.status.map_backup_status or self.status.map_recovery_status:
            return 2
        if self._last_update_failed:
            return 5 if now - self._last_update_failed <= 60 else 10 if now - self._last_update_failed <= 300 else 30
        if not -self._last_change <= 60:
            return 3 if self.status.active else 5
        if self.status.active or self.status.started:
            return 3 if self.status.running else 5
        if self._map_manager:
            return min(self._map_update_interval, 10)
        return 10

    @property
    def _map_update_interval(self) -> float:
        """Dynamic map update interval for the map manager."""
        if self._map_manager:
            if self._protocol.dreame_cloud:
                return 10 if self.status.active else 30
            now = time.time()
            if now - self._last_map_request <= 120 or now - self._last_change <= 60:
                return 2.5 if self.status.active or self.status.started else 5
            return 3 if self.status.running else 10 if self.status.active else 30
        return -1

    @property
    def name(self) -> str:
        """Return the name of the device."""
        return self._name

    @property
    def device_connected(self) -> bool:
        """Return connection status of the device."""
        return self._protocol.connected

    @property
    def cloud_connected(self) -> bool:
        """Return connection status of the device."""
        return (
            self._protocol.cloud
            and self._protocol.cloud.connected
        )
