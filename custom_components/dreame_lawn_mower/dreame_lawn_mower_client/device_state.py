"""Property reconciliation and state-transition callbacks for the legacy device."""

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

class _DreameMowerDeviceStateMixin:
    def _connected_callback(self):
        if not self._ready:
            return
        _LOGGER.info("Requesting properties after connect")
        self.schedule_update(2, True)

    def _message_callback(self, message):
        if not self._ready:
            return

        _LOGGER.debug("Message Callback: %s", message)
        with self._state_lock:
            self._remember_realtime_message(message)

            if "method" in message:
                self.available = True
                if message["method"] == "properties_changed" and "params" in message:
                    params = []
                    map_params = []
                    for param in message["params"]:
                        matched_property = None
                        properties = [prop for prop in DreameMowerProperty]
                        for prop in properties:
                            if prop in self.property_mapping:
                                mapping = self.property_mapping[prop]
                                _LOGGER.debug("Mapping: %s", mapping)
                                if (
                                    "aiid" not in mapping
                                    and param["siid"] == mapping["siid"]
                                    and param["piid"] == mapping["piid"]
                                ):
                                    matched_property = prop
                                    if prop in self._default_properties:
                                        param["did"] = str(prop.value)
                                        param["code"] = 0
                                        params.append(param)
                                    elif (
                                        prop is DreameMowerProperty.OBJECT_NAME
                                        or prop is DreameMowerProperty.MAP_DATA
                                        or prop is DreameMowerProperty.ROBOT_TIME
                                        or prop is DreameMowerProperty.OLD_MAP_DATA
                                    ):
                                        map_params.append(param)
                                    break
                        self._remember_realtime_property(param, matched_property)
                    if len(map_params) and self._map_manager:
                        self._map_manager.handle_properties(map_params)

                    self._handle_properties(params)

    def _handle_properties(self, properties) -> bool:
        if not isinstance(properties, list | tuple):
            _LOGGER.debug(
                "Ignoring invalid property response of type %s",
                type(properties).__name__,
            )
            return False
        changed = False
        callbacks = []
        for prop in properties:
            if not isinstance(prop, dict):
                continue
            did = int(prop["did"])
            property_enum = self._property_enum_from_payload(prop, did)
            data_id = property_enum.value if property_enum is not None else did
            property_name = self._property_name(data_id)
            if property_enum is None:
                self._remember_unknown_property(did, prop)
            if prop["code"] == 0 and "value" in prop:
                value = prop["value"]
                if data_id in self._dirty_data:
                    if (
                        self._dirty_data[data_id].value != value
                        and time.time() - self._dirty_data[data_id].update_time
                        < self._discard_timeout
                    ):
                        _LOGGER.info(
                            "Property %s Value Discarded: %s <- %s",
                            property_name,
                            self._dirty_data[data_id].value,
                            value,
                        )
                        del self._dirty_data[data_id]
                        continue
                    del self._dirty_data[data_id]

                current_value = self.data.get(data_id)

                if current_value != value:
                    # Do not call external listener when map and json properties changed
                    if not (
                        data_id == DreameMowerProperty.MAP_LIST.value
                        or data_id == DreameMowerProperty.RECOVERY_MAP_LIST.value
                        or data_id == DreameMowerProperty.MAP_DATA.value
                        or data_id == DreameMowerProperty.OBJECT_NAME.value
                        or data_id == DreameMowerProperty.AUTO_SWITCH_SETTINGS.value
                        or data_id == DreameMowerProperty.AI_DETECTION.value
                        # or did == DreameMowerProperty.SELF_TEST_STATUS.value
                    ):
                        changed = True
                    custom_property = (
                        data_id == DreameMowerProperty.AUTO_SWITCH_SETTINGS.value
                        or data_id == DreameMowerProperty.AI_DETECTION.value
                        or data_id == DreameMowerProperty.MAP_LIST.value
                        or data_id == DreameMowerProperty.SERIAL_NUMBER.value
                    )
                    if not custom_property:
                        if current_value is not None:
                            _LOGGER.debug(
                                "Property %s Changed: %s -> %s",
                                property_name,
                                current_value,
                                value,
                            )
                        else:
                            _LOGGER.debug(
                                "Property %s Added: %s",
                                property_name,
                                value,
                            )
                    self.data[data_id] = value
                    if data_id in self._property_update_callback:
                        _LOGGER.debug(
                            "Property %s Callbacks: %s",
                            property_name,
                            self._property_update_callback[data_id],
                        )
                        for callback in self._property_update_callback[data_id]:
                            if not self._ready and custom_property:
                                callback(current_value)
                            else:
                                callbacks.append([callback, current_value])
            else:
                _LOGGER.debug("Property %s Not Available", property_name)

        if not self._ready:
            self.capability.refresh(
                json.loads(zlib.decompress(base64.b64decode(DREAME_MODEL_CAPABILITIES), zlib.MAX_WBITS | 32))
            )

        for callback in callbacks:
            callback[0](callback[1])

        if changed:
            self._last_change = time.time()
            if self._ready:
                self._property_changed()

        if not self._ready:
            if self._protocol.dreame_cloud:
                self._discard_timeout = 5

            self.status.segment_cleaning_mode_list = self.status.cleaning_mode_list.copy()

            if self.capability.cleaning_route:
                if (
                    self.status.cleaning_mode == DreameMowerCleaningMode.MOWING
                ):
                    new_list = CLEANING_ROUTE_TO_NAME.copy()
                    new_list.pop(DreameMowerCleaningRoute.DEEP)
                    new_list.pop(DreameMowerCleaningRoute.INTENSIVE)
                    self.status.cleaning_route_list = {v: k for k, v in new_list.items()}
                    new_list = CLEANING_ROUTE_TO_NAME.copy()
                    if self.capability.segment_slow_clean_route:
                        new_list.pop(DreameMowerCleaningRoute.QUICK)
                    self.status.segment_cleaning_route_list = {v: k for k, v in new_list.items()}

            for p in dir(self.capability):
                if not p.startswith("__") and not callable(getattr(self.capability, p)):
                    val = getattr(self.capability, p)
                    if isinstance(val, bool) and val:
                        _LOGGER.debug("Capability %s", p.upper())

        return changed

    def _property_enum(self, did: int) -> DreameMowerProperty | None:
        """Return the known property enum for a device property id."""
        try:
            return DreameMowerProperty(did)
        except ValueError:
            return None

    def _property_enum_from_payload(
        self,
        payload: dict[str, Any],
        did: int,
    ) -> DreameMowerProperty | None:
        """Return the known property enum for a payload.

        Some MOVA mower cloud responses use generated negative ``did`` values
        even for standard properties. In that case the siid/piid pair is still
        authoritative and lets us update the normal property cache.
        """
        property_enum = self._property_enum(did)
        if property_enum is not None:
            return property_enum

        siid = payload.get("siid")
        piid = payload.get("piid")
        if siid is None or piid is None:
            return None

        for prop, mapping in self.property_mapping.items():
            if (
                "aiid" not in mapping
                and siid == mapping["siid"]
                and piid == mapping["piid"]
            ):
                return prop
        return None

    def _property_name(self, did: int) -> str:
        """Return a safe log/debug name for a property id."""
        property_enum = self._property_enum(did)
        if property_enum is not None:
            return property_enum.name
        return f"UNKNOWN_{did}"

    def _remember_unknown_property(self, did: int, payload: dict[str, Any]) -> None:
        """Store unknown property payloads for diagnostics instead of crashing."""
        self.unknown_properties[did] = {
            "did": did,
            "code": payload.get("code"),
            "siid": payload.get("siid"),
            "piid": payload.get("piid"),
            "value": payload.get("value"),
            "last_seen": time.time(),
        }

    def _remember_realtime_message(self, message: dict[str, Any]) -> None:
        """Store the last realtime payload for diagnostics."""
        self.last_realtime_message = {
            "received_at": time.time(),
            "message": copy.deepcopy(message),
        }

    def _remember_realtime_property(
        self,
        payload: dict[str, Any],
        property_enum: DreameMowerProperty | None,
    ) -> None:
        """Store realtime MQTT property payloads, even when not yet decoded."""
        siid = payload.get("siid")
        piid = payload.get("piid")
        if siid is None or piid is None:
            return

        key = f"{siid}.{piid}"
        property_name = (
            property_enum.name
            if property_enum is not None
            else mower_realtime_property_name(key)
        )
        received_at = (
            self.last_realtime_message.get("received_at")
            if isinstance(self.last_realtime_message, dict)
            else None
        )
        self.realtime_properties[key] = {
            "siid": siid,
            "piid": piid,
            "did": payload.get("did"),
            "code": payload.get("code"),
            "value": copy.deepcopy(payload.get("value")),
            "property_name": property_name,
            "last_seen": (
                received_at
                if isinstance(received_at, (int, float))
                else time.time()
            ),
        }

    def _request_properties(self, properties: list[DreameMowerProperty] = None) -> bool:
        """Request properties from the device."""
        if not properties:
            properties = self._default_properties

        property_list = []
        for prop in properties:
            if prop in self.property_mapping:
                mapping = self.property_mapping[prop]
                # Do not include properties that are not exists on the device
                if "aiid" not in mapping and (not self._ready or prop.value in self.data):
                    property_list.append({"did": str(prop.value), **mapping})

        results = self._protocol.get_properties(property_list)
        return self._handle_properties(results)

    def _update_status(self, task_status: DreameMowerTaskStatus, status: DreameMowerStatus) -> None:
        """Update status properties on memory for map renderer to update the image before action is sent to the device."""
        if task_status is not DreameMowerTaskStatus.COMPLETED:
            new_state = DreameMowerState.MOWING
            self._update_property(DreameMowerProperty.STATE, new_state.value)

        self._update_property(DreameMowerProperty.STATUS, status.value)
        self._update_property(DreameMowerProperty.TASK_STATUS, task_status.value)

    def _update_property(self, prop: DreameMowerProperty, value: Any) -> Any:
        """Update device property on memory and notify listeners."""
        if prop in self.property_mapping:
            if (
                not self.capability.new_state
                and prop == DreameMowerProperty.STATE
                and int(value) > 18
                and value in DreameMowerState._value2member_map_
            ):
                old_state = DreameMowerStateOld[DreameMowerState(value).name]
                if old_state:
                    value = int(old_state)
            current_value = self.get_property(prop)
            if current_value != value:
                did = prop.value
                self.data[did] = value
                if did in self._property_update_callback:
                    for callback in self._property_update_callback[did]:
                        callback(current_value)

                self._property_changed()
                return current_value if current_value is not None else value
        return None

    def _map_property_changed(self, previous_property: Any = None) -> None:
        """Update last update time of the map when a property associated with rendering map changed."""
        if self._map_manager and previous_property is not None:
            self._map_manager.editor.refresh_map()

    def _map_list_changed(self, previous_map_list: Any = None) -> None:
        """Update map list object name on map manager map list property when changed"""
        if self._map_manager:
            map_list = self.get_property(DreameMowerProperty.MAP_LIST)
            if map_list and map_list != "":
                try:
                    map_list = json.loads(map_list)
                    object_name = map_list.get("object_name")
                    if object_name is None:
                        object_name = map_list.get("obj_name")
                    if object_name and object_name != "":
                        _LOGGER.info("Property MAP_LIST Changed: %s", object_name)
                        self._map_manager.set_map_list_object_name(object_name, map_list.get("md5"))
                    else:
                        self._last_map_list_request = 0
                except:
                    pass

    def _recovery_map_list_changed(self, previous_recovery_map_list: Any = None) -> None:
        """Update recovery list object name on map manager recovery list property when changed"""
        if self._map_manager:
            map_list = self.get_property(DreameMowerProperty.RECOVERY_MAP_LIST)
            if map_list and map_list != "":
                try:
                    map_list = json.loads(map_list)
                    object_name = map_list.get("object_name")
                    if object_name is None:
                        object_name = map_list.get("obj_name")
                    if object_name and object_name != "":
                        self._map_manager.set_recovery_map_list_object_name(object_name)
                    else:
                        self._last_map_list_request = 0
                except:
                    pass

    def _map_recovery_status_changed(self, previous_map_recovery_status: Any = None) -> None:
        if previous_map_recovery_status and self.status.map_recovery_status:
            if self.status.map_recovery_status == DreameMapRecoveryStatus.SUCCESS.value:
                if not self._protocol.dreame_cloud:
                    self._last_map_list_request = 0
                self._map_manager.request_next_map()
                self._map_manager.request_next_recovery_map_list()

            if self.status.map_recovery_status != DreameMapRecoveryStatus.RUNNING.value:
                self._request_properties([DreameMowerProperty.MAP_RECOVERY_STATUS])

    def _map_backup_status_changed(self, previous_map_backup_status: Any = None) -> None:
        if previous_map_backup_status and self.status.map_backup_status:
            if self.status.map_backup_status == DreameMapBackupStatus.SUCCESS.value:
                if not self._protocol.dreame_cloud:
                    self._last_map_list_request = 0
                self._map_manager.request_next_recovery_map_list()
            if self.status.map_backup_status != DreameMapBackupStatus.RUNNING.value:
                self._request_properties([DreameMowerProperty.MAP_BACKUP_STATUS])

    def _cleaning_mode_changed(self, previous_cleaning_mode: Any = None) -> None:
        value = self.get_property(DreameMowerProperty.CLEANING_MODE)
        new_cleaning_mode = None

        if previous_cleaning_mode is not None and self.status.go_to_zone:
            self.status.go_to_zone.cleaning_mode = None

        if self.status.cleaning_mode != new_cleaning_mode:
            self.status.cleaning_mode = new_cleaning_mode

            if self._ready and self.capability.cleaning_route:
                new_list = CLEANING_ROUTE_TO_NAME.copy()
                if (
                    self.status.cleaning_mode == DreameMowerCleaningMode.MOWING
                ):
                    new_list.pop(DreameMowerCleaningRoute.DEEP)
                    new_list.pop(DreameMowerCleaningRoute.INTENSIVE)
                self.status.cleaning_route_list = {v: k for k, v in new_list.items()}

                if self.status.cleaning_route and self.status.cleaning_route not in self.status.cleaning_route_list:
                    self.set_auto_switch_property(
                        DreameMowerAutoSwitchProperty.CLEANING_ROUTE,
                        DreameMowerCleaningRoute.STANDARD.value,
                    )

    def _task_status_changed(self, previous_task_status: Any = None) -> None:
        """Task status is a very important property and must be listened to trigger necessary actions when a task started or ended"""
        if previous_task_status is not None:
            if previous_task_status in DreameMowerTaskStatus._value2member_map_:
                previous_task_status = DreameMowerTaskStatus(previous_task_status)

            task_status = self.get_property(DreameMowerProperty.TASK_STATUS)
            if task_status in DreameMowerTaskStatus._value2member_map_:
                task_status = DreameMowerTaskStatus(task_status)

            if previous_task_status is DreameMowerTaskStatus.COMPLETED:
                # as implemented on the app
                self._update_property(DreameMowerProperty.CLEANING_TIME, 0)
                self._update_property(DreameMowerProperty.CLEANED_AREA, 0)

            if self._map_manager is not None:
                # Update map data for renderer to update the map image according to the new task status
                if previous_task_status is DreameMowerTaskStatus.COMPLETED:
                    if (
                        task_status is DreameMowerTaskStatus.AUTO_CLEANING
                        or task_status is DreameMowerTaskStatus.ZONE_CLEANING
                        or task_status is DreameMowerTaskStatus.SEGMENT_CLEANING
                        or task_status is DreameMowerTaskStatus.SPOT_CLEANING
                        or task_status is DreameMowerTaskStatus.CRUISING_PATH
                        or task_status is DreameMowerTaskStatus.CRUISING_POINT
                    ):
                        # Clear path on current map on cleaning start as implemented on the app
                        self._map_manager.editor.clear_path()
                    elif task_status is DreameMowerTaskStatus.FAST_MAPPING:
                        # Clear current map on mapping start as implemented on the app
                        self._map_manager.editor.reset_map()
                    else:
                        self._map_manager.editor.refresh_map()
                else:
                    self._map_manager.editor.refresh_map()

            if task_status is DreameMowerTaskStatus.COMPLETED:
                if (
                    previous_task_status is DreameMowerTaskStatus.CRUISING_PATH
                    or previous_task_status is DreameMowerTaskStatus.CRUISING_POINT
                    or self.status.go_to_zone
                ):
                    if self._map_manager is not None:
                        # Get the new map list from cloud
                        self._map_manager.editor.set_cruise_points([])
                        self._map_manager.request_next_map_list()
                    self._cleaning_history_update = time.time()
                elif previous_task_status is DreameMowerTaskStatus.FAST_MAPPING:
                    # as implemented on the app
                    self._update_property(DreameMowerProperty.CLEANING_TIME, 0)
                    if self._map_manager is not None:
                        # Mapping is completed, get the new map list from cloud
                        self._map_manager.request_next_map_list()
                elif (
                    self.status.cleanup_started
                    and not self.status.cleanup_completed
                    and (self.status.status is DreameMowerStatus.BACK_HOME or not self.status.running)
                ):
                    self.status.cleanup_started = False
                    self.status.cleanup_completed = True
                    self._cleaning_history_update = time.time()
            else:
                self.status.cleanup_started = not (
                    self.status.fast_mapping
                    or self.status.cruising
                    or (
                        task_status is DreameMowerTaskStatus.DOCKING_PAUSED
                        and previous_task_status is DreameMowerTaskStatus.COMPLETED
                    )
                )
                self.status.cleanup_completed = False

            if self.status.go_to_zone is not None and not (
                task_status is DreameMowerTaskStatus.ZONE_CLEANING
                or task_status is DreameMowerTaskStatus.ZONE_CLEANING_PAUSED
                or task_status is DreameMowerTaskStatus.ZONE_DOCKING_PAUSED
                or task_status is DreameMowerTaskStatus.CRUISING_POINT
                or task_status is DreameMowerTaskStatus.CRUISING_POINT_PAUSED
            ):
                self._restore_go_to_zone()

            if self._map_manager:
                self._map_manager.editor.refresh_map()

            if (
                task_status is DreameMowerTaskStatus.COMPLETED
                or previous_task_status is DreameMowerTaskStatus.COMPLETED
            ):
                # Get properties that only changes when task status is changed
                properties = [
                    DreameMowerProperty.BLADES_TIME_LEFT,
                    DreameMowerProperty.BLADES_LEFT,
                    DreameMowerProperty.SIDE_BRUSH_TIME_LEFT,
                    DreameMowerProperty.SIDE_BRUSH_LEFT,
                    DreameMowerProperty.FILTER_LEFT,
                    DreameMowerProperty.FILTER_TIME_LEFT,
                    DreameMowerProperty.TANK_FILTER_LEFT,
                    DreameMowerProperty.TANK_FILTER_TIME_LEFT,
                    DreameMowerProperty.SILVER_ION_TIME_LEFT,
                    DreameMowerProperty.SILVER_ION_LEFT,
                    DreameMowerProperty.LENSBRUSH_TIME_LEFT,
                    DreameMowerProperty.LENSBRUSH_LEFT,
                    DreameMowerProperty.SQUEEGEE_TIME_LEFT,
                    DreameMowerProperty.SQUEEGEE_LEFT,
                    DreameMowerProperty.TOTAL_CLEANING_TIME,
                    DreameMowerProperty.CLEANING_COUNT,
                    DreameMowerProperty.TOTAL_CLEANED_AREA,
                    DreameMowerProperty.TOTAL_RUNTIME,
                    DreameMowerProperty.TOTAL_CRUISE_TIME,
                    DreameMowerProperty.FIRST_CLEANING_DATE,
                    DreameMowerProperty.SCHEDULE,
                    DreameMowerProperty.SCHEDULE_CANCEL_REASON,
                    DreameMowerProperty.CRUISE_SCHEDULE,
                ]

                if not self.capability.disable_sensor_cleaning:
                    properties.extend(
                        [
                            DreameMowerProperty.SENSOR_DIRTY_LEFT,
                            DreameMowerProperty.SENSOR_DIRTY_TIME_LEFT,
                        ]
                    )

                if self._map_manager is not None:
                    properties.extend(
                        [
                            DreameMowerProperty.MAP_LIST,
                            DreameMowerProperty.RECOVERY_MAP_LIST,
                        ]
                    )
                    self._last_map_list_request = time.time()

                try:
                    self._request_properties(properties)
                except Exception as ex:
                    pass

                if self._protocol.prefer_cloud and self._protocol.dreame_cloud:
                    self.schedule_update(1, True)

    def _status_changed(self, previous_status: Any = None) -> None:
        if previous_status is not None:
            if previous_status in DreameMowerStatus._value2member_map_:
                previous_status = DreameMowerStatus(previous_status)

            status = self.get_property(DreameMowerProperty.STATUS)
            if (
                self._remote_control
                and status != DreameMowerStatus.REMOTE_CONTROL.value
                and previous_status != DreameMowerStatus.REMOTE_CONTROL.value
            ):
                self._remote_control = False

            if (
                not self.capability.cruising
                and status == DreameMowerStatus.BACK_HOME
                and previous_status == DreameMowerStatus.ZONE_CLEANING
                and self.status.started
            ):
                self.status.cleanup_started = False
                self.status.cleanup_completed = False
                self.status.go_to_zone.stop = True
                self._restore_go_to_zone(True)
            elif (
                not self.status.started
                and self.status.cleanup_started
                and not self.status.cleanup_completed
                and (self.status.status is DreameMowerStatus.BACK_HOME or not self.status.running)
            ):
                self.status.cleanup_started = False
                self.status.cleanup_completed = True
                self._cleaning_history_update = time.time()

                did = DreameMowerProperty.TASK_STATUS.value
                if did in self._property_update_callback:
                    for callback in self._property_update_callback[did]:
                        callback(self.status.task_status.value)
                self._property_changed()
            elif status == DreameMowerStatus.CHARGING.value and previous_status == DreameMowerStatus.BACK_HOME.value:
                self._cleaning_history_update = time.time()

            if previous_status == DreameMowerStatus.OTA.value:
                self._ready = False
                self.connect_device()

            if self._map_manager:
                self._map_manager.editor.refresh_map()

    def _charging_status_changed(self, previous_charging_status: Any = None) -> None:
        self._remote_control = False
        if previous_charging_status is not None:
            if self._map_manager:
                self._map_manager.editor.refresh_map()

            if (
                self._protocol.dreame_cloud
                and self.status.charging_status != DreameMowerChargingStatus.CHARGING_COMPLETED
            ):
                self.schedule_update(2, True)

    def _ai_obstacle_detection_changed(self, previous_ai_obstacle_detection: Any = None) -> None:
        """AI Detection property returns multiple values as json or int this function parses and sets the sub properties to memory"""
        ai_value = self.get_property(DreameMowerProperty.AI_DETECTION)
        changed = False
        if isinstance(ai_value, str):
            settings = json.loads(ai_value)
            if settings and self.ai_data is None:
                self.ai_data = {}

            for prop in DreameMowerStrAIProperty:
                if prop.value in settings:
                    value = settings[prop.value]
                    if prop.value in self._dirty_ai_data:
                        if (
                            self._dirty_ai_data[prop.name].value != value
                            and time.time() - self._dirty_ai_data[prop.name].update_time < self._discard_timeout
                        ):
                            _LOGGER.info(
                                "AI Property %s Value Discarded: %s <- %s",
                                prop.name,
                                self._dirty_ai_data[prop.name].value,
                                value,
                            )
                            del self._dirty_ai_data[prop.name]
                            continue
                        del self._dirty_ai_data[prop.name]

                    current_value = self.ai_data.get(prop.name)
                    if current_value != value:
                        if current_value is not None:
                            _LOGGER.info(
                                "AI Property %s Changed: %s -> %s",
                                prop.name,
                                current_value,
                                value,
                            )
                        else:
                            _LOGGER.info("AI Property %s Added: %s", prop.name, value)
                        changed = True
                        self.ai_data[prop.name] = value
        elif isinstance(ai_value, int):
            if self.ai_data is None:
                self.ai_data = {}

            for prop in DreameMowerAIProperty:
                bit = int(prop.value)
                value = (ai_value & bit) == bit
                if prop.name in self._dirty_ai_data:
                    if (
                        self._dirty_ai_data[prop.name].value != value
                        and time.time() - self._dirty_ai_data[prop.name].update_time < self._discard_timeout
                    ):
                        _LOGGER.info(
                            "AI Property %s Value Discarded: %s <- %s",
                            prop.name,
                            self._dirty_ai_data[prop.name].value,
                            value,
                        )
                        del self._dirty_ai_data[prop.name]
                        continue
                    del self._dirty_ai_data[prop.name]

                current_value = self.ai_data.get(prop.name)
                if current_value != value:
                    if current_value is not None:
                        _LOGGER.info(
                            "AI Property %s Changed: %s -> %s",
                            prop.name,
                            current_value,
                            value,
                        )
                    else:
                        _LOGGER.info("AI Property %s Added: %s", prop.name, value)
                    changed = True
                    self.ai_data[prop.name] = value

        if changed:
            self._last_change = time.time()
            if self._ready:
                self._property_changed()

        self.status.ai_policy_accepted = bool(
            self.status.ai_policy_accepted or self.status.ai_obstacle_detection or self.status.ai_obstacle_picture
        )

    def _auto_switch_settings_changed(self, previous_auto_switch_settings: Any = None) -> None:
        value = self.get_property(DreameMowerProperty.AUTO_SWITCH_SETTINGS)
        if isinstance(value, str) and len(value) > 2:
            cleangenius_changed = False
            try:
                settings = json.loads(value)
                settings_dict = {}

                if isinstance(settings, list):
                    for setting in settings:
                        settings_dict[setting["k"]] = setting["v"]
                elif "k" in settings:
                    settings_dict[settings["k"]] = settings["v"]

                if settings_dict and self.auto_switch_data is None:
                    self.auto_switch_data = {}

                changed = False
                for prop in DreameMowerAutoSwitchProperty:
                    if prop.value in settings_dict:
                        value = settings_dict[prop.value]

                        if prop.name in self._dirty_auto_switch_data:
                            if (
                                self._dirty_auto_switch_data[prop.name].value != value
                                and time.time() - self._dirty_auto_switch_data[prop.name].update_time
                                < self._discard_timeout
                            ):
                                _LOGGER.info(
                                    "Property %s Value Discarded: %s <- %s",
                                    prop.name,
                                    self._dirty_auto_switch_data[prop.name].value,
                                    value,
                                )
                                del self._dirty_auto_switch_data[prop.name]
                                continue
                            del self._dirty_auto_switch_data[prop.name]

                        current_value = self.auto_switch_data.get(prop.name)
                        if current_value != value:
                            if prop == DreameMowerAutoSwitchProperty.CLEANGENIUS:
                                cleangenius_changed = True

                            if current_value is not None:
                                _LOGGER.info(
                                    "Property %s Changed: %s -> %s",
                                    prop.name,
                                    current_value,
                                    value,
                                )
                            else:
                                _LOGGER.info("Property %s Added: %s", prop.name, value)
                            changed = True
                            self.auto_switch_data[prop.name] = value

                if changed:
                    self._last_change = time.time()
                    if self._ready and previous_auto_switch_settings is not None:
                        self._property_changed()
            except Exception as ex:
                _LOGGER.error("Failed to parse auto switch settings: %s", ex)

            if cleangenius_changed and self._map_manager and self._ready and previous_auto_switch_settings is not None:
                self._map_manager.editor.refresh_map()

    def _dnd_task_changed(self, previous_dnd_task: Any = None) -> None:
        dnd_tasks = self.get_property(DreameMowerProperty.DND_TASK)
        if dnd_tasks and dnd_tasks != "":
            self.status.dnd_tasks = json.loads(dnd_tasks)

    def _stream_status_changed(self, previous_stream_status: Any = None) -> None:
        stream_status = self.get_property(DreameMowerProperty.STREAM_STATUS)
        if stream_status and stream_status != "" and stream_status != "null":
            stream_status = json.loads(stream_status)
            if stream_status and stream_status.get("result") == 0:
                self.status.stream_session = stream_status.get("session")
                operation_type = stream_status.get("operType")
                operation = stream_status.get("operation")
                if operation_type:
                    if operation_type == "end" or operation == "end":
                        self.status.stream_status = DreameMowerStreamStatus.IDLE
                    elif operation_type == "start" or operation == "start":
                        if operation:
                            if operation == "monitor" or operation_type == "monitor":
                                self.status.stream_status = DreameMowerStreamStatus.VIDEO
                            elif operation == "intercom" or operation_type == "intercom":
                                self.status.stream_status = DreameMowerStreamStatus.AUDIO
                            elif operation == "recordVideo" or operation_type == "recordVideo":
                                self.status.stream_status = DreameMowerStreamStatus.RECORDING

    def _shortcuts_changed(self, previous_shortcuts: Any = None) -> None:
        shortcuts = self.get_property(DreameMowerProperty.SHORTCUTS)
        if shortcuts and shortcuts != "":
            shortcuts = json.loads(shortcuts)
            if shortcuts:
                # response = self.call_shortcut_action("GET_COMMANDS")
                new_shortcuts = {}
                for shortcut in shortcuts:
                    id = shortcut["id"]
                    running = (
                        False
                        if "state" not in shortcut
                        else bool(shortcut["state"] == "0" or shortcut["state"] == "1")
                    )
                    name = base64.decodebytes(shortcut["name"].encode("utf8")).decode("utf-8")
                    tasks = None
                    # response = self.call_shortcut_action("GET_COMMAND_BY_ID", {"id": id})
                    # if response and "out" in response:
                    #    data = response["out"]
                    #    if data and len(data):
                    #        if "value" in data[0] and data[0]["value"] != "":
                    #            tasks = []
                    #            for task in json.loads(data[0]["value"]):
                    #                segments = []
                    #                for segment in task:
                    #                    segments.append(ShortcutTask(segment_id=segment[0], suction_level=segment[1], water_volume=segment[2], cleaning_times=segment[3], cleaning_mode=segment[4]))
                    #                tasks.append(segments)
                    new_shortcuts[id] = Shortcut(id=id, name=name, running=running, tasks=tasks)
                self.status.shortcuts = new_shortcuts

    def _voice_assistant_language_changed(self, previous_voice_assistant_language: Any = None) -> None:
        value = self.get_property(DreameMowerProperty.VOICE_ASSISTANT_LANGUAGE)
        language_list = self.status.voice_assistant_language_list
        if value and len(value):
            language_list = VOICE_ASSISTANT_LANGUAGE_TO_NAME.copy()
            language_list.pop(DreameMowerVoiceAssistantLanguage.DEFAULT)
            language_list = {v: k for k, v in language_list.items()}
        elif DreameMowerVoiceAssistantLanguage.DEFAULT.value not in language_list:
            language_list = {v: k for k, v in VOICE_ASSISTANT_LANGUAGE_TO_NAME.items()}
        self.status.voice_assistant_language_list = language_list

    def _off_peak_charging_changed(self, previous_off_peak_charging: Any = None) -> None:
        off_peak_charging = self.get_property(DreameMowerProperty.OFF_PEAK_CHARGING)
        if off_peak_charging and off_peak_charging != "":
            self.status.off_peak_charging_config = json.loads(off_peak_charging)

    def _error_changed(self, previous_error: Any = None) -> None:
        if previous_error is not None and self.status.go_to_zone and self.status.has_error:
            self._restore_go_to_zone(True)

        if self._map_manager and previous_error is not None:
            self._map_manager.editor.refresh_map()

    def _battery_level_changed(self, previous_battery_level: Any = None) -> None:
        if self._map_manager and previous_battery_level is not None and self.status.battery_level == 100:
            self._map_manager.editor.refresh_map()

    def _property_changed(self) -> None:
        """Call external listener when a property changed"""
        if self._update_callback:
            self._update_callback()

    def _map_changed(self) -> None:
        """Call external listener when a map changed"""
        map_data = self.status.current_map
        if self._map_select_time:
            self._map_select_time = None
        if map_data and self.status.started:
            if self.status.go_to_zone is None and not self.status._capability.cruising and self.status.zone_cleaning:
                if map_data.active_areas and len(map_data.active_areas) == 1:
                    area = map_data.active_areas[0]
                    size = map_data.dimensions.grid_size
                    if area.check_size(size):
                        new_cleaning_mode = DreameMowerCleaningMode.MOWING.value

                        size = int(map_data.dimensions.grid_size / 2)
                        self.status.go_to_zone = GoToZoneSettings(
                            x=area.x0 + size,
                            y=area.y0 + size,
                            stop=bool(not self._map_manager.ready),
                            size=size,
                            cleaning_mode=new_cleaning_mode,
                        )
                        self._map_manager.editor.set_active_areas([])
                    else:
                        self.status.go_to_zone = False
                else:
                    self.status.go_to_zone = False

            if self.status.go_to_zone:
                position = map_data.robot_position
                if position:
                    size = self.status.go_to_zone.size
                    x = self.status.go_to_zone.x
                    y = self.status.go_to_zone.y
                    if (
                        position.x >= x - size
                        and position.x <= x + size
                        and position.y >= y - size
                        and position.y <= y + size
                    ):
                        self._restore_go_to_zone(True)

            if self.status.docked != map_data.docked and self._protocol.prefer_cloud:
                self.schedule_update(self._update_interval, True)

        if self._map_manager.ready:
            self._property_changed()

    def _update_failed(self, ex) -> None:
        """Call external listener when update failed"""
        if self._error_callback:
            self._error_callback(ex)

    def _action_update_task(self) -> None:
        self._update_task(True)

    def _update_task(self, force_request_properties=False) -> None:
        """Timer task for updating properties periodically"""
        self._update_timer = None
        try:
            self.update(force_request_properties)
            if self._ready:
                self.available = True
            self._update_fail_count = 0
        except Exception as ex:
            self._update_fail_count = self._update_fail_count + 1
            if self.available:
                self._last_update_failed = time.time()
                if self._update_fail_count <= 3:
                    _LOGGER.debug(
                        "Update failed, retrying %s: %s",
                        self._update_fail_count,
                        str(ex),
                    )
                elif self._ready:
                    _LOGGER.warning("Update Failed: %s", str(ex))
                    self.available = False
                    self._update_failed(ex)

        if not self.disconnected:
            self.schedule_update(self._update_interval)
