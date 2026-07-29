"""Legacy map acquisition, queueing, and lifecycle management."""

from __future__ import annotations
import io
import math
import time
import base64
import json
import zlib
import re
import logging
import traceback
import copy
import numpy as np
import hashlib
import textwrap
from datetime import datetime
from py_mini_racer import MiniRacer
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from Crypto.Util.Padding import unpad
from PIL import (
    Image,
    ImageDraw,
    ImageOps,
    ImageFont,
    ImageEnhance,
    PngImagePlugin,
    ImageFilter,
)
from typing import Any, Mapping
from time import sleep
from io import BytesIO
from typing import Optional, Tuple
from functools import cmp_to_key
from threading import Timer
from .protocol import DreameMowerProtocol
from .exceptions import DeviceUpdateFailedException
from .map_decoder import DreameMowerMapDecoder
from .map_json_renderer import DreameMowerMapDataJsonRenderer
from .map_optimizer import DreameMowerMapOptimizer
from .map_editor import DreameMapMowerMapEditor
from .device_types import (
    PIID,
    DIID,
    DreameMowerProperty,
    DreameMowerAction,
    DreameMowerActionMapping,
    DreameMowerDeviceCapability,
    RobotType,
    CleansetType,
    ObstacleType,
    FurnitureType,
    PathType,
    ObstacleIgnoreStatus,
    SEGMENT_TYPE_CODE_TO_NAME,
    SEGMENT_TYPE_CODE_TO_HA_ICON,
    FURNITURE_TYPE_TO_DIMENSIONS,
    FURNITURE_V2_TYPE_TO_DIMENSIONS,
)
from .map_renderer_types import (
    ALine,
    MAP_COLOR_SCHEME_LIST,
    MAP_ICON_SET_LIST,
    Angle,
    CLine,
    MapRendererColorScheme,
    MapRendererConfig,
    MapRendererData,
    MapRendererLayer,
    MapRendererResources,
    Paths,
)
from .map_types import (
    Area,
    CleanupMethod,
    Coordinate,
    Furniture,
    MapData,
    MapDataPartial,
    MapFrameType,
    MapImageDimensions,
    MapPixelType,
    Obstacle,
    Path,
    Point,
    RecoveryMapInfo,
    RecoveryMapType,
    Segment,
    StartupMethod,
    TaskEndType,
    Wall,
)
from .const import (
    MAP_PARAMETER_NAME,
    MAP_PARAMETER_VALUE,
    MAP_PARAMETER_TIME,
    MAP_PARAMETER_CODE,
    MAP_PARAMETER_OUT,
    MAP_PARAMETER_MAP,
    MAP_PARAMETER_ANGLE,
    MAP_PARAMETER_MAPSTR,
    MAP_PARAMETER_CURR_ID,
    MAP_PARAMETER_MOWER,
    MAP_PARAMETER_EXPIRES_TIME,
    MAP_PARAMETER_URL,
    MAP_REQUEST_PARAMETER_MAP_ID,
    MAP_REQUEST_PARAMETER_FRAME_ID,
    MAP_REQUEST_PARAMETER_FRAME_TYPE,
    MAP_REQUEST_PARAMETER_REQ_TYPE,
    MAP_REQUEST_PARAMETER_FORCE_TYPE,
    MAP_REQUEST_PARAMETER_TYPE,
    MAP_REQUEST_PARAMETER_INDEX,
    MAP_REQUEST_PARAMETER_ZONE_ID,
    MAP_DATA_JSON_CLASS,
    MAP_DATA_JSON_PARAMETER_CLASS,
    MAP_DATA_JSON_PARAMETER_SIZE,
    MAP_DATA_JSON_PARAMETER_X,
    MAP_DATA_JSON_PARAMETER_Y,
    MAP_DATA_JSON_PARAMETER_PIXEL_SIZE,
    MAP_DATA_JSON_PARAMETER_LAYERS,
    MAP_DATA_JSON_PARAMETER_ENTITIES,
    MAP_DATA_JSON_PARAMETER_META_DATA,
    MAP_DATA_JSON_PARAMETER_VERSION,
    MAP_DATA_JSON_PARAMETER_ROTATION,
    MAP_DATA_JSON_PARAMETER_TYPE,
    MAP_DATA_JSON_PARAMETER_POINTS,
    MAP_DATA_JSON_PARAMETER_PIXELS,
    MAP_DATA_JSON_PARAMETER_SEGMENT_ID,
    MAP_DATA_JSON_PARAMETER_ACTIVE,
    MAP_DATA_JSON_PARAMETER_NAME,
    MAP_DATA_JSON_PARAMETER_DIMENSIONS,
    MAP_DATA_JSON_PARAMETER_MIN,
    MAP_DATA_JSON_PARAMETER_MAX,
    MAP_DATA_JSON_PARAMETER_MID,
    MAP_DATA_JSON_PARAMETER_AVG,
    MAP_DATA_JSON_PARAMETER_PIXEL_COUNT,
    MAP_DATA_JSON_PARAMETER_COMPRESSED_PIXELS,
    MAP_DATA_JSON_PARAMETER_ROBOT_POSITION,
    MAP_DATA_JSON_PARAMETER_CHARGER_POSITION,
    MAP_DATA_JSON_PARAMETER_NO_GO_AREA,
    MAP_DATA_JSON_PARAMETER_ACTIVE_ZONE,
    MAP_DATA_JSON_PARAMETER_VIRTUAL_WALL,
    MAP_DATA_JSON_PARAMETER_PATH,
    MAP_DATA_JSON_PARAMETER_FLOOR,
    MAP_DATA_JSON_PARAMETER_WALL,
    MAP_DATA_JSON_PARAMETER_SEGMENT,
)

_LOGGER = logging.getLogger(__name__)


