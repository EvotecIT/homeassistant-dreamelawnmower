"""Map-facing operations for the legacy mower device."""

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




class _DreameMowerDeviceMapMixin:
    def get_map_for_render(self, map_data: MapData) -> MapData | None:
        """Makes changes on map data for device related properties for renderer.
        Map manager does not need any device property for parsing and storing map data but map renderer does.
        """
        if map_data:
            if map_data.need_optimization:
                map_data = self._map_manager.optimizer.optimize(
                    map_data,
                    self._map_manager.selected_map if map_data.saved_map_status == 2 else None,
                )
                map_data.need_optimization = False

            render_map_data = copy.deepcopy(map_data)
            if (
                not self.capability.lidar_navigation
                and self.status.docked
                and not self.status.started
                and map_data.saved_map_status == 1
            ):
                saved_map_data = self._map_manager.selected_map
                render_map_data.segments = copy.deepcopy(saved_map_data.segments)
                render_map_data.data = copy.deepcopy(saved_map_data.data)
                render_map_data.pixel_type = copy.deepcopy(saved_map_data.pixel_type)
                render_map_data.dimensions = copy.deepcopy(saved_map_data.dimensions)
                render_map_data.charger_position = copy.deepcopy(saved_map_data.charger_position)
                render_map_data.no_go_areas = saved_map_data.no_go_areas
                render_map_data.virtual_walls = saved_map_data.virtual_walls
                render_map_data.robot_position = render_map_data.charger_position
                render_map_data.docked = True
                render_map_data.path = None
                render_map_data.need_optimization = False
                render_map_data.saved_map_status = 2
                render_map_data.optimized_pixel_type = None
                render_map_data.optimized_charger_position = None

            if render_map_data.optimized_pixel_type is not None:
                render_map_data.pixel_type = render_map_data.optimized_pixel_type
                render_map_data.dimensions = render_map_data.optimized_dimensions
                if render_map_data.optimized_charger_position is not None:
                    render_map_data.charger_position = render_map_data.optimized_charger_position

                # if not self.status.started and render_map_data.docked and render_map_data.robot_position and render_map_data.charger_position:
                #    render_map_data.charger_position = copy.deepcopy(render_map_data.robot_position)

            if render_map_data.combined_pixel_type is not None:
                render_map_data.pixel_type = render_map_data.combined_pixel_type
                render_map_data.dimensions = render_map_data.combined_dimensions

            offset = render_map_data.dimensions.grid_size / (1 if self.capability.map_object_offset else 2)
            render_map_data.dimensions.left = render_map_data.dimensions.left - offset
            render_map_data.dimensions.top = render_map_data.dimensions.top + offset

            if render_map_data.wifi_map:
                return render_map_data

            if render_map_data.furniture_version == 1 and self.capability.new_furnitures:
                render_map_data.furniture_version = 2

            if not render_map_data.history_map:
                if self.status.started and not (
                    self.status.zone_cleaning
                    or self.status.go_to_zone
                    or (
                        render_map_data.active_areas
                        and self.status.task_status is DreameMowerTaskStatus.DOCKING_PAUSED
                    )
                ):
                    # Map data always contains last active areas
                    render_map_data.active_areas = None

                if self.status.started and not self.status.spot_cleaning:
                    # Map data always contains last active points
                    render_map_data.active_points = None

                if not self.status.segment_cleaning:
                    # Map data always contains last active segments
                    render_map_data.active_segments = None

                if not self.status.cruising:
                    # Map data always contains last active path points
                    render_map_data.active_cruise_points = None

                if self.capability.camera_streaming and render_map_data.predefined_points is None:
                    render_map_data.predefined_points = []
            else:
                if not self.capability.camera_streaming:
                    if render_map_data.active_areas and len(render_map_data.active_areas) == 1:
                        area = render_map_data.active_areas[0]
                        size = render_map_data.dimensions.grid_size
                        if area.check_size(size):
                            x = area.x0 + int(size / 2)
                            y = area.y0 + int(size / 2)
                            render_map_data.task_cruise_points = {
                                1: Coordinate(
                                    x,
                                    y,
                                    False,
                                    0,
                                )
                            }

                            if render_map_data.completed == False:
                                if render_map_data.robot_position:
                                    render_map_data.completed = bool(
                                        render_map_data.robot_position.x >= x - size
                                        and render_map_data.robot_position.x <= x + size
                                        and render_map_data.robot_position.y >= y - size
                                        and render_map_data.robot_position.y <= y + size
                                    )
                                else:
                                    render_map_data.completed = True

                            render_map_data.active_areas = None

                if render_map_data.active_areas or render_map_data.active_points:
                    render_map_data.segments = None

                if render_map_data.customized_cleaning != 1:
                    render_map_data.cleanset = None

                if (
                    render_map_data.cleanup_method is None
                    or render_map_data.cleanup_method != CleanupMethod.CUSTOMIZED_CLEANING
                ):
                    render_map_data.cleanset = None

                if render_map_data.task_cruise_points:
                    render_map_data.active_cruise_points = render_map_data.task_cruise_points.copy()
                    render_map_data.task_cruise_points = True
                    render_map_data.active_areas = None
                    render_map_data.path = None
                    render_map_data.cleanset = None
                    if render_map_data.furnitures is not None:
                        render_map_data.furnitures = {}

                if render_map_data.segments:
                    if render_map_data.task_cruise_points or (
                        render_map_data.cleanup_method is not None
                        and render_map_data.cleanup_method == CleanupMethod.CLEANGENIUS
                    ):
                        for k, v in render_map_data.segments.items():
                            render_map_data.segments[k].order = None
                    elif render_map_data.active_segments:
                        order = 1
                        for segment_id in list(
                            sorted(
                                render_map_data.segments,
                                key=lambda segment_id: (
                                    render_map_data.segments[segment_id].order
                                    if render_map_data.segments[segment_id].order
                                    else 99
                                ),
                            )
                        ):
                            if (
                                len(render_map_data.active_segments) > 1
                                and render_map_data.segments[segment_id].order
                                and segment_id in render_map_data.active_segments
                            ):
                                render_map_data.segments[segment_id].order = order
                                order = order + 1
                            else:
                                render_map_data.segments[segment_id].order = None

                return render_map_data

            if not render_map_data.saved_map and not render_map_data.recovery_map:
                if not self.status._capability.cruising:
                    if self.status.go_to_zone:
                        render_map_data.active_cruise_points = {
                            1: Coordinate(
                                self.status.go_to_zone.x,
                                self.status.go_to_zone.y,
                                False,
                                0,
                            )
                        }
                        render_map_data.active_areas = None
                        render_map_data.path = None

                    if render_map_data.active_areas and len(render_map_data.active_areas) == 1:
                        area = render_map_data.active_areas[0]
                        if area.check_size(render_map_data.dimensions.grid_size):
                            if self.status.started and not self.status.go_to_zone and self.status.zone_cleaning:
                                render_map_data.active_cruise_points = {
                                    1: Coordinate(
                                        area.x0 + int(render_map_data.dimensions.grid_size / 2),
                                        area.y0 + int(render_map_data.dimensions.grid_size / 2),
                                        False,
                                        0,
                                    )
                                }
                            render_map_data.active_areas = None
                            render_map_data.path = None

                if not self.status.go_to_zone and (
                    (self.status.zone_cleaning and render_map_data.active_areas)
                    or (self.status.spot_cleaning and render_map_data.active_points)
                ):
                    # App does not render segments when zone or spot cleaning
                    render_map_data.segments = None

                # App does not render pet obstacles when pet detection turned off
                if render_map_data.obstacles and self.status.ai_pet_detection == 0:
                    obstacles = copy.deepcopy(render_map_data.obstacles)
                    for k, v in obstacles.items():
                        if v.type == ObstacleType.PET:
                            del render_map_data.obstacles[k]

                if render_map_data.furnitures and self.status.ai_furniture_detection == 0:
                    render_map_data.furnitures = {}

                # App adds robot position to paths as last line when map data is line to robot
                if render_map_data.line_to_robot and render_map_data.path and render_map_data.robot_position:
                    render_map_data.path.append(
                        Path(
                            render_map_data.robot_position.x,
                            render_map_data.robot_position.y,
                            PathType.LINE,
                        )
                    )

            if not self.status.customized_cleaning or self.status.cruising or self.status.cleangenius_cleaning:
                # App does not render customized cleaning settings on saved map list
                render_map_data.cleanset = None
            elif (
                not render_map_data.saved_map
                and not render_map_data.recovery_map
                and render_map_data.cleanset is None
                and self.status.customized_cleaning
            ):
                DreameMowerMapDecoder.set_segment_cleanset(render_map_data, {}, self.capability)
                render_map_data.cleanset = True

            if render_map_data.segments:
                if (
                    not self.status.custom_order
                    or self.status.cleangenius_cleaning
                    or render_map_data.saved_map
                    or render_map_data.recovery_map
                ):
                    for k, v in render_map_data.segments.items():
                        render_map_data.segments[k].order = None

            # Device currently may not be docked but map data can be old and still showing when robot is docked
            render_map_data.docked = bool(render_map_data.docked or self.status.docked)

            if (
                not self.capability.lidar_navigation
                and not render_map_data.saved_map
                and not render_map_data.recovery_map
                and render_map_data.saved_map_status == 1
                and render_map_data.docked
            ):
                # For correct scaling of vslam saved map
                render_map_data.saved_map_status = 2

            if (
                render_map_data.charger_position == None
                and render_map_data.docked
                and render_map_data.robot_position
                and not render_map_data.saved_map
                and not render_map_data.recovery_map
            ):
                render_map_data.charger_position = copy.deepcopy(render_map_data.robot_position)
                render_map_data.charger_position.a = render_map_data.robot_position.a + 180

            if render_map_data.saved_map or render_map_data.recovery_map:
                if not render_map_data.recovery_map:
                    render_map_data.virtual_walls = None
                    render_map_data.no_go_areas = None
                    render_map_data.pathways = None
                render_map_data.active_areas = None
                render_map_data.active_points = None
                render_map_data.active_segments = None
                render_map_data.active_cruise_points = None
                render_map_data.path = None
                render_map_data.cleanset = None
            elif render_map_data.charger_position and render_map_data.docked and not self.status.fast_mapping:
                if not render_map_data.robot_position:
                    render_map_data.robot_position = copy.deepcopy(render_map_data.charger_position)
            return render_map_data
        return map_data

    def get_map(self, map_index: int) -> MapData | None:
        """Get stored map data by index from map manager."""
        if self._map_manager:
            if self.status.multi_map:
                return self._map_manager.get_map(map_index)
            if map_index == 1:
                return self._map_manager.selected_map
            if map_index == 0:
                return self.status.current_map

    def update_map(self) -> None:
        """Trigger a map update.
        This function is used for requesting map data when a image request has been made to renderer
        """

        self._last_change = time.time()
        if self._map_manager:
            now = time.time()
            if now - self._last_map_request > 120:
                self._last_map_request = now
                self._map_manager.set_update_interval(self._map_update_interval)
                self._map_manager.schedule_update(0.01)

    def obstacle_image(self, index):
        if self.capability.map:
            map_data = self.status.current_map
            if map_data:
                return self._map_manager.get_obstacle_image(map_data, index)
        return (None, None)

    def obstacle_history_image(self, index, history_index, cruising=False):
        if self.capability.map:
            map_data = self.history_map(history_index, cruising)
            if map_data:
                return self._map_manager.get_obstacle_image(map_data, index)
        return (None, None)

    def history_map(self, index, cruising=False):
        if self.capability.map and index and str(index).isnumeric():
            item = None
            if cruising:
                if self.status._cruising_history and len(self.status._cruising_history) > int(index) - 1:
                    item = self.status._cruising_history[int(index) - 1]
            else:
                if self.status._cleaning_history and len(self.status._cleaning_history) > int(index) - 1:
                    item = self.status._cleaning_history[int(index) - 1]
            if item and item.object_name:
                if item.object_name not in self.status._history_map_data:
                    map_data = self._map_manager.get_history_map(item.object_name, item.key)
                    if map_data is None:
                        return None
                    map_data.last_updated = item.date.timestamp()
                    map_data.completed = item.completed
                    map_data.neglected_segments = item.neglected_segments
                    map_data.second_cleaning = item.second_cleaning
                    map_data.cleaned_area = item.cleaned_area
                    map_data.cleaning_time = item.cleaning_time
                    if item.cleanup_method is not None:
                        map_data.cleanup_method = item.cleanup_method
                    if map_data.cleaning_map_data:
                        map_data.cleaning_map_data.last_updated = item.date.timestamp()
                        map_data.cleaning_map_data.completed = item.completed
                        map_data.cleaning_map_data.neglected_segments = item.neglected_segments
                        map_data.cleaning_map_data.second_cleaning = item.second_cleaning
                        map_data.cleaning_map_data.cleaned_area = item.cleaned_area
                        map_data.cleaning_map_data.cleaning_time = item.cleaning_time
                        map_data.cleaning_map_data.cleanup_method = map_data.cleanup_method
                    self.status._history_map_data[item.object_name] = map_data
                return self.status._history_map_data[item.object_name]

    def recovery_map(self, map_id, index):
        if self.capability.map and map_id and index and str(index).isnumeric():
            if (map_id is None or map_id == "") and self.status.selected_map:
                map_id = self.status.selected_map.map_id

            return self._map_manager.get_recovery_map(map_id, index)

    def request_map(self) -> dict[str, Any] | None:
        """Send map request action to the device.
        Device will upload a new map on cloud after this command if it has a saved map on memory.
        Otherwise this action will timeout when device is spot cleaning or a restored map exists on memory.
        """

        if self._map_manager:
            return self._map_manager.request_new_map()
        return self.call_action(
            DreameMowerAction.REQUEST_MAP,
            [
                {
                    "piid": PIID(DreameMowerProperty.FRAME_INFO, self.property_mapping),
                    "value": '{"frame_type":"I"}',
                }
            ],
        )

    def update_map_data_async(self, parameters: dict[str, Any]):
        """Send update map action to the device."""
        if self._map_manager:
            self._map_manager.schedule_update(10)
            self._property_changed()
            self._last_map_request = time.time()

        parameters = [
            {
                "piid": PIID(DreameMowerProperty.MAP_EXTEND_DATA, self.property_mapping),
                "value": str(json.dumps(parameters, separators=(",", ":"))).replace(" ", ""),
            }
        ]

        def callback(result):
            if result and result.get("code") == 0:
                _LOGGER.info("Send action UPDATE_MAP_DATA async %s", parameters)
                self._last_change = time.time()
            else:
                _LOGGER.error(
                    "Send action failed UPDATE_MAP_DATA async (%s): %s",
                    parameters,
                    result,
                )

            self.schedule_update(5)

            if self._map_manager:
                if self._protocol.dreame_cloud:
                    self._map_manager.schedule_update(3)
                else:
                    self._map_manager.request_next_map()
                    self._last_map_list_request = 0

        mapping = self.action_mapping[DreameMowerAction.UPDATE_MAP_DATA]
        self._protocol.action_async(callback, mapping["siid"], mapping["aiid"], parameters)

    def update_map_data(self, parameters: dict[str, Any]) -> dict[str, Any] | None:
        """Send update map action to the device."""
        if self._map_manager:
            self._map_manager.schedule_update(10)
            self._property_changed()
            self._last_map_request = time.time()

        response = self.call_action(
            DreameMowerAction.UPDATE_MAP_DATA,
            [
                {
                    "piid": PIID(DreameMowerProperty.MAP_EXTEND_DATA, self.property_mapping),
                    "value": str(json.dumps(parameters, separators=(",", ":"))).replace(" ", ""),
                }
            ],
        )

        self.schedule_update(5, True)

        if self._map_manager:
            if self._protocol.dreame_cloud:
                self._map_manager.schedule_update(3)
            else:
                self._map_manager.request_next_map()
                self._last_map_list_request = 0

        return response

    def rename_map(self, map_id: int, map_name: str = "") -> dict[str, Any] | None:
        """Set custom name for a map"""
        if self.status.has_temporary_map:
            raise InvalidActionException("Cannot rename a map when temporary map is present")

        if map_name != "":
            map_name = map_name.replace(" ", "-")
            if self._map_manager:
                self._map_manager.editor.set_map_name(map_id, map_name)
            return self.update_map_data_async({"nrism": {map_id: {"name": map_name}}})

    def set_map_rotation(self, rotation: int, map_id: int = None) -> dict[str, Any] | None:
        """Set rotation of a map"""
        if self.status.has_temporary_map:
            raise InvalidActionException("Cannot rotate a map when temporary map is present")

        if rotation is not None:
            rotation = int(rotation)
            if rotation > 270 or rotation < 0:
                rotation = 0

            if self._map_manager:
                if map_id is None:
                    map_id = self.status.selected_map.map_id
                self._map_manager.editor.set_rotation(map_id, rotation)

            if map_id is not None:
                return self.update_map_data_async({"smra": {map_id: {"ra": rotation}}})

    def set_restricted_zone(self, walls=[], zones=[], no_mops=[]) -> dict[str, Any] | None:
        """Set restricted zones on current saved map."""
        if walls == "":
            walls = []
        if zones == "":
            zones = []
        if no_mops == "":
            no_mops = []

        if self._map_manager:
            self._map_manager.editor.set_zones(walls, zones, no_mops)
        return self.update_map_data_async({"vw": {"line": walls, "rect": zones, "mop": no_mops}})

    def set_pathway(self, pathways=[]) -> dict[str, Any] | None:
        """Set pathways on current saved map."""
        if pathways == "":
            pathways = []

        if self._map_manager:
            if self.status.current_map and not (
                self.status.current_map.pathways is not None or self.capability.floor_material
            ):
                raise InvalidActionException("Pathways are not supported on this device")

            if self.status.current_map and not self.status.has_saved_map:
                raise InvalidActionException("Cannot edit pathways on current map")
            self._map_manager.editor.set_pathways(pathways)

        return self.update_map_data_async({"vws": {"vwsl": pathways}})

    def set_predefined_points(self, points=[]) -> dict[str, Any] | None:
        """Set predefined points on current saved map."""
        if points == "":
            points = []

        if not self.capability.cruising:
            raise InvalidActionException("Predefined points are not supported on this device")

        if self.status.started:
            raise InvalidActionException("Cannot set predefined points while mower is running")

        if self.status.current_map:
            for point in points:
                if not self.status.current_map.check_point(point[0], point[1]):
                    raise InvalidActionException(f"Coordinate ({point[0]}, {point[1]}) is not inside the map")

        predefined_points = []
        for point in points:
            predefined_points.append([point[0], point[1], 0, 1])

        if self._map_manager:
            if self.status.current_map and not self.status.has_saved_map:
                raise InvalidActionException("Cannot edit predefined points on current map")
            self._map_manager.editor.set_predefined_points(predefined_points[:20])

        return self.update_map_data_async({"spoint": predefined_points[:20], "tpoint": []})

    def set_selected_map(self, map_id: int) -> dict[str, Any] | None:
        """Change currently selected map when multi floor map is enabled."""
        if self.status.multi_map:
            self._map_select_time = time.time()
            if self._map_manager:
                self._map_manager.editor.set_selected_map(map_id)
            return self.update_map_data({"sm": {}, "mapid": map_id})

    def delete_map(self, map_id: int = None) -> dict[str, Any] | None:
        """Delete a map."""
        if self.status.has_temporary_map:
            raise InvalidActionException("Cannot delete a map when temporary map is present")

        if self.status.started:
            raise InvalidActionException("Cannot delete a map while mower is running")

        if self._map_manager:
            if map_id == 0:
                map_id = None

            # Device do not deletes saved maps when you disable multi floor map feature
            # but it deletes all maps if you delete any map when multi floor map is disabled.
            if self.status.multi_map:
                if not map_id and self._map_manager.selected_map:
                    map_id = self._map_manager.selected_map.map_id
            else:
                if self._map_manager.selected_map and map_id == self._map_manager.selected_map.map_id:
                    self._map_manager.editor.delete_map()
                else:
                    self._map_manager.editor.delete_map(map_id)
        parameters = {"cm": {}}
        if map_id:
            parameters["mapid"] = map_id
        return self.update_map_data(parameters)

    def save_temporary_map(self) -> dict[str, Any] | None:
        """Replace new map with an old one when multi floor map is disabled."""
        if self.status.has_temporary_map:
            if self._map_manager:
                self._map_manager.editor.save_temporary_map()
            return self.update_map_data({"cw": 5})

    def discard_temporary_map(self) -> dict[str, Any] | None:
        """Discard new map when device have reached maximum number of maps it can store."""
        if self.status.has_temporary_map:
            if self._map_manager:
                self._map_manager.editor.discard_temporary_map()
            return self.update_map_data({"cw": 0})

    def replace_temporary_map(self, map_id: int = None) -> dict[str, Any] | None:
        """Replace new map with an old one when device have reached maximum number of maps it can store."""
        if self.status.has_temporary_map:
            if self.status.multi_map:
                raise InvalidActionException("Cannot replace a map when multi floor map is disabled")

            if self._map_manager:
                self._map_manager.editor.replace_temporary_map(map_id)
            parameters = {"cw": 1}
            if map_id:
                parameters["mapid"] = map_id
            return self.update_map_data(parameters)

    def restore_map_from_file(self, map_url: int, map_id: int = None) -> dict[str, Any] | None:
        map_recovery_status = self.status.map_recovery_status
        if map_recovery_status is None:
            raise InvalidActionException("Map recovery is not supported on this device")

        if map_recovery_status == DreameMapRecoveryStatus.RUNNING.value:
            raise InvalidActionException("Map recovery in progress")

        if map_id is None or map_id == "":
            if self.status.selected_map is None:
                raise InvalidActionException("Map ID is required")

            map_id = self.status.selected_map.map_id

        if self.status.map_data_list and not (map_id in self.status.map_data_list):
            raise InvalidActionException("Map not found")

        if self.status.started:
            raise InvalidActionException("Cannot set restore a map while mower is running")

        self.schedule_update(15)
        if self._map_manager:
            self._last_map_request = time.time()
            self._map_manager.schedule_update(15)

        self._update_property(
            DreameMowerProperty.MAP_RECOVERY_STATUS,
            DreameMapRecoveryStatus.RUNNING.value,
        )
        mapping = self.property_mapping[DreameMowerProperty.MAP_RECOVERY]
        response = self._protocol.set_property(
            mapping["siid"],
            mapping["piid"],
            str(json.dumps({"map_id": map_id, "map_url": map_url}, separators=(",", ":"))).replace(" ", ""),
        )
        if not response or response[0]["code"] != 0:
            self._update_property(DreameMowerProperty.MAP_RECOVERY_STATUS, map_recovery_status)
            raise InvalidActionException("Map recovery failed with error code %s", response[0]["code"])
        self._map_manager.schedule_update(5)
        self.schedule_update(1)
        return response

    def restore_map(self, recovery_map_index: int, map_id: int = None) -> dict[str, Any] | None:
        """Replace a map with previously saved version by device."""
        map_recovery_status = self.status.map_recovery_status
        if map_recovery_status is None:
            raise InvalidActionException("Map recovery is not supported on this device")

        if not self._map_manager:
            raise InvalidActionException("Map recovery requires cloud connection")

        if map_recovery_status == DreameMapRecoveryStatus.RUNNING.value:
            raise InvalidActionException("Map recovery in progress")

        if self.status.started:
            raise InvalidActionException("Cannot set restore a map while mower is running")

        if self.status.has_temporary_map:
            raise InvalidActionException("Restore a map when temporary map is present")

        if (map_id is None or map_id == "") and self.status.selected_map:
            map_id = self.status.selected_map.map_id

        if not map_id or map_id not in self.status.map_data_list:
            raise InvalidActionException("Map not found")

        if len(self.status.map_data_list[map_id].recovery_map_list) <= int(recovery_map_index) - 1:
            raise InvalidActionException("Invalid recovery map index")

        recovery_map_info = self.status.map_data_list[map_id].recovery_map_list[int(recovery_map_index) - 1]
        object_name = recovery_map_info.object_name
        if object_name and object_name != "":
            file, map_url, object_name = self.recovery_map_file(map_id, recovery_map_index)
            if map_url == None:
                raise InvalidActionException("Failed get recovery map file url: %s", object_name)

            if file == None:
                raise InvalidActionException("Failed to download recovery map file: %s", map_url)

            response = self.restore_map_from_file(map_url, map_id)
            if response and response[0]["code"] == 0:
                self._map_manager.editor.restore_map(recovery_map_info)
            return response
        raise InvalidActionException("Invalid recovery map object name")

    def backup_map(self, map_id: int = None) -> dict[str, Any] | None:
        """Save a map map to cloud for later use of restoring."""
        if not self.capability.backup_map:
            raise InvalidActionException("Map backup is not supported on this device")

        if self.status.map_backup_status == DreameMapBackupStatus.RUNNING.value:
            raise InvalidActionException("Map backup in progress")

        if map_id is None or map_id == "":
            if self.status.selected_map is None:
                raise InvalidActionException("Map ID is required")

            map_id = self.status.selected_map.map_id

        if self.status.map_data_list and not (map_id in self.status.map_data_list):
            raise InvalidActionException("Map not found")

        response = self.call_action(
            DreameMowerAction.BACKUP_MAP,
            [
                {
                    "piid": PIID(DreameMowerProperty.MAP_EXTEND_DATA, self.property_mapping),
                    "value": str(map_id),
                }
            ],
        )
        self.schedule_update(3, True)
        if response and response.get("code") == 0:
            self._update_property(
                DreameMowerProperty.MAP_BACKUP_STATUS,
                DreameMapBackupStatus.RUNNING.value,
            )
        return response

    def merge_segments(self, map_id: int, segments: list[int]) -> dict[str, Any] | None:
        """Merge segments on a map"""
        if self.status.has_temporary_map:
            raise InvalidActionException("Cannot edit segments when temporary map is present")

        if segments:
            if map_id == "":
                map_id = None

            if self._map_manager:
                if not map_id:
                    if self.capability.lidar_navigation and self._map_manager.selected_map:
                        map_id = self._map_manager.selected_map.map_id
                    else:
                        map_id = 0
                self._map_manager.editor.merge_segments(map_id, segments)

            if not map_id and self.capability.lidar_navigation:
                raise InvalidActionException("Map ID is required")

            data = {"msr": [segments[0], segments[1]]}
            if map_id:
                data["mapid"] = map_id
            return self.update_map_data(data)

    def split_segments(self, map_id: int, segment: int, line: list[int]) -> dict[str, Any] | None:
        """Split segments on a map"""
        if self.status.has_temporary_map:
            raise InvalidActionException("Cannot edit segments when temporary map is present")

        if segment and line is not None:
            if map_id == "":
                map_id = None

            if self._map_manager:
                if not map_id:
                    if self.capability.lidar_navigation and self._map_manager.selected_map:
                        map_id = self._map_manager.selected_map.map_id
                    else:
                        map_id = 0
                self._map_manager.editor.split_segments(map_id, segment, line)

            if not map_id and self.capability.lidar_navigation:
                raise InvalidActionException("Map ID is required")

            line.append(segment)
            data = {"dsrid": line}
            if map_id:
                data["mapid"] = map_id
            return self.update_map_data(data)

    def set_cleaning_sequence(self, cleaning_sequence: list[int]) -> dict[str, Any] | None:
        """Set cleaning sequence on current map.
        Device will use this order even you specify order in segment cleaning."""

        if not self.capability.customized_cleaning:
            raise InvalidActionException("Cleaning sequence is not supported on this device")

        if self.status.has_temporary_map:
            raise InvalidActionException("Cannot edit segments when temporary map is present")

        if self.status.has_temporary_map:
            raise InvalidActionException("Cannot edit segments when temporary map is present")

        if self.status.started:
            raise InvalidActionException("Cannot set cleaning sequence while mower is running")

        if cleaning_sequence == "" or not cleaning_sequence:
            cleaning_sequence = []

        if self._map_manager:
            if cleaning_sequence and self.status.segments:
                for k in cleaning_sequence:
                    if int(k) not in self.status.segments.keys():
                        raise InvalidValueException("Segment not found! (%s)", k)

            map_data = self.status.current_map
            if map_data and map_data.segments and not map_data.temporary_map:
                if not cleaning_sequence:
                    current = self._map_manager.cleaning_sequence
                    if current and len(current):
                        self.status._previous_cleaning_sequence[map_data.map_id] = current
                    elif map_data.map_id in self.status._previous_cleaning_sequence:
                        del self.status._previous_cleaning_sequence[map_data.map_id]

                cleaning_sequence = self._map_manager.editor.set_cleaning_sequence(cleaning_sequence)

        return self.update_map_data_async({"cleanOrder": cleaning_sequence})

    def set_cleanset(self, cleanset: dict[str, list[int]]) -> dict[str, Any] | None:
        """Set customized cleaning settings on current map.
        Device will use these settings even you pass another setting for custom segment cleaning.
        """

        if self.status.has_temporary_map:
            raise InvalidActionException("Cannot edit segments when temporary map is present")

        if cleanset is not None:
            return self.update_map_data_async({"customeClean": cleanset})

    def set_custom_cleaning(
        self,
        segment_id: list[int],
        cleaning_times: list[int],
        cleaning_mode: list[int] = None,
    ) -> dict[str, Any] | None:
        """Set customized cleaning settings on current map.
        Device will use these settings even you pass another setting for custom segment cleaning.
        """

        if not self.capability.customized_cleaning:
            raise InvalidActionException("Customized cleaning is not supported on this device")

        if self.status.has_temporary_map:
            raise InvalidActionException("Cannot edit customized cleaning parameters when temporary map is present")

        if self.status.started:
            raise InvalidActionException("Cannot edit customized cleaning parameters while mower is running")

        if cleaning_times:
            for v in cleaning_times:
                if int(v) < 1 or int(v) > 3:
                    raise InvalidActionException("Invalid cleaning times: %s", v)

        if cleaning_mode:
            for v in cleaning_mode:
                if int(v) < 0 or int(v) > 2:
                    raise InvalidActionException("Invalid cleaning mode: %s", v)

        if self.capability.map:
            if not self.status.has_saved_map:
                raise InvalidActionException("Cannot edit customized cleaning parameters on current map")

            current_map = self.status.current_map
            if current_map:
                segments = self.status.segments
                index = 0
                for k in segment_id:
                    id = int(k)
                    if not segments or id not in segments:
                        raise InvalidActionException("Invalid Segment ID: %s", id)
                    self._map_manager.editor.set_segment_cleaning_times(id, int(cleaning_times[index]), False)
                    if self.capability.custom_cleaning_mode:
                        self._map_manager.editor.set_segment_cleaning_mode(id, int(cleaning_mode[index]), False)
                    index = index + 1
                self._map_manager.editor.refresh_map()
                return self.set_cleanset(self._map_manager.editor.cleanset(current_map))

        custom_cleaning_mode = self.capability.custom_cleaning_mode
        has_cleaning_mode = cleaning_mode != "" and cleaning_mode is not None
        if (
            segment_id != ""
            and segment_id
            and cleaning_times != ""
            and cleaning_times is not None
        ):
            if has_cleaning_mode and not custom_cleaning_mode:
                raise InvalidActionException(
                    "Setting custom cleaning mode for segments is not supported by the device!"
                )
            elif not has_cleaning_mode and custom_cleaning_mode:
                raise InvalidActionException("Cleaning mode is required")

            if segments:
                count = len(segments.items())
                if (
                    len(segment_id) != count
                    or len(cleaning_times) != count
                    or (custom_cleaning_mode and len(cleaning_mode) != count)
                ):
                    raise InvalidActionException("Parameter count mismatch!")

            custom_cleaning = []
            index = 0

            for id in segment_id:
                values = [
                    id,
                    cleaning_times[index],
                ]
                if custom_cleaning_mode:
                    values.append(cleaning_mode[index])
                    if segments:
                        if id not in segments:
                            raise InvalidActionException("Invalid Segment ID: %s", id)

                custom_cleaning.append(values)
                index = index + 1

            return self.set_cleanset(custom_cleaning)

        raise InvalidActionException("Missing parameters!")

    def set_hidden_segments(self, invisible_segments: list[int]):
        if self.status.has_temporary_map:
            raise InvalidActionException("Cannot edit segments when temporary map is present")

        if self.status.started:
            raise InvalidActionException("Cannot set room visibility while mower is running")

        if invisible_segments == "" or not invisible_segments:
            invisible_segments = []

        if self._map_manager:
            if invisible_segments and self.status.segments:
                for k in invisible_segments:
                    if int(k) not in self.status.segments.keys():
                        raise InvalidValueException("Segment not found! (%s)", k)

            # invisible_segments = self._map_manager.editor.set_invisible_segments(invisible_segments)

        return self.update_map_data_async({"delsr": invisible_segments})

    def set_segment_name(self, segment_id: int, segment_type: int, custom_name: str = None) -> dict[str, Any] | None:
        """Update name of a segment on current map"""
        if self.status.has_temporary_map:
            raise InvalidActionException("Cannot edit segment when temporary map is present")

        if self._map_manager:
            segment_info = self._map_manager.editor.set_segment_name(segment_id, segment_type, custom_name)
            if segment_info:
                data = {"nsr": segment_info}
                if self.status.current_map:
                    data["mapid"] = self.status.current_map.map_id
                if self.capability.auto_rename_segment:
                    data["autonsr"] = True
                return self.update_map_data_async(data)

    def set_segment_order(self, segment_id: int, order: int) -> dict[str, Any] | None:
        """Update cleaning order of a segment on current map"""
        if self._map_manager and not self.status.has_temporary_map:
            if order is None or (isinstance(order, str) and not order.isnumeric()):
                order = 0

            cleaning_order = self._map_manager.editor.set_segment_order(segment_id, order)

            return self.update_map_data_async({"cleanOrder": cleaning_order})

    def set_segment_cleaning_mode(self, segment_id: int, cleaning_mode: int) -> dict[str, Any] | None:
        """Update mop pad humidity of a segment on current map"""
        if self._map_manager and not self.status.has_temporary_map:
            return self.set_cleanset(self._map_manager.editor.set_segment_cleaning_mode(segment_id, cleaning_mode))

    def set_segment_cleaning_route(self, segment_id: int, cleaning_route: int) -> dict[str, Any] | None:
        """Update cleaning route of a segment on current map"""
        if (
            self.capability.cleaning_route
            and self._map_manager
            and not self.status.has_temporary_map
        ):
            return self.set_cleanset(self._map_manager.editor.set_segment_cleaning_route(segment_id, cleaning_route))

    def set_segment_cleaning_times(self, segment_id: int, cleaning_times: int) -> dict[str, Any] | None:
        """Update cleaning times of a segment on current map."""
        if self.status.started:
            raise InvalidActionException("Cannot set room cleaning times while mower is running")

        if self._map_manager and not self.status.has_temporary_map:
            return self.set_cleanset(self._map_manager.editor.set_segment_cleaning_times(segment_id, cleaning_times))

    def set_segment_floor_material(
        self, segment_id: int, floor_material: int, direction: int = None
    ) -> dict[str, Any] | None:
        """Update floor material of a segment on current map"""
        if self._map_manager and not self.status.has_temporary_map:
            if not self.capability.floor_direction_cleaning:
                direction = None
            else:
                if floor_material != 1:
                    direction = None
                elif direction is None:
                    segment = self.status.segments[segment_id]
                    direction = (
                        segment.floor_material_rotated_direction
                        if segment.floor_material_rotated_direction is not None
                        else (
                            0
                            if self.status.current_map.rotation == 0 or self.status.current_map.rotation == 90
                            else 90
                        )
                    )

            data = {"nsm": self._map_manager.editor.set_segment_floor_material(segment_id, floor_material, direction)}
            if self.status.selected_map:
                data["map_id"] = self.status.selected_map.map_id
            return self.update_map_data_async(data)

    def set_segment_floor_material_direction(
        self, segment_id: int, floor_material_direction: int
    ) -> dict[str, Any] | None:
        """Update floor material direction of a segment on current map"""
        if self.capability.floor_direction_cleaning and self._map_manager and not self.status.has_temporary_map:
            data = {
                "nsm": self._map_manager.editor.set_segment_floor_material(segment_id, 1, floor_material_direction)
            }
            if self.status.selected_map:
                data["map_id"] = self.status.selected_map.map_id
            return self.update_map_data_async(data)

    def set_segment_visibility(self, segment_id: int, visibility: int) -> dict[str, Any] | None:
        """Update visibility a segment on current map"""
        if self.capability.segment_visibility and self._map_manager and not self.status.has_temporary_map:
            data = {"delsr": self._map_manager.editor.set_segment_visibility(segment_id, int(visibility))}
            # if self.status.selected_map:
            #    data["map_id"] = self.status.selected_map.map_id
            return self.update_map_data_async(data)

    def recovery_map_file(self, map_id, index):
        if self.capability.map and map_id and index and str(index).isnumeric():
            if (map_id is None or map_id == "") and self.status.selected_map:
                map_id = self.status.selected_map.map_id

            return self._map_manager.get_recovery_map_file(map_id, index)
