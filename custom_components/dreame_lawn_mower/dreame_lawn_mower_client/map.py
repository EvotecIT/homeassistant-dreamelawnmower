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
from .resources import *
from .protocol import DreameMowerProtocol
from .exceptions import DeviceUpdateFailedException
from .map_decoder import DreameMowerMapDecoder
from .map_json_renderer import DreameMowerMapDataJsonRenderer
from .map_optimizer import DreameMowerMapOptimizer
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

from .map_editor import DreameMapMowerMapEditor
from .map_manager import DreameMapMowerMapManager
from .map_renderer import DreameMowerMapRenderer