class DreameMapMowerMapManager:
    def __init__(self, _protocol: DreameMowerProtocol) -> None:
        self._map_list_object_name: str = None
        self._map_list_md5: str = None
        self._recovery_map_list_object_name: str = None
        self._update_callback = None
        self._change_callback = None
        self._error_callback = None
        self._update_timer: Timer = None
        self._update_running: bool = False
        self._update_interval: float = 10
        self._device_running: bool = False
        self._device_docked: bool = False
        self._available: bool = False
        self._disconnected: bool = False
        self._ready: bool = False
        self._connected: bool = True
        self._vslam_map: bool = False

        self._init_data()

        self._protocol = _protocol
        self.editor = DreameMapMowerMapEditor(self)
        self.optimizer = DreameMowerMapOptimizer()

    def _init_data(self) -> None:
        self._map_data: MapData = None
        self._current_frame_id: int = None
        self._current_map_id: int = None
        self._current_timestamp_ms: int = None
        self._file_urls: dict[str, str] = {}
        self._saved_map_data: dict[int, MapData] = {}
        self._map_list: list[int] = []
        self._need_map_request: bool = False
        self._need_map_list_request: bool = None
        self._need_recovery_map_list_request: bool = None
        self._map_data_queue: dict[int, MapData] = {}
        self._updated_frame_id: int = None
        self._selected_map_id: int = None
        self._request_queue: dict[str, bool] = {}
        self._latest_map_data_time: int = None
        self._latest_object_name_time: int = None
        self._latest_map_timestamp_ms: int = None
        self._latest_map_id: int = None
        self._last_p_request_map_id: int = None
        self._last_p_request_frame_id: int = None
        self._last_p_request_time: int = None
        self._last_robot_time: int = None
        self._map_request_time: int = None
        self._map_request_count: int = 0
        self._new_map_request_time: int = None
        self._aes_iv: str = None
        self._capability: DreameMowerDeviceCapability = None

    def _request_map_from_cloud(self) -> bool:
        if self._protocol.cloud.dreame_cloud:
            return True

        if self._current_timestamp_ms is not None:
            start_time = self._current_timestamp_ms
            request_start_time = int(math.floor(start_time / 1000.0))
        else:
            request_start_time = 0
            if self._latest_object_name_time is not None:
                request_start_time = self._latest_object_name_time
            elif self._map_request_time is not None:
                request_start_time = self._map_request_time
            elif self._last_robot_time is not None:
                request_start_time = int(self._last_robot_time / 1000)

        if self._latest_map_data_time is None or self._latest_map_data_time < request_start_time:
            self._latest_map_data_time = request_start_time

        if self._latest_object_name_time is None or self._latest_object_name_time < request_start_time:
            self._latest_object_name_time = request_start_time

        map_data_result = self._protocol.cloud.get_device_property(
            DIID(DreameMowerProperty.MAP_DATA), 20, self._latest_map_data_time
        )

        if not self._protocol.cloud.connected:
            if self._connected:
                self._connected = False
                self._map_data_changed()
            return False
        elif not self._connected:
            self._connected = True
            self._map_data_changed()

        if map_data_result is None:
            _LOGGER.warn("Getting map_data from cloud failed")
            map_data_result = []

        object_name_result = self._protocol.cloud.get_device_property(
            DIID(DreameMowerProperty.OBJECT_NAME), 1, self._latest_object_name_time
        )
        if object_name_result is None:
            _LOGGER.warn("Getting object_name from cloud failed")

        partial_map_data = None
        if len(map_data_result):
            partial_map_data = []
            self._latest_map_data_time = map_data_result[0][MAP_PARAMETER_TIME] + 1

            for data in map_data_result:
                partial_map_data.append(
                    self._decode_map_partial(
                        json.loads(data[MAP_PARAMETER_VALUE if MAP_PARAMETER_VALUE in data else "val"])[0],
                        data[MAP_PARAMETER_TIME] * 1000 if data.get(MAP_PARAMETER_TIME) else None,
                    )
                )

        object_name = None
        object_name_timestamp = None
        if object_name_result:
            data = object_name_result[0]
            if MAP_PARAMETER_TIME in data:
                timestamp = data[MAP_PARAMETER_TIME]
                self._latest_object_name_time = timestamp + 1

            if len(object_name_result) == 1:
                object_name = json.loads(data[MAP_PARAMETER_VALUE if MAP_PARAMETER_VALUE in data else "val"])[0]
                if timestamp:
                    object_name_timestamp = timestamp * 1000

        self._add_cloud_map_data(partial_map_data, object_name, object_name_timestamp)
        return len(map_data_result) or object_name is not None

    def _request_map(self, parameters: dict[str, Any] = None) -> dict[str, Any] | None:
        if parameters is None:
            parameters = {
                MAP_REQUEST_PARAMETER_FRAME_TYPE: MapFrameType.I.name,
            }

        payload = [
            {
                "piid": PIID(DreameMowerProperty.FRAME_INFO),
                MAP_PARAMETER_VALUE: str(json.dumps(parameters, separators=(",", ":"))).replace(" ", ""),
            }
        ]

        try:
            _LOGGER.debug("DreameMapMowerMapManager._request_map %s", payload)
            mapping = DreameMowerActionMapping[DreameMowerAction.REQUEST_MAP]
            return self._protocol.action(mapping["siid"], mapping["aiid"], payload, 0)
        except Exception as ex:
            _LOGGER.warning("DreameMapMowerMapManager._request_map failed: %s", ex)
        return None

    def _map_action_succeeded(self, result: Any) -> bool:
        """Return whether an app action response contains a successful map payload."""
        if not isinstance(result, Mapping):
            if result is not None:
                _LOGGER.debug("Ignoring non-mapping map response: %s", result)
            return False
        return result.get(MAP_PARAMETER_CODE) == 0

    def _request_i_map(self, start_time: int = None) -> bool:
        if not self._request_i_map_available and not self._protocol.dreame_cloud:
            return self.request_new_map()

        parameters = {
            MAP_REQUEST_PARAMETER_REQ_TYPE: 1,
            MAP_REQUEST_PARAMETER_FRAME_TYPE: MapFrameType.I.name,
            MAP_REQUEST_PARAMETER_FORCE_TYPE: 1,
        }

        if start_time:
            parameters[MAP_PARAMETER_TIME] = start_time

        result = self._request_map(parameters)
        if self._map_action_succeeded(result):
            out = result[MAP_PARAMETER_OUT]
            _LOGGER.debug("Response from device %s", out)
            has_map = False
            object_name = None
            raw_map_data = None
            for prop in out:
                value = prop.get(MAP_PARAMETER_VALUE)
                if value is None:
                    _LOGGER.debug(
                        "Map response property has no value field: %s",
                        prop,
                    )
                    continue
                if value != "":
                    piid = prop["piid"]
                    if piid == PIID(DreameMowerProperty.OBJECT_NAME):
                        has_map = True
                        object_name = value
                    elif piid == PIID(DreameMowerProperty.MAP_DATA):
                        has_map = True
                        raw_map_data = value
                    elif piid == PIID(DreameMowerProperty.ROBOT_TIME):
                        self._last_robot_time = int(value)
                        if start_time is None:
                            self._map_request_time = self._last_robot_time
                            self._map_request_count = 1
                    elif piid == PIID(DreameMowerProperty.OLD_MAP_DATA):
                        if not has_map:
                            values = value.split(",")
                            if values[0] == "0":
                                raw_map_data = values[1]
                            else:
                                object_name = values[1]
                                if len(values) == 3:
                                    object_name = f"{object_name},{values[2]}"

            if has_map:
                self._latest_object_name_time = int(self._last_robot_time / 1000) + 1
                self._map_request_time = None

            if object_name:
                self._add_map_data_file(object_name, self._last_robot_time)
                return True
            if raw_map_data:
                self._add_raw_map_data(raw_map_data, self._last_robot_time)
                return True
            return False

        self._request_map_from_cloud()
        return False

    def _request_missing_p_map(self) -> bool:
        if self._map_data is None:
            return

        if self._partial_map_queue_size() == 0:
            return

        frame_id = self._current_frame_id + 1
        map_id = self._current_map_id

        if (
            self._last_p_request_time is not None
            and self._last_p_request_map_id == map_id
            and self._last_p_request_frame_id == frame_id
            and (time.time() - self._last_p_request_time) < 3
        ):
            return

        self._last_p_request_map_id = map_id
        self._last_p_request_frame_id = frame_id
        self._last_p_request_time = time.time()

        _LOGGER.info("Request missing P map: %s", frame_id)
        result = self._request_map(
            {
                MAP_REQUEST_PARAMETER_MAP_ID: map_id,
                MAP_REQUEST_PARAMETER_FRAME_ID: frame_id,
                MAP_REQUEST_PARAMETER_FRAME_TYPE: MapFrameType.P.name,
            }
        )
        return self._map_action_succeeded(result)

    def _request_next_p_map(self, map_id: int, frame_id: int) -> bool:
        key = f"{map_id}:{frame_id}"
        if key in self._request_queue and self._request_queue[key]:
            return

        self._request_queue[key] = True
        _LOGGER.info("Request next P map: %s", frame_id)
        result = self._request_map(
            {
                MAP_REQUEST_PARAMETER_MAP_ID: map_id,
                MAP_REQUEST_PARAMETER_REQ_TYPE: 1,
                MAP_REQUEST_PARAMETER_FRAME_ID: frame_id,
                MAP_REQUEST_PARAMETER_FRAME_TYPE: MapFrameType.P.name,
            }
        )
        if self._map_action_succeeded(result):
            del self._request_queue[key]

            object_name = None
            raw_map_data = None
            timestamp = None

            for prop in result[MAP_PARAMETER_OUT]:
                value = prop.get(MAP_PARAMETER_VALUE)
                if value is None:
                    _LOGGER.debug(
                        "P-map response property has no value field: %s",
                        prop,
                    )
                    continue
                if value != "":
                    piid = prop["piid"]
                    if piid == PIID(DreameMowerProperty.OBJECT_NAME):
                        object_name = value
                    elif piid == PIID(DreameMowerProperty.MAP_DATA):
                        raw_map_data = value
                    elif piid == PIID(DreameMowerProperty.ROBOT_TIME):
                        timestamp = int(value)

            if object_name:
                self._add_map_data_file(object_name, timestamp)
            if raw_map_data:
                _LOGGER.info("Lost P map received: %s:%s", map_id, frame_id)
                self._add_raw_map_data(raw_map_data, timestamp)

            if not raw_map_data and self._vslam_map and not object_name:
                self.request_new_map()
                return False
            return True
        return False

    def _request_t_map(self) -> None:
        result = self._request_map({MAP_REQUEST_PARAMETER_FRAME_TYPE: "T"})
        if self._map_action_succeeded(result):
            self.request_map_list()

    def _request_w_map(self) -> None:
        try:
            _LOGGER.info("Request wifi map from device")
            mapping = DreameMowerActionMapping[DreameMowerAction.WIFI_MAP]
            return self._protocol.action(mapping["siid"], mapping["aiid"], None, 0)
        except Exception as ex:
            _LOGGER.warning("Send _request_w_map failed: %s", ex)
        return None

    def _request_current_map(self, map_request_time: int = None) -> bool:
        if self._request_i_map_available or self._protocol.dreame_cloud:
            return self._request_i_map(map_request_time)

        return self._request_map_from_cloud()

    def _map_data_updated(self) -> None:
        if self._update_callback:
            _LOGGER.debug("Update callback")
            self._update_callback()

    def _map_data_changed(self) -> None:
        if self._change_callback:
            _LOGGER.debug("Change callback")
            self._change_callback()

    def _update_task(self) -> None:
        if self._update_timer is not None:
            self._update_timer.cancel()
            self._update_timer = None

        start = time.time()
        try:
            self.update()
        except Exception as ex:
            _LOGGER.warning("Background map update failed: %s", ex)
        finally:
            self.schedule_update(max(self._update_interval - (time.time() - start), 1))

    def _queue_partial_map(self, map_data) -> None:
        if map_data.map_id != self._latest_map_id:
            return
        next_frame_id = 0

        if self._current_map_id is not None and self._current_map_id == self._latest_map_id:
            next_frame_id = self._current_frame_id + 1

        if map_data.map_id not in self._map_data_queue:
            self._map_data_queue[map_data.map_id] = {}

        if map_data.frame_id < next_frame_id:
            return
        self._map_data_queue[map_data.map_id][map_data.frame_id] = map_data

    def _delete_invalid_partial_maps(self) -> None:
        if self._latest_map_id is None:
            return

        if self._current_frame_id is None:
            return

        frame_id = self._current_frame_id
        map_data_queue = copy.deepcopy(self._map_data_queue)
        for k, v in map_data_queue.items():
            if k != self._latest_map_id:
                del self._map_data_queue[k]

        if self._latest_map_id not in self._map_data_queue or not self._map_data_queue[self._latest_map_id]:
            return

        map_data_queue = copy.deepcopy(self._map_data_queue[self._latest_map_id])
        for k, v in map_data_queue.items():
            if k <= frame_id:
                del self._map_data_queue[self._latest_map_id][k]

    def _unqueue_next_partial_map(self) -> MapData | None:
        if (
            self._latest_map_id is None
            or self._current_frame_id is None
            or self._current_map_id != self._latest_map_id
        ):
            return

        frame_id = self._current_frame_id + 1
        if (
            self._latest_map_id not in self._map_data_queue
            or not self._map_data_queue[self._latest_map_id]
            or frame_id not in self._map_data_queue[self._latest_map_id]
        ):
            return

        map_data = self._map_data_queue[self._latest_map_id][frame_id]

        if map_data:
            del self._map_data_queue[self._latest_map_id][frame_id]
            return map_data

    def _unqueue_partial_map(self, map_id: int, frame_id: int) -> MapData | None:
        if (
            map_id in self._map_data_queue
            and self._map_data_queue[map_id]
            and frame_id in self._map_data_queue[map_id]
        ):
            map_data = self._map_data_queue[map_id][frame_id]
            del self._map_data_queue[map_id][frame_id]
            return map_data

    def _partial_map_queue_size(self) -> int:
        if self._latest_map_timestamp_ms is None:
            return 0

        if self._latest_map_id not in self._map_data_queue or not self._map_data_queue[self._latest_map_id]:
            return 0

        return len(self._map_data_queue[self._latest_map_id])

    def _get_object_file_data(self, object_name: str = "", timestamp=None) -> Tuple[Any, Optional[str]]:
        key = None
        if object_name and "," in object_name:
            values = object_name.split(",")
            object_name = values[0]
            key = values[1]
        response = self._get_interim_file_data(object_name, timestamp)
        return response, key

    def _get_interim_file_data(self, object_name: str = "", timestamp=None) -> str | None:
        if self._protocol.cloud.logged_in:
            if object_name is None or object_name == "":
                _LOGGER.info("Get object name from cloud")
                if self._protocol.cloud.dreame_cloud:
                    object_name_result = self._protocol.cloud.get_properties(DIID(DreameMowerProperty.OBJECT_NAME))
                    if object_name_result:
                        object_name_result = object_name_result[0][MAP_PARAMETER_VALUE]
                        object_name = object_name_result[0]
                else:
                    object_name_result = self._protocol.cloud.get_device_property(
                        DIID(DreameMowerProperty.OBJECT_NAME)
                    )
                    if object_name_result:
                        object_name_result = json.loads(object_name_result[0][MAP_PARAMETER_VALUE])
                        object_name = object_name_result[0]

            if object_name is None or object_name == "":
                object_name = self._protocol.cloud.object_name

            url = self._get_file_url(object_name)
            if url:
                _LOGGER.info("Request map data from cloud %s", url)
                response = self._protocol.cloud.get_file(url)
                if response is not None:
                    return response
                _LOGGER.warning("Request map data from cloud failed %s", url)
                if self._file_urls.get(object_name):
                    del self._file_urls[object_name]

    def _get_file_url(self, object_name: str, interim: bool = True) -> str | None:
        url = None
        now = int(round(time.time()))
        if self._file_urls and self._file_urls.get(object_name):
            object = self._file_urls[object_name]
            if object[MAP_PARAMETER_EXPIRES_TIME] - now > 60:
                url = f"{object[MAP_PARAMETER_URL]}&current={str(now)}"

        if url is None:
            response = (
                self._protocol.cloud.get_interim_file_url(object_name)
                if interim
                else self._protocol.cloud.get_file_url(object_name)
            )
            if response:
                self._file_urls[object_name] = {
                    MAP_PARAMETER_URL: response,
                    MAP_PARAMETER_EXPIRES_TIME: now + (30 * 60),
                }
                url = self._file_urls[object_name][MAP_PARAMETER_URL]
        return url

    def _decode_map_partial(self, raw_map, timestamp=None, key=None) -> MapDataPartial | None:
        partial_map = DreameMowerMapDecoder.decode_map_partial(raw_map, self._aes_iv, key)
        if partial_map is not None:
            # After restart or unsuccessful start robot returns timestamp_ms as uptime and that messes up with the latest map/frame id detection.
            # I could not figure out how app handles with this issue but i have added this code to update time stamp as request/object time.

            if timestamp and (partial_map.timestamp_ms is None or partial_map.timestamp_ms < 1577826000000):
                partial_map.timestamp_ms = timestamp

            if self._latest_map_timestamp_ms is None or partial_map.timestamp_ms > self._latest_map_timestamp_ms:
                self._latest_map_timestamp_ms = partial_map.timestamp_ms
                self._latest_map_id = partial_map.map_id

        return partial_map

    def _add_cloud_map_data(self, partial_map_data, object_name, object_name_timestamp):
        if partial_map_data:
            for partial_map in partial_map_data:
                if partial_map.frame_type == MapFrameType.I.value:
                    self._add_map_data(partial_map)
                else:
                    self._queue_partial_map(partial_map)

        next_frame_id = 1
        if self._current_frame_id:
            next_frame_id = self._current_frame_id + 1

        if (
            not self._add_map_data(self._unqueue_partial_map(self._latest_map_id, next_frame_id))
            and object_name is None
        ):
            self._delete_invalid_partial_maps()
            tmpLen = self._partial_map_queue_size()
            if tmpLen > 8:
                if self._protocol.dreame_cloud:
                    self._request_map()
                else:
                    self.request_new_map()
            elif tmpLen > 4:
                self._request_missing_p_map()
            elif tmpLen > 0 and partial_map_data and len(partial_map_data) > 0:
                self._request_next_p_map(self._latest_map_id, next_frame_id)

        if object_name is not None:
            _LOGGER.info("New object name received: %s", object_name)
            response, key = self._get_object_file_data(object_name, object_name_timestamp)
            if response:
                partial_map = self._decode_map_partial(response.decode(), object_name_timestamp, key)
                if partial_map:
                    if self._map_data is None or partial_map.frame_type == MapFrameType.I.value:
                        return self._add_map_data(partial_map)

                    self._queue_partial_map(partial_map)
                    next_partial_map = self._unqueue_next_partial_map()
                    if next_partial_map:
                        self._add_map_data(next_partial_map)
                    else:
                        self._delete_invalid_partial_maps()
                        if self._partial_map_queue_size() > 8:
                            if self._protocol.dreame_cloud:
                                self._request_map()
                            else:
                                self.request_new_map()

    def _add_map_data_file(self, object_name: str, timestamp) -> None:
        response, key = self._get_object_file_data(object_name, timestamp)
        if response is not None:
            self._add_raw_map_data(response.decode(), timestamp, key)

    def _add_raw_map_data(self, raw_map: str, timestamp=None, key=None) -> bool:
        return self._add_map_data(self._decode_map_partial(raw_map, timestamp, key))

    def _add_map_data(self, partial_map: MapDataPartial) -> None:
        if partial_map is None:
            return False

        if (
            partial_map.timestamp_ms is not None
            and self._current_timestamp_ms is not None
            and self._current_frame_id
            and self._current_timestamp_ms > partial_map.timestamp_ms
        ):
            _LOGGER.debug(
                "Skip frame %s, timestamp %s:%s < %s:%s",
                partial_map.frame_type,
                partial_map.frame_id,
                partial_map.timestamp_ms,
                self._current_frame_id,
                self._current_timestamp_ms,
            )
            return True

        if self._current_map_id is not None and self._current_map_id != self._latest_map_id:
            _LOGGER.info(
                "Map ID Changed: %s -> %s",
                self._current_map_id,
                self._latest_map_id,
            )

            self._current_frame_id = None
            self._current_map_id = None
            self._updated_frame_id = None
            # self.request_next_map_list()

        if partial_map.map_id != self._latest_map_id:
            _LOGGER.info(
                "Skip frame, map_id %s != %s",
                partial_map.map_id,
                self._latest_map_id,
            )
            # self._add_next_map_data()
            return True

        if (
            self._current_frame_id is not None
            and self._current_frame_id is not None
            and partial_map.frame_id < self._current_frame_id
        ):
            if (
                partial_map.frame_type != MapFrameType.I.value
                or partial_map.timestamp_ms <= self._current_timestamp_ms
            ):
                _LOGGER.info(
                    "Skip frame, frame id %s:%s < %s:%s",
                    partial_map.map_id,
                    partial_map.frame_id,
                    self._current_map_id,
                    self._current_frame_id,
                )
                # self._add_next_map_data()
                return True

        if partial_map.frame_type == MapFrameType.P.value:
            if self._current_frame_id is not None and self._map_data is not None and self._map_data.restored_map:
                _LOGGER.debug("Current map data removed")
                self._map_data = None
                self._current_frame_id = None
                self._current_map_id = None

            if self._current_frame_id is None or self._map_data is None:
                self._queue_partial_map(partial_map)

                if self._map_request_time is None:
                    self._request_i_map()
                    return True

            if partial_map.frame_id != self._current_frame_id + 1:
                if partial_map.frame_id <= self._current_frame_id:
                    self._add_next_map_data()
                    return True

                self._queue_partial_map(partial_map)
                self._delete_invalid_partial_maps()

                tmpLen = self._partial_map_queue_size()
                if tmpLen > 0:
                    if self._protocol.dreame_cloud:
                        if tmpLen > 8:
                            self._request_map()
                        elif tmpLen > 4:
                            self._request_missing_p_map()
                        else:
                            next_frame_id = 1
                            if self._current_frame_id:
                                next_frame_id = self._current_frame_id + 1
                            self._request_next_p_map(self._latest_map_id, next_frame_id)
                    else:
                        self._request_next_p_map(partial_map.map_id, self._current_frame_id + 1)
                else:
                    self._add_next_map_data()
                return True

            current_robot_position = (
                copy.deepcopy(self._map_data.robot_position) if self._map_data.robot_position else None
            )

            map_data = DreameMowerMapDecoder.decode_p_map_data_from_partial(
                partial_map,
                self._map_data,
                self._vslam_map,
            )
            if map_data:
                self._map_data = map_data
                self._map_data.last_updated = time.time()
                self._updated_frame_id = None
                self._current_frame_id = map_data.frame_id
                self._current_map_id = map_data.map_id
                self._current_timestamp_ms = map_data.timestamp_ms

                _LOGGER.info("Decode P map %d %d", map_data.map_id, map_data.frame_id)

                if not self._device_running or current_robot_position != map_data.robot_position:
                    self._map_data_changed()

        elif partial_map.frame_type == MapFrameType.I.value:
            self._need_map_request = False
            self._delete_invalid_partial_maps()

            (
                map_data,
                saved_map_data,
            ) = DreameMowerMapDecoder.decode_map_data_from_partial(partial_map, self._vslam_map)
            if map_data is None:
                self._add_next_map_data()
                return True

            if map_data.empty_map:
                if self._map_data is None or not self._map_data.empty_map:
                    self._init_data()
                    self._map_data = map_data
                    self._current_frame_id = map_data.frame_id
                    self._current_map_id = map_data.map_id
                    self._current_timestamp_ms = map_data.timestamp_ms

                    self._map_data_changed()
                self._add_next_map_data()
                return True

            if saved_map_data is not None and saved_map_data.saved_map:
                if saved_map_data.map_id in self._saved_map_data:
                    map_data.temporary_map = False
                    self._selected_map_id = saved_map_data.map_id
                    saved_map_data.map_name = self._saved_map_data[saved_map_data.map_id].map_name
                    saved_map_data.custom_name = self._saved_map_data[saved_map_data.map_id].custom_name
                    saved_map_data.rotation = self._saved_map_data[saved_map_data.map_id].rotation
                    saved_map_data.map_index = self._saved_map_data[saved_map_data.map_id].map_index
                    saved_map_data.recovery_map_list = self._saved_map_data[saved_map_data.map_id].recovery_map_list

                    saved_map_data.timestamp_ms = map_data.timestamp_ms
                    if (
                        saved_map_data != self._saved_map_data[saved_map_data.map_id]
                        or saved_map_data.segments != self._saved_map_data[saved_map_data.map_id].segments
                    ):
                        saved_map_data.last_updated = time.time()
                        if saved_map_data.wifi_map_data:
                            saved_map_data.wifi_map_data.last_updated = saved_map_data.last_updated
                        self._saved_map_data[saved_map_data.map_id] = saved_map_data

                        _LOGGER.debug(
                            "Decode saved map %s: %s",
                            saved_map_data.map_id,
                            saved_map_data.map_name,
                        )
                elif not map_data.temporary_map:
                    if not self._map_list:
                        saved_map_data.last_updated = time.time()
                        if saved_map_data.wifi_map_data:
                            saved_map_data.wifi_map_data.last_updated = saved_map_data.last_updated
                        self._saved_map_data[saved_map_data.map_id] = saved_map_data

                        _LOGGER.info("Add saved map from new map %s", saved_map_data.map_id)
                        self._refresh_map_list()
                        if self._map_data:
                            self._map_data_changed()

                    if self._device_running:
                        self.request_next_map_list()
                    else:
                        self.request_map_list()

            DreameMowerMapDecoder.set_segment_cleanset(map_data, map_data.cleanset, self._capability)

            if not map_data.saved_map:
                if self._vslam_map:
                    if map_data.saved_map_status == 1 and saved_map_data and self._device_docked:
                        map_data.segments = copy.deepcopy(saved_map_data.segments)
                        map_data.data = copy.deepcopy(saved_map_data.data)
                        map_data.pixel_type = copy.deepcopy(saved_map_data.pixel_type)
                        map_data.dimensions = copy.deepcopy(saved_map_data.dimensions)
                        map_data.charger_position = copy.deepcopy(saved_map_data.charger_position)
                        map_data.no_go_areas = saved_map_data.no_go_areas
                        map_data.virtual_walls = saved_map_data.virtual_walls
                        map_data.robot_position = None
                        map_data.docked = True
                        # map_data.restored_map = True
                        map_data.path = None
                        map_data.need_optimization = False
                        map_data.saved_map_status = 2
                    elif (
                        map_data.robot_position is None
                        and map_data.restored_map
                        and not self._device_docked
                        and self._map_data
                        and not map_data.docked
                    ):
                        map_data.robot_position = self._map_data.robot_position

                changed = (
                    self._current_frame_id is None
                    or self._map_data is None
                    or map_data != self._map_data
                    or map_data.segments != self._map_data.segments
                )

                if (
                    changed
                    or self._current_frame_id != map_data.frame_id
                    or self._current_timestamp_ms != map_data.timestamp_ms
                ):
                    if (
                        self._current_frame_id is not None
                        and self._map_data is not None
                        and self._updated_frame_id is not None
                    ):
                        if map_data.frame_id <= self._updated_frame_id + 1:
                            if not self._map_data.empty_map and (
                                self._map_data.saved_map_status == 2
                                or (self._vslam_map and self._map_data.saved_map_status == 1)
                            ):
                                map_data.active_segments = self._map_data.active_segments
                                map_data.active_areas = self._map_data.active_areas
                                map_data.active_points = self._map_data.active_points
                                map_data.active_cruise_points = self._map_data.active_cruise_points
                                map_data.path = self._map_data.path
                                map_data.segments = self._map_data.segments
                                map_data.floor_material = self._map_data.floor_material
                                map_data.hidden_segments = self._map_data.hidden_segments
                                map_data.cleanset = self._map_data.cleanset
                                changed = map_data != self._map_data
                            else:
                                changed = False
                                map_data.empty_map = True
                        else:
                            self._updated_frame_id = None

                    if (
                        self._map_data
                        and not changed
                        and map_data.need_optimization
                        and not self._map_data.need_optimization
                    ):
                        map_data.need_optimization = False
                        map_data.optimized_pixel_type = copy.deepcopy(self._map_data.optimized_pixel_type)
                        map_data.optimized_dimensions = copy.deepcopy(self._map_data.optimized_dimensions)
                        map_data.optimized_charger_position = copy.deepcopy(self._map_data.optimized_charger_position)

                    self._map_data = map_data
                    self._current_frame_id = map_data.frame_id
                    self._current_map_id = map_data.map_id
                    self._current_timestamp_ms = map_data.timestamp_ms

                    if changed:
                        _LOGGER.info("Decode I map %d %d", map_data.map_id, map_data.frame_id)
                        self._map_data.last_updated = time.time()
                        self._map_data_changed()
                    else:
                        _LOGGER.info(
                            "Decode map %d %d not changed",
                            map_data.map_id,
                            map_data.frame_id,
                        )

        if self._current_frame_id is None and self._map_data is not None:
            self._map_data = None
            self._map_data_changed()

        self._add_next_map_data()
        return True

    def _add_next_map_data(self) -> None:
        next_partial_map = self._unqueue_next_partial_map()
        if next_partial_map is not None:
            _LOGGER.debug("Continue to next map data")
            self._add_map_data(next_partial_map)

    def _refresh_map_list(self) -> None:
        index = 1
        new_map_list = []
        for map_id, saved_map_data in sorted(self._saved_map_data.items()):
            new_map_list.append(map_id)
            if saved_map_data.custom_name is None:
                saved_map_data.map_name = f"Map {str(index)}"
            else:
                saved_map_data.map_name = saved_map_data.custom_name
            saved_map_data.map_index = index
            index = index + 1
        self._map_list = new_map_list

    def _refresh_recovery_map_list(self) -> None:
        index = 1
        for map_id, saved_map_data in sorted(self._saved_map_data.items()):
            if saved_map_data.recovery_map_list:
                for recovery_map_data in saved_map_data.recovery_map_list:
                    map_type = recovery_map_data.map_type.name.replace("_", " ").title()
                    if saved_map_data.custom_name is None:
                        recovery_map_data.map_name = f"Recovery Map {str(index)} ({map_type})"
                    else:
                        recovery_map_data.map_name = (
                            f"{saved_map_data.custom_name} Recovery Map {str(index)} ({map_type})"
                        )
                    recovery_map_data.map_index = index
                    index = index + 1

    def handle_properties(self, properties):
        if not self._ready:
            return

        has_map = False
        object_name = None
        raw_map_data = None

        for prop in properties:
            value = prop.get(MAP_PARAMETER_VALUE)
            if value is None:
                _LOGGER.debug(
                    "Map property update has no value field: %s",
                    prop,
                )
                continue
            if value != "":
                piid = prop["piid"]
                if piid == PIID(DreameMowerProperty.OBJECT_NAME):
                    has_map = True
                    object_name = value
                elif piid == PIID(DreameMowerProperty.MAP_DATA):
                    has_map = True
                    raw_map_data = value
                elif piid == PIID(DreameMowerProperty.OLD_MAP_DATA):
                    if not has_map:
                        values = value.split(",")
                        if values[0] == "0":
                            raw_map_data = values[1]
                        else:
                            object_name = values[1]
                            if len(values) == 3:
                                object_name = f"{object_name},{values[2]}"

        if has_map:
            self._map_request_time = None

        if object_name or raw_map_data:
            partial_map_data = None
            timestamp = int(time.time() * 1000)

            if raw_map_data:
                partial_map_data = [self._decode_map_partial(raw_map_data, timestamp)]
            self._add_cloud_map_data(partial_map_data, object_name, timestamp)

    def get_map(self, map_index: int = 0) -> MapData | None:
        if map_index:
            if map_index <= len(self._map_list):
                return self._saved_map_data[self._map_list[map_index - 1]]
            return None
        return self._map_data

    def get_obstacle_image(self, map_data, index):
        index = str(index)
        if map_data and map_data.obstacles and index in map_data.obstacles:
            obstacle = map_data.obstacles[index]
            if (
                obstacle.file_name
                and len(obstacle.file_name) > 1
                and obstacle.key
                and len(obstacle.key) > 1
                and obstacle.picture_status.value != 3
            ):
                try:
                    object_name = (
                        f"{obstacle.file_name}-{obstacle.object_id}"
                        if self._protocol.dreame_cloud
                        else obstacle.file_name
                    )
                    _LOGGER.info(
                        "Obstacle image object name: %s",
                        object_name,
                    )
                    response = self._get_file_url(object_name, False)
                    if response:
                        response = self._protocol.cloud.get_file(response)
                        if response:
                            response = base64.b64encode(response).decode("utf-8")

                            cipher = Cipher(
                                algorithms.AES(
                                    bytearray.fromhex(hashlib.md5((obstacle.key).encode("utf-8")).hexdigest())
                                ),
                                modes.ECB(),
                                backend=default_backend(),
                            )
                            decryptor = cipher.decryptor()
                            unpadder = padding.PKCS7(128).unpadder()
                            return (
                                (
                                    unpadder.update(
                                        decryptor.update(base64.b64decode(response[response.find(",") + 1 :]))
                                        + decryptor.finalize()
                                    )
                                    + unpadder.finalize()
                                ),
                                obstacle,
                            )
                except Exception as ex:
                    _LOGGER.warning(
                        "Obstacle (%s) image decryption failed: %s",
                        index,
                        traceback.format_exc(),
                    )
        return (None, None)

    def get_history_map(self, object_name, key=None):
        if object_name and len(object_name):
            try:
                _LOGGER.info(
                    "History map object name: %s",
                    object_name,
                )
                response = self._get_file_url(object_name, self._protocol.cloud.dreame_cloud)
                if response:
                    response = self._protocol.cloud.get_file(response)
                    if response:
                        map_data, saved_map_data = DreameMowerMapDecoder.decode_map(
                            response.decode(), self._vslam_map, None, self._aes_iv, key
                        )
                        if map_data:
                            DreameMowerMapDecoder.set_segment_cleanset(map_data, map_data.cleanset, self._capability)
                            map_data.history_map = True
                            if map_data.need_optimization:
                                map_data = self.optimizer.optimize(map_data, saved_map_data)
                                map_data.need_optimization = False
                            return map_data
            except Exception as ex:
                _LOGGER.warning(
                    "History map decoding failed: %s",
                    traceback.format_exc(),
                )

    def get_recovery_map(self, map_id, index):
        if map_id in self._map_list:
            recovery_map_list = self._saved_map_data[map_id].recovery_map_list
            index = int(index) - 1
            if recovery_map_list and len(recovery_map_list) > index:
                if recovery_map_list[index].map_data is None:
                    recovery_map_list[index].map_data = DreameMowerMapDecoder.decode_saved_map(
                        recovery_map_list[index].raw_map,
                        self._vslam_map,
                        self._saved_map_data[map_id].rotation,
                        self._aes_iv,
                    )
                    recovery_map_list[index].map_data.last_updated = recovery_map_list[index].date.timestamp()
                    recovery_map_list[index].map_data.recovery_map_type = recovery_map_list[index].map_type
                    recovery_map_list[index].map_data.recovery_map = True
                return recovery_map_list[index].map_data

    def get_recovery_map_file(self, map_id, index):
        if map_id in self._map_list:
            recovery_map_list = self._saved_map_data[map_id].recovery_map_list
            index = int(index) - 1
            if recovery_map_list and len(recovery_map_list) > index:
                object_name = recovery_map_list[index].object_name
                if object_name and object_name != "":
                    _LOGGER.info(
                        "Recovery map object name: %s",
                        object_name,
                    )
                    map_url = self._get_file_url(
                        object_name,
                        not (object_name.endswith("mb.tbz2") and not self._protocol.dreame_cloud),
                    )
                    _LOGGER.info("Recovery map file url: %s = %s", object_name, map_url)
                    if map_url:
                        return (
                            self._protocol.cloud.get_file(map_url),
                            map_url,
                            object_name,
                        )
        return None, None, None

    def listen(self, change_callback, update_callback) -> None:
        self._change_callback = change_callback
        self._update_callback = update_callback

    def listen_error(self, callback) -> None:
        self._error_callback = callback

    def disconnect(self) -> None:
        """Disconnect from map and cancel timers"""
        self._disconnected = True
        self.schedule_update(-1)
        self._update_callback = None
        self._change_callback = None
        self._error_callback = None

    def schedule_update(self, wait: float = None) -> None:
        if wait == None:
            wait = self._update_interval
        if self._update_timer is not None:
            self._update_timer.cancel()
            del self._update_timer
            self._update_timer = None
        if wait >= 0 and not self._disconnected:
            self._update_timer = Timer(wait, self._update_task)
            self._update_timer.start()

    def update(self) -> None:
        if self._update_running:
            return

        self._update_running = True

        _LOGGER.debug("Map update: %s", self._update_interval)
        try:
            if (self._map_list_object_name and self._need_map_list_request is None) or (
                self._need_map_list_request and not self._device_running
            ):
                self.request_map_list()

            if self._recovery_map_list_object_name and self._need_recovery_map_list_request:
                self.request_recovery_map_list()

            if self._map_request_time is not None or self._need_map_request:
                self._updated_frame_id = None
                self._map_request_count = self._map_request_count + 1
                if self._map_request_count >= 6:
                    self._map_request_time = None
                    self._need_map_request = False
                elif (
                    not self._request_current_map(self._map_request_time)
                    and self._protocol.dreame_cloud
                    and self._map_request_count == 2
                    and self._map_data is None
                ):
                    object_name_result = self._protocol.cloud.get_properties(DIID(DreameMowerProperty.OBJECT_NAME))
                    if object_name_result and MAP_PARAMETER_VALUE in object_name_result[0]:
                        self._add_cloud_map_data(
                            None, object_name_result[0][MAP_PARAMETER_VALUE], object_name_result[0].get("updateDate")
                        )
            elif not self._protocol.dreame_cloud:
                if self._map_data is None or (
                    self._device_running
                    and (time.time() - (self._current_timestamp_ms / 1000.0) > 15 or self._map_data.empty_map)
                ):
                    self._updated_frame_id = None
                    if self._map_data and not self._map_data.empty_map:
                        _LOGGER.info(
                            "Need map request: %.2f",
                            time.time() - (self._current_timestamp_ms / 1000.0),
                        )
                    if self._protocol.cloud.logged_in:
                        self._request_current_map()
                elif not self._request_map_from_cloud() and self._device_running:
                    _LOGGER.debug("No new map data received, retrying")
                    sleep(1)
                    if not self._request_map_from_cloud():
                        self.schedule_update(1)
                        _LOGGER.debug("No new map data received on second try")
            elif self._protocol.cloud.connected:
                if not self._connected:
                    self._connected = True
                    self._map_data_changed()

                if self._map_data is None or (
                    self._device_running
                    and (
                        (self._map_data.last_updated and time.time() - (self._map_data.last_updated) > 60)
                        or self._map_data.empty_map
                    )
                ):
                    if self._map_data and not self._map_data.empty_map:
                        _LOGGER.info(
                            "Need map request: %.2f",
                            time.time() - (self._map_data.last_updated),
                        )
                        self._request_map()
                    else:
                        self._request_current_map()
            elif self._connected:
                self._connected = False
                self._map_data_changed()

            if not self._available and self._connected:
                self._available = True
                self._map_data_changed()
        except Exception as ex:
            if self._available:
                _LOGGER.warning("Map update Failed: %s", traceback.format_exc())
                self._available = False
                if self._error_callback:
                    self._error_callback(DeviceUpdateFailedException(ex))

        self._ready = True
        self._update_running = False

    def set_aes_iv(self, aes_iv: str) -> None:
        if aes_iv:
            self._aes_iv = aes_iv

    def set_capability(self, capability) -> None:
        if capability:
            self._capability = capability
            if not capability.lidar_navigation:
                self._vslam_map = True

    def set_update_interval(self, update_interval: float) -> None:
        if self._update_interval != update_interval:
            self._update_interval = update_interval
            self.schedule_update()

    def set_device_running(self, running: bool, docked: bool) -> None:
        if self._device_running != running:
            self._device_running = running

        if self._device_docked != docked:
            if docked:
                if not self._vslam_map:
                    self._request_map()
                elif self._map_data and self._map_data.saved_map_status == 1:
                    saved_map_data = self._map_manager.selected_map
                    self._map_data.segments = copy.deepcopy(saved_map_data.segments)
                    self._map_data.data = copy.deepcopy(saved_map_data.data)
                    self._map_data.pixel_type = copy.deepcopy(saved_map_data.pixel_type)
                    self._map_data.dimensions = copy.deepcopy(saved_map_data.dimensions)
                    self._map_data.charger_position = copy.deepcopy(saved_map_data.charger_position)
                    self._map_data.no_go_areas = saved_map_data.no_go_areas
                    self._map_data.virtual_walls = saved_map_data.virtual_walls
                    self._map_data.robot_position = self._map_data.charger_position
                    self._map_data.docked = True
                    # self._map_data.restored_map = True
                    self._map_data.path = None
                    self._map_data.need_optimization = False
                    self._map_data.saved_map_status = 2
                    self._map_data.last_updated = time.time()
                    self._map_data.optimized_pixel_type = None
                    self._map_data.optimized_charger_position = None
                    self._map_data_changed()

            self._device_docked = docked
            self.schedule_update(2)

    def set_device_docked(self, device_docked: bool) -> None:
        if self._device_docked != device_docked:
            self.schedule_update(2)
        self._device_docked = device_docked

    def request_new_map(self) -> None:
        if (
            self._new_map_request_time
            and time.time() - self._new_map_request_time < 10
            and not self._protocol.dreame_cloud
        ):
            if time.time() - self._new_map_request_time > 3:
                self._new_map_request_time = time.time()
                self._request_map_from_cloud()
            return

        self._new_map_request_time = time.time()
        if self._map_data is None:
            return self._request_i_map()
        else:
            result = self._request_map()
            if self._map_action_succeeded(result) and not self._protocol.dreame_cloud:
                self._request_map_from_cloud()

    def request_next_map(self) -> None:
        self._map_request_count = 0
        self._need_map_request = True
        self.schedule_update(2)

    def request_next_map_list(self) -> None:
        self._need_map_list_request = True

    def request_next_recovery_map_list(self) -> None:
        self._need_recovery_map_list_request = True

    def set_map_list_object_name(self, object_name: str, md5: str = None) -> bool:
        if object_name and object_name != "":
            if self._map_list_object_name != object_name or self._map_list_md5 != md5:
                self._map_list_object_name = object_name
                if not self._device_running and self._map_list_md5 is not None:
                    self.request_next_map_list()
                    self.schedule_update(3)
                self._map_list_md5 = md5
                return True
        return False

    def set_recovery_map_list_object_name(self, object_name: str) -> bool:
        if object_name and object_name != "":
            if self._recovery_map_list_object_name != object_name:
                self._recovery_map_list_object_name = object_name
                self._need_recovery_map_list_request = True
                return True
        return False

    def request_map_list(self) -> None:
        if self._map_list_object_name and self._protocol.cloud.logged_in:
            _LOGGER.info("Get Map List: %s", self._map_list_object_name)
            try:
                response = self._get_interim_file_data(self._map_list_object_name)
            except Exception as ex:
                _LOGGER.warn("Get Map List failed: %s", ex)
                return

            if response:
                self._need_map_list_request = False
                raw_map = response.decode()

                try:
                    map_info = json.loads(raw_map)
                except:
                    _LOGGER.warn("Get Map List json parse failed")
                    return

                saved_map_list = map_info[MAP_PARAMETER_MAPSTR]
                changed = False
                now = time.time()
                map_list = {}
                if saved_map_list:
                    for v in saved_map_list:
                        if v.get(MAP_PARAMETER_MAP):
                            saved_map_data = DreameMowerMapDecoder.decode_saved_map(
                                v[MAP_PARAMETER_MAP],
                                self._vslam_map,
                                int(v[MAP_PARAMETER_ANGLE]) if v.get(MAP_PARAMETER_ANGLE) else 0,
                                self._aes_iv,
                            )
                            if saved_map_data is not None:
                                name = v.get(MAP_PARAMETER_NAME)
                                if name:
                                    saved_map_data.custom_name = name
                                    saved_map_data.map_name = name
                                map_list[saved_map_data.map_id] = saved_map_data

                    for map_id, saved_map_data in sorted(map_list.items()):
                        if map_id in self._saved_map_data:
                            if self._selected_map_id == map_id and self._map_data:
                                saved_map_data.cleanset = self._map_data.cleanset
                            else:
                                saved_map_data.cleanset = self._saved_map_data[map_id].cleanset

                            if self._saved_map_data[map_id] != saved_map_data:
                                _LOGGER.info("Saved map changed: %s", map_id)
                                changed = True
                                saved_map_data.last_updated = now
                                if saved_map_data.wifi_map_data:
                                    saved_map_data.wifi_map_data.last_updated = saved_map_data.last_updated
                                saved_map_data.recovery_map_list = self._saved_map_data[map_id].recovery_map_list
                                if self._map_data is None or self._selected_map_id != map_id:
                                    self._saved_map_data[map_id] = saved_map_data
                                else:
                                    self._saved_map_data[map_id].custom_name = saved_map_data.custom_name
                                    self._saved_map_data[map_id].rotation = saved_map_data.rotation
                            else:
                                _LOGGER.info("Saved map not changed: %s", map_id)
                        else:
                            saved_map_data.last_updated = now
                            if saved_map_data.wifi_map_data:
                                saved_map_data.wifi_map_data.last_updated = saved_map_data.last_updated
                            self._saved_map_data[map_id] = saved_map_data
                            _LOGGER.info("Add saved map: %s", map_id)
                            changed = True

                current_map_list = self._saved_map_data.copy()
                for map_id in current_map_list.keys():
                    if map_id not in map_list:
                        del self._saved_map_data[map_id]
                        changed = True

                selected_map_id = map_info[MAP_PARAMETER_CURR_ID]
                if selected_map_id in self._saved_map_data and self._selected_map_id != selected_map_id:
                    self._selected_map_id = selected_map_id
                    changed = True

                if changed == True:
                    self._refresh_map_list()
                    if self._map_data:
                        self._map_data_changed()

    def request_recovery_map_list(self) -> None:
        if self._recovery_map_list_object_name:
            _LOGGER.info("Get Recovery Map List: %s", self._recovery_map_list_object_name)
            response = self._get_file_url(self._recovery_map_list_object_name)
            if response:
                self._need_recovery_map_list_request = False
                response = self._protocol.cloud.get_file(response)
                if response:
                    try:
                        recovery_map_list = json.loads(response.decode())
                    except:
                        _LOGGER.warn("Get Recovery Map List json parse failed")
                        return

                    changed = False
                    for recovery_map in recovery_map_list:
                        map_id = recovery_map["id"]
                        if map_id in self._map_list:
                            recovery_map_list = []
                            map_info_list = recovery_map["info"]
                            for map_info in map_info_list:
                                recovery_map_list.append(RecoveryMapInfo(map_id, map_info))
                            if len(recovery_map_list) > 2:
                                recovery_map_list.sort(
                                    key=cmp_to_key(
                                        lambda a, b: (
                                            int(a.map_type) - int(b.map_type)
                                            if int(a.map_type == 0) and int(b.map_type == 2)
                                            else 0
                                        )
                                    )
                                )
                            if self._saved_map_data[map_id].recovery_map_list != recovery_map_list:
                                self._saved_map_data[map_id].recovery_map_list = recovery_map_list
                                _LOGGER.info("Saved recovery map list changed: %s", map_id)
                                changed = True

                    if changed:
                        self._refresh_recovery_map_list()
                        if self._connected:
                            self._map_data_changed()

    @property
    def _request_i_map_available(self) -> bool:
        return bool(
            not (
                self._map_data is not None
                and (
                    (self._map_data.saved_map_status == 0 and not self._map_data.empty_map)
                    or self._map_data.saved_map_status == 1
                    or self._map_data.restored_map
                    or self._map_data.temporary_map
                )
            )
        )

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def map_list(self) -> list[int] | None:
        return self._saved_map_data.keys()

    @property
    def map_data_list(self) -> dict[int, MapData] | None:
        return self._saved_map_data

    @property
    def selected_map(self) -> MapData | None:
        if self._map_data:
            if self._selected_map_id is not None and self._selected_map_id in self._saved_map_data:
                return self._saved_map_data[self._selected_map_id]

            if self._map_list and len(self._map_list) == 1 and self._map_list[0] in self._saved_map_data:
                return self._saved_map_data[self._map_list[0]]

    @property
    def cleaning_sequence(self) -> list | None:
        return (
            [
                (k)
                for k, v in sorted(
                    self._map_data.segments.items(),
                    key=lambda s: s[1].order if s[1].order != None else 0,
                )
                if v.order
            ]
            if self._map_data and self._map_data.segments
            else []
        )
