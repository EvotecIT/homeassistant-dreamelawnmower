"""Device protocol enums, mappings, capabilities, and settings data."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Any, Final

SEGMENT_TYPE_CODE_TO_NAME: Final = {
    0: "Zone",
    1: "Living Zone",
    2: "Primary Bedzone",
    3: "Study",
    4: "Kitchen",
    5: "Dining Hall",
    6: "Bathzone",
    7: "Balcony",
    8: "Corridor",
    9: "Utility Zone",
    10: "Closet",
    11: "Meeting Zone",
    12: "Office",
    13: "Fitness Area",
    14: "Recreation Area",
    15: "Secondary Bedzone",
}

SEGMENT_TYPE_CODE_TO_HA_ICON: Final = {
    0: "mdi:home-outline",
    1: "mdi:sofa-outline",
    2: "mdi:bed-king-outline",
    3: "mdi:bookshelf",
    4: "mdi:chef-hat",
    5: "mdi:zone-service-outline",
    6: "mdi:toilet",
    7: "mdi:flower-outline",
    8: "mdi:foot-print",
    9: "mdi:archive-outline",
    10: "mdi:hanger",
    11: "mdi:presentation",
    12: "mdi:monitor-shimmer",
    13: "mdi:dumbbell",
    14: "mdi:gamepad-variant-outline",
    15: "mdi:bed-single-outline",
}

FURNITURE_TYPE_TO_DIMENSIONS: Final = {
    1: [1500, 2000],
    2: [1800, 2000],
    3: [800, 700],
    4: [1260, 800],
    5: [2340, 750],
    6: [1500, 800],
    7: [500, 400],
    8: [800, 400],
    9: [450, 690],
    10: [735, 990],
    11: [566, 865],
    12: [210, 378],
    13: [628, 936],
}

FURNITURE_V2_TYPE_TO_DIMENSIONS: Final = {
    1: [1000, 2000],
    2: [1500, 2000],
    3: [800, 700],
    4: [1400, 600],
    5: [2300, 700],
    6: [1200, 800],
    7: [500, 400],
    8: [800, 800],
    9: [400, 600],
    10: [300, 500],
    11: [500, 400],
    12: [400, 200],
    13: [400, 600],
    14: [600, 600],
    15: [600, 600],
    16: [300, 500],
    17: [400, 400],
    18: [1600, 300],
    19: [800, 300],
    20: [800, 400],
    21: [2000, 600],
    22: [300, 300],
    23: [1000, 400],
    24: [2800, 1700],
    25: [1000, 1000],
}

piid: Final = "piid"
siid: Final = "siid"
aiid: Final = "aiid"

ATTR_A: Final = "a"
ATTR_X: Final = "x"
ATTR_X0: Final = "x0"
ATTR_X1: Final = "x1"
ATTR_X2: Final = "x2"
ATTR_X3: Final = "x3"
ATTR_Y: Final = "y"
ATTR_Y0: Final = "y0"
ATTR_Y1: Final = "y1"
ATTR_Y2: Final = "y2"
ATTR_Y3: Final = "y3"
ATTR_CHARGER: Final = "charger_position"
ATTR_IS_EMPTY: Final = "is_empty"
ATTR_NO_GO_AREAS: Final = "no_go_areas"
ATTR_PREDEFINED_POINTS: Final = "predefined_points"
ATTR_VIRTUAL_WALLS: Final = "virtual_walls"
ATTR_PATHWAYS: Final = "pathways"
ATTR_ZONES: Final = "zones"
ATTR_ROBOT_POSITION: Final = "mower_position"
ATTR_MAP_ID: Final = "map_id"
ATTR_MAP_NAME: Final = "map_name"
ATTR_ROTATION: Final = "rotation"
ATTR_TIMESTAMP: Final = "timestamp"
ATTR_UPDATED: Final = "updated_at"
ATTR_ACTIVE_AREAS: Final = "active_areas"
ATTR_ACTIVE_POINTS: Final = "active_points"
ATTR_ACTIVE_CRUISE_POINTS: Final = "active_cruise_points"
ATTR_ACTIVE_SEGMENTS: Final = "active_segments"
ATTR_FRAME_ID: Final = "frame_id"
ATTR_MAP_INDEX: Final = "map_index"
ATTR_ZONE_ID: Final = "zone_id"
ATTR_ZONE_ICON: Final = "zone_icon"
ATTR_UNIQUE_ID: Final = "unique_id"
ATTR_FLOOR_MATERIAL: Final = "floor_material"
ATTR_FLOOR_MATERIAL_DIRECTION: Final = "floor_material_direction"
ATTR_VISIBILITY: Final = "visibility"
ATTR_NAME: Final = "name"
ATTR_OUTLINE: Final = "outline"
ATTR_CENTER: Final = "center"
ATTR_ORDER: Final = "order"
ATTR_CLEANING_TIMES: Final = "cleaning_times"
ATTR_CLEANING_MODE: Final = "cleaning_mode"
ATTR_CLEANING_ROUTE: Final = "cleaning_route"
ATTR_TYPE: Final = "type"
ATTR_INDEX: Final = "index"
ATTR_ICON: Final = "icon"
ATTR_COLOR_INDEX: Final = "color_index"
ATTR_OBSTACLES: Final = "obstacles"
ATTR_POSSIBILTY: Final = "possibility"
ATTR_PICTURE_STATUS: Final = "picture_status"
ATTR_IGNORE_STATUS: Final = "ignore_status"
ATTR_ZONE: Final = "zone"
ATTR_ROUTER_POSITION: Final = "router_position"
ATTR_FURNITURES: Final = "furnitures"
ATTR_STARTUP_METHOD: Final = "startup_method"
ATTR_RECOVERY_MAP_LIST: Final = "recovery_map_list"
ATTR_WIDTH: Final = "width"
ATTR_HEIGHT: Final = "height"
ATTR_SIZE_TYPE: Final = "size_type"
ATTR_ANGLE: Final = "angle"
ATTR_SCALE: Final = "scale"
ATTR_COMPLETED: Final = "completed"
ATTR_FIRMWARE_VERSION: Final = "firmware_version"
ATTR_AP: Final = "ap"
ATTR_COLOR_SCHEME: Final = "color_scheme"


class DreameMowerChargingStatus(IntEnum):
    """Dreame Mower charging status"""

    UNKNOWN = -1
    CHARGING = 1
    NOT_CHARGING = 2
    CHARGING_COMPLETED = 3
    RETURN_TO_CHARGE = 5


class DreameMowerErrorCode(IntEnum):
    """Dreame Mower error code"""

    UNKNOWN = -1
    NO_ERROR = 0
    DROP = 1
    CLIFF = 2
    BUMPER = 3
    GESTURE = 4
    BUMPER_REPEAT = 5
    DROP_REPEAT = 6
    OPTICAL_FLOW = 7
    BRUSH = 12
    SIDE_BRUSH = 13
    FAN = 14
    LEFT_WHEEL_MOTOR = 15
    RIGHT_WHEEL_MOTOR = 16
    TURN_SUFFOCATE = 17
    FORWARD_SUFFOCATE = 18
    CHARGER_GET = 19
    BATTERY_LOW = 20
    CHARGE_FAULT = 21
    BATTERY_PERCENTAGE = 22
    HEART = 23
    CAMERA_OCCLUSION = 24
    MOVE = 25
    FLOW_SHIELDING = 26
    INFRARED_SHIELDING = 27
    CHARGE_NO_ELECTRIC = 28
    BATTERY_FAULT = 29
    FAN_SPEED_ERROR = 30
    LEFTWHELL_SPEED = 31
    RIGHTWHELL_SPEED = 32
    BMI055_ACCE = 33
    BMI055_GYRO = 34
    XV7001 = 35
    LEFT_MAGNET = 36
    RIGHT_MAGNET = 37
    FLOW_ERROR = 38
    INFRARED_FAULT = 39
    CAMERA_FAULT = 40
    STRONG_MAGNET = 41
    RTC = 43
    AUTO_KEY_TRIG = 44
    P3V3 = 45
    CAMERA_IDLE = 46
    BLOCKED = 47
    LDS_ERROR = 48
    LDS_BUMPER = 49
    FILTER_BLOCKED = 51
    EDGE = 54
    LASER = 56
    EDGE_2 = 57
    ULTRASONIC = 58
    NO_GO_ZONE = 59
    ROUTE = 61
    ROUTE_2 = 62
    BLOCKED_2 = 63
    BLOCKED_3 = 64
    RESTRICTED = 65
    RESTRICTED_2 = 66
    RESTRICTED_3 = 67
    LOW_BATTERY_TURN_OFF = 75
    ROBOT_IN_HIDDEN_ZONE = 78
    STATION_DISCONNECTED = 117
    UNKNOWN_WARNING_2 = 122
    SELF_TEST_FAILED = 123
    RETURN_TO_CHARGE_FAILED = 1000


class DreameMowerState(IntEnum):
    """Dreame Mower state"""

    UNKNOWN = -1
    MOWING = 1
    IDLE = 2
    PAUSED = 3
    ERROR = 4
    RETURNING = 5
    CHARGING = 6
    BUILDING = 11
    CHARGING_COMPLETED = 13
    UPGRADING = 14
    CLEAN_SUMMON = 15
    STATION_RESET = 16
    REMOTE_CONTROL = 23
    SMART_CHARGING = 24
    SECOND_CLEANING = 25
    HUMAN_FOLLOWING = 26
    SPOT_CLEANING = 27
    WAITING_FOR_TASK = 29
    STATION_CLEANING = 30
    SHORTCUT = 97
    MONITORING = 98
    MONITORING_PAUSED = 99


class DreameMowerStateOld(IntEnum):
    """Dreame Mower old state"""

    UNKNOWN = -1
    MOWING = 1
    IDLE = 2
    PAUSED = 3
    ERROR = 4
    RETURNING = 5
    CHARGING = 6
    BUILDING = 11
    CHARGING_COMPLETED = 13
    UPGRADING = 14
    CLEAN_SUMMON = 15
    STATION_RESET = 16
    REMOTE_CONTROL = 19
    MONITORING = 21
    MONITORING_PAUSED = 21
    SMART_CHARGING = 26


class DreameMowerCleaningMode(IntEnum):
    """Dreame Mower cleaning mode"""

    UNKNOWN = -1
    MOWING = 0


class DreameMowerRelocationStatus(IntEnum):
    """Dreame Mower relocation status"""

    UNKNOWN = -1
    LOCATED = 0
    LOCATING = 1
    FAILED = 10
    SUCCESS = 11


class DreameMowerTaskStatus(IntEnum):
    """Dreame Mower task status"""

    UNKNOWN = -1
    COMPLETED = 0
    AUTO_CLEANING = 1
    ZONE_CLEANING = 2
    SEGMENT_CLEANING = 3
    SPOT_CLEANING = 4
    FAST_MAPPING = 5
    AUTO_CLEANING_PAUSED = 6
    ZONE_CLEANING_PAUSED = 7
    SEGMENT_CLEANING_PAUSED = 8
    SPOT_CLEANING_PAUSED = 9
    MAP_CLEANING_PAUSED = 10
    DOCKING_PAUSED = 11
    AUTO_DOCKING_PAUSED = 16
    SEGMENT_DOCKING_PAUSED = 17
    ZONE_DOCKING_PAUSED = 18
    CRUISING_PATH = 20
    CRUISING_PATH_PAUSED = 21
    CRUISING_POINT = 22
    CRUISING_POINT_PAUSED = 23
    SUMMON_CLEAN_PAUSED = 24


class DreameMowerStatus(IntEnum):
    """Dreame Mower status"""

    UNKNOWN = -1
    IDLE = 0
    PAUSED = 1
    CLEANING = 2
    BACK_HOME = 3
    PART_CLEANING = 4
    FOLLOW_WALL = 5
    CHARGING = 6
    OTA = 7
    FCT = 8
    WIFI_SET = 9
    POWER_OFF = 10
    FACTORY = 11
    ERROR = 12
    REMOTE_CONTROL = 13
    SLEEPING = 14
    SELF_REPAIR = 15
    FACTORY_FUNCION_TEST = 16
    STANDBY = 17
    SEGMENT_CLEANING = 18
    ZONE_CLEANING = 19
    SPOT_CLEANING = 20
    FAST_MAPPING = 21
    CRUISING_PATH = 22
    CRUISING_POINT = 23
    SUMMON_CLEAN = 24
    SHORTCUT = 25
    PERSON_FOLLOW = 26


class DreameMowerDustCollection(IntEnum):
    """Dreame Mower dust collection availability"""

    UNKNOWN = -1
    NOT_AVAILABLE = 0
    AVAILABLE = 1


class DreameMowerAutoEmptyStatus(IntEnum):
    """Dreame Mower dust collection status"""

    UNKNOWN = -1
    IDLE = 0
    ACTIVE = 1
    NOT_PERFORMED = 2


class DreameMowerSelfCleanArea(IntEnum):
    """Dreame Mower self clean area"""

    UNKNOWN = -1
    SINGLE_ZONE = 0
    FIVE_SQUARE_METERS = 5
    TEN_SQUARE_METERS = 10
    FIFTEEN_SQUARE_METERS = 15


class DreameMowerCleaningRoute(IntEnum):
    """Dreame Mower Cleaning route"""

    UNKNOWN = -1
    NOT_SET = 0
    STANDARD = 1
    INTENSIVE = 2
    DEEP = 3
    QUICK = 4


class DreameMowerWiderCornerCoverage(IntEnum):
    """Dreame Mower wider corner coverage"""

    UNKNOWN = -1
    OFF = 0
    HIGH_FREQUENCY = 1
    LOW_FREQUENCY = 7


class DreameMowerCleanGenius(IntEnum):
    """Dreame Mower CleanGenius mode"""

    UNKNOWN = -1
    OFF = 0
    ROUTINE_CLEANING = 1
    DEEP_CLEANING = 2


class DreameMowerSecondCleaning(IntEnum):
    """Dreame Mower Second Cleaning mode"""

    UNKNOWN = -1
    OFF = 0
    IN_DEEP_MODE = 1
    IN_ALL_MODES = 2


class DreameMowerFloorMaterial(IntEnum):
    """Dreame Mower floor material"""

    UNKNOWN = -1
    NONE = 0
    WOOD = 1
    TILE = 2


class DreameMowerFloorMaterialDirection(IntEnum):
    """Dreame Mower floor direction"""

    UNKNOWN = -1
    HORIZONTAL = 0
    VERTICAL = 90


class DreameMowerSegmentVisibility(IntEnum):
    """Dreame Mower segment visibility"""

    HIDDEN = 0
    VISIBLE = 1


class DreameMowerVoiceAssistantLanguage(str, Enum):  # noqa: UP042
    """Dreame Mower assistant language"""

    DEFAULT = ""
    ENGLISH = "EN"
    GERMAN = "DE"
    CHINESE = "ZH"


class DreameMowerStreamStatus(IntEnum):
    """Dreame Mower stream status"""

    UNKNOWN = -1
    IDLE = 0
    VIDEO = 1
    AUDIO = 2
    RECORDING = 3


class DreameMowerTaskType(IntEnum):
    """Dreame Mower task type status"""

    UNKNOWN = -1
    IDLE = 0
    STANDARD = 1
    STANDARD_PAUSED = 2
    CUSTOM = 3
    CUSTOM_PAUSED = 4
    SHORTCUT = 5
    SHORTCUT_PAUSED = 6
    SCHEDULED = 7
    SCHEDULED_PAUSED = 8
    SMART = 9
    SMART_PAUSED = 10
    PARTIAL = 11
    PARTIAL_PAUSED = 12
    SUMMON = 13
    SUMMON_PAUSED = 14


class DreameMapRecoveryStatus(IntEnum):
    """Dreame Mower map recovery status"""

    UNKNOWN = -1
    IDLE = 0
    RUNNING = 2
    SUCCESS = 3
    FAIL = 4
    FAIL_2 = 5


class DreameMapBackupStatus(IntEnum):
    """Dreame Mower map backup status"""

    UNKNOWN = -1
    IDLE = 0
    RUNNING = 2
    SUCCESS = 3
    FAIL = 4


class DreameMowerProperty(IntEnum):
    """Dreame Mower properties"""

    STATE = 0
    ERROR = 1
    BATTERY_LEVEL = 2
    CHARGING_STATUS = 3
    OFF_PEAK_CHARGING = 4
    STATUS = 5
    CLEANING_TIME = 6
    CLEANED_AREA = 7
    TASK_STATUS = 11
    CLEANING_START_TIME = 12
    CLEAN_LOG_FILE_NAME = 13
    CLEANING_PROPERTIES = 14
    RESUME_CLEANING = 15
    CLEAN_LOG_STATUS = 17
    SERIAL_NUMBER = 18
    REMOTE_CONTROL = 19
    CLEANING_PAUSED = 21
    FAULTS = 22
    NATION_MATCHED = 23
    RELOCATION_STATUS = 24
    OBSTACLE_AVOIDANCE = 25
    AI_DETECTION = 26
    CLEANING_MODE = 27
    UPLOAD_MAP = 28
    CUSTOMIZED_CLEANING = 30
    CHILD_LOCK = 31
    CLEANING_CANCEL = 34
    Y_CLEAN = 35
    WARN_STATUS = 39
    CAPABILITY = 42
    MAP_INDEX = 46
    MAP_NAME = 47
    CRUISE_TYPE = 48
    SCHEDULED_CLEAN = 51
    SHORTCUTS = 52
    INTELLIGENT_RECOGNITION = 53
    AUTO_SWITCH_SETTINGS = 54
    NUMERIC_MESSAGE_PROMPT = 60
    MESSAGE_PROMPT = 61
    TASK_TYPE = 62
    PET_DETECTIVE = 63
    BACK_CLEAN_MODE = 66
    CLEANING_PROGRESS = 67
    DEVICE_CAPABILITY = 69
    DND = 70
    DND_START = 71
    DND_END = 72
    DND_TASK = 73
    MAP_DATA = 74
    FRAME_INFO = 75
    OBJECT_NAME = 76
    MAP_EXTEND_DATA = 77
    ROBOT_TIME = 78
    RESULT_CODE = 79
    MULTI_FLOOR_MAP = 80
    MAP_LIST = 81
    RECOVERY_MAP_LIST = 82
    MAP_RECOVERY = 83
    MAP_RECOVERY_STATUS = 84
    OLD_MAP_DATA = 85
    MAP_BACKUP_STATUS = 86
    WIFI_MAP = 87
    VOLUME = 88
    VOICE_PACKET_ID = 89
    VOICE_CHANGE_STATUS = 90
    VOICE_CHANGE = 91
    VOICE_ASSISTANT = 92
    VOICE_ASSISTANT_LANGUAGE = 93
    EMPTY_STAMP = 94
    CURRENT_CITY = 95
    VOICE_TEST = 96
    LISTEN_LANGUAGE = 97
    TIMEZONE = 98
    SCHEDULE = 99
    SCHEDULE_ID = 100
    SCHEDULE_CANCEL_REASON = 101
    CRUISE_SCHEDULE = 102
    BLADES_TIME_LEFT = 103
    BLADES_LEFT = 104
    SIDE_BRUSH_TIME_LEFT = 105
    SIDE_BRUSH_LEFT = 106
    FILTER_LEFT = 107
    FILTER_TIME_LEFT = 108
    FIRST_CLEANING_DATE = 109
    TOTAL_CLEANING_TIME = 110
    CLEANING_COUNT = 111
    TOTAL_CLEANED_AREA = 112
    TOTAL_RUNTIME = 113
    TOTAL_CRUISE_TIME = 114
    MAP_SAVING = 115
    SENSOR_DIRTY_LEFT = 120
    SENSOR_DIRTY_TIME_LEFT = 121
    TANK_FILTER_LEFT = 124
    TANK_FILTER_TIME_LEFT = 125
    SILVER_ION_TIME_LEFT = 126
    SILVER_ION_LEFT = 127
    LENSBRUSH_LEFT = 128
    LENSBRUSH_TIME_LEFT = 129
    SQUEEGEE_LEFT = 130
    SQUEEGEE_TIME_LEFT = 131
    LENSBRUSH_STATUS = 139
    AI_MAP_OPTIMIZATION_STATUS = 141
    SECOND_CLEANING_STATUS = 142
    ADD_CLEANING_AREA_STATUS = 144
    ADD_CLEANING_AREA_RESULT = 145
    CLEAN_EFFICIENCY = 150
    FACTORY_TEST_STATUS = 151
    FACTORY_TEST_RESULT = 152
    SELF_TEST_STATUS = 153
    LSD_TEST_STATUS = 154
    DEBUG_SWITCH = 155
    SERIAL = 156
    CALIBRATION_STATUS = 157
    VERSION = 158
    PERFORMANCE_SWITCH = 159
    AI_TEST_STATUS = 160
    PUBLIC_KEY = 161
    AUTO_PAIR = 162
    MCU_VERSION = 163
    PLATFORM_NETWORK = 165
    STREAM_STATUS = 166
    STREAM_AUDIO = 167
    STREAM_RECORD = 168
    TAKE_PHOTO = 169
    STREAM_KEEP_ALIVE = 170
    STREAM_FAULT = 171
    CAMERA_LIGHT_BRIGHTNESS = 172
    CAMERA_LIGHT = 173
    STEAM_HUMAN_FOLLOW = 174
    STREAM_CRUISE_POINT = 175
    STREAM_PROPERTY = 176
    STREAM_TASK = 177
    STREAM_UPLOAD = 178
    STREAM_CODE = 179
    STREAM_SET_CODE = 180
    STREAM_VERIFY_CODE = 181
    STREAM_RESET_CODE = 182
    STREAM_SPACE = 183


class DreameMowerAutoSwitchProperty(str, Enum):  # noqa: UP042
    """Dreame Mower Auto Switch properties"""

    COLLISION_AVOIDANCE = "LessColl"
    FILL_LIGHT = "FillinLight"
    STAIN_AVOIDANCE = "StainIdentify"
    CLEANGENIUS = "SmartHost"
    WIDER_CORNER_COVERAGE = "MeticulousTwist"
    FLOOR_DIRECTION_CLEANING = "MaterialDirectionClean"
    PET_FOCUSED_CLEANING = "PetPartClean"
    AUTO_CHARGING = "SmartCharge"
    HUMAN_FOLLOW = "MonitorHumanFollow"
    CLEANING_ROUTE = "CleanRoute"
    GAP_CLEANING_EXTENSION = "LacuneMopScalable"
    STREAMING_VOICE_PROMPT = "MonitorPromptLevel"


class DreameMowerStrAIProperty(str, Enum):  # noqa: UP042
    """Dreame Mower json AI obstacle detection properties"""

    AI_OBSTACLE_DETECTION = "obstacle_detect_switch"
    AI_OBSTACLE_IMAGE_UPLOAD = "obstacle_app_display_switch"
    AI_PET_DETECTION = "whether_have_pet"
    AI_HUMAN_DETECTION = "human_detect_switch"
    AI_FURNITURE_DETECTION = "furniture_detect_switch"
    AI_FLUID_DETECTION = "fluid_detect_switch"


class DreameMowerAIProperty(IntEnum):
    """Dreame Mower bitwise AI obstacle detection properties"""

    AI_FURNITURE_DETECTION = 1
    AI_OBSTACLE_DETECTION = 2
    AI_OBSTACLE_PICTURE = 4
    AI_FLUID_DETECTION = 8
    AI_PET_DETECTION = 16
    AI_OBSTACLE_IMAGE_UPLOAD = 32
    AI_IMAGE = 64
    AI_PET_AVOIDANCE = 128
    FUZZY_OBSTACLE_DETECTION = 256
    PET_PICTURE = 512
    PET_FOCUSED_DETECTION = 1024
    LARGE_PARTICLES_BOOST = 2048


class DreameMowerAction(IntEnum):
    """Dreame Mower actions"""

    START_MOWING = 1
    PAUSE = 2
    DOCK = 3
    START_CUSTOM = 4
    STOP = 5
    CLEAR_WARNING = 6
    GET_PHOTO_INFO = 8
    SHORTCUTS = 9
    REQUEST_MAP = 10
    UPDATE_MAP_DATA = 11
    BACKUP_MAP = 12
    WIFI_MAP = 13
    LOCATE = 14
    TEST_SOUND = 15
    DELETE_SCHEDULE = 16
    DELETE_CRUISE_SCHEDULE = 17
    RESET_BLADES = 18
    RESET_SIDE_BRUSH = 19
    RESET_FILTER = 20
    RESET_SENSOR = 21
    RESET_TANK_FILTER = 23
    RESET_SILVER_ION = 25
    RESET_LENSBRUSH = 26
    RESET_SQUEEGEE = 27
    STREAM_VIDEO = 30
    STREAM_AUDIO = 31
    STREAM_PROPERTY = 32
    STREAM_CODE = 33
    PULL_STATUS = 34


# Dreame Mower property mapping
DreameMowerPropertyMapping = {
    DreameMowerProperty.STATE: {siid: 2, piid: 1},
    DreameMowerProperty.ERROR: {siid: 2, piid: 2},
    DreameMowerProperty.BATTERY_LEVEL: {siid: 3, piid: 1},
    DreameMowerProperty.CHARGING_STATUS: {siid: 3, piid: 2},
    DreameMowerProperty.OFF_PEAK_CHARGING: {siid: 3, piid: 3},
    DreameMowerProperty.STATUS: {siid: 4, piid: 1},
    DreameMowerProperty.CLEANING_TIME: {siid: 4, piid: 2},
    DreameMowerProperty.CLEANED_AREA: {siid: 4, piid: 3},
    DreameMowerProperty.TASK_STATUS: {siid: 4, piid: 7},
    DreameMowerProperty.CLEANING_START_TIME: {siid: 4, piid: 8},
    DreameMowerProperty.CLEAN_LOG_FILE_NAME: {siid: 4, piid: 9},
    DreameMowerProperty.CLEANING_PROPERTIES: {siid: 4, piid: 10},
    DreameMowerProperty.RESUME_CLEANING: {siid: 4, piid: 11},
    DreameMowerProperty.CLEAN_LOG_STATUS: {siid: 4, piid: 13},
    DreameMowerProperty.SERIAL_NUMBER: {siid: 4, piid: 14},
    DreameMowerProperty.REMOTE_CONTROL: {siid: 4, piid: 15},
    DreameMowerProperty.CLEANING_PAUSED: {siid: 4, piid: 17},
    DreameMowerProperty.FAULTS: {siid: 4, piid: 18},
    DreameMowerProperty.NATION_MATCHED: {siid: 4, piid: 19},
    DreameMowerProperty.RELOCATION_STATUS: {siid: 4, piid: 20},
    DreameMowerProperty.OBSTACLE_AVOIDANCE: {siid: 4, piid: 21},
    DreameMowerProperty.AI_DETECTION: {siid: 4, piid: 22},
    DreameMowerProperty.CLEANING_MODE: {siid: 4, piid: 23},
    DreameMowerProperty.UPLOAD_MAP: {siid: 4, piid: 24},
    DreameMowerProperty.CUSTOMIZED_CLEANING: {siid: 4, piid: 26},
    DreameMowerProperty.CHILD_LOCK: {siid: 4, piid: 27},
    DreameMowerProperty.CLEANING_CANCEL: {siid: 4, piid: 30},
    DreameMowerProperty.Y_CLEAN: {siid: 4, piid: 31},
    DreameMowerProperty.WARN_STATUS: {siid: 4, piid: 35},
    DreameMowerProperty.CAPABILITY: {siid: 4, piid: 38},
    DreameMowerProperty.MAP_INDEX: {siid: 4, piid: 42},
    DreameMowerProperty.MAP_NAME: {siid: 4, piid: 43},
    DreameMowerProperty.CRUISE_TYPE: {siid: 4, piid: 44},
    DreameMowerProperty.SCHEDULED_CLEAN: {siid: 4, piid: 47},
    DreameMowerProperty.SHORTCUTS: {siid: 4, piid: 48},
    DreameMowerProperty.INTELLIGENT_RECOGNITION: {siid: 4, piid: 49},
    DreameMowerProperty.NUMERIC_MESSAGE_PROMPT: {siid: 4, piid: 56},
    DreameMowerProperty.MESSAGE_PROMPT: {siid: 4, piid: 57},
    DreameMowerProperty.TASK_TYPE: {siid: 4, piid: 58},
    DreameMowerProperty.PET_DETECTIVE: {siid: 4, piid: 59},
    DreameMowerProperty.BACK_CLEAN_MODE: {siid: 4, piid: 62},
    DreameMowerProperty.CLEANING_PROGRESS: {siid: 4, piid: 63},
    DreameMowerProperty.DEVICE_CAPABILITY: {siid: 4, piid: 83},
    # DreameMowerProperty.COMBINED_DATA: {siid: 4, piid: 99},
    DreameMowerProperty.DND: {siid: 5, piid: 1},
    DreameMowerProperty.DND_START: {siid: 5, piid: 2},
    DreameMowerProperty.DND_END: {siid: 5, piid: 3},
    DreameMowerProperty.DND_TASK: {siid: 5, piid: 4},
    DreameMowerProperty.MAP_DATA: {siid: 6, piid: 1},
    DreameMowerProperty.FRAME_INFO: {siid: 6, piid: 2},
    DreameMowerProperty.OBJECT_NAME: {siid: 6, piid: 3},
    DreameMowerProperty.MAP_EXTEND_DATA: {siid: 6, piid: 4},
    DreameMowerProperty.ROBOT_TIME: {siid: 6, piid: 5},
    DreameMowerProperty.RESULT_CODE: {siid: 6, piid: 6},
    DreameMowerProperty.MULTI_FLOOR_MAP: {siid: 6, piid: 7},
    DreameMowerProperty.MAP_LIST: {siid: 6, piid: 8},
    DreameMowerProperty.RECOVERY_MAP_LIST: {siid: 6, piid: 9},
    DreameMowerProperty.MAP_RECOVERY: {siid: 6, piid: 10},
    DreameMowerProperty.MAP_RECOVERY_STATUS: {siid: 6, piid: 11},
    DreameMowerProperty.OLD_MAP_DATA: {siid: 6, piid: 13},
    DreameMowerProperty.MAP_BACKUP_STATUS: {siid: 6, piid: 14},
    DreameMowerProperty.WIFI_MAP: {siid: 6, piid: 15},
    DreameMowerProperty.VOLUME: {siid: 7, piid: 1},
    DreameMowerProperty.VOICE_PACKET_ID: {siid: 7, piid: 2},
    DreameMowerProperty.VOICE_CHANGE_STATUS: {siid: 7, piid: 3},
    DreameMowerProperty.VOICE_CHANGE: {siid: 7, piid: 4},
    DreameMowerProperty.VOICE_ASSISTANT: {siid: 7, piid: 5},
    DreameMowerProperty.EMPTY_STAMP: {siid: 7, piid: 6},
    DreameMowerProperty.CURRENT_CITY: {siid: 7, piid: 7},
    DreameMowerProperty.VOICE_TEST: {siid: 7, piid: 9},
    DreameMowerProperty.VOICE_ASSISTANT_LANGUAGE: {siid: 7, piid: 10},
    DreameMowerProperty.LISTEN_LANGUAGE: {siid: 7, piid: 10},
    DreameMowerProperty.TIMEZONE: {siid: 8, piid: 1},
    DreameMowerProperty.SCHEDULE: {siid: 8, piid: 2},
    DreameMowerProperty.SCHEDULE_ID: {siid: 8, piid: 3},
    DreameMowerProperty.SCHEDULE_CANCEL_REASON: {siid: 8, piid: 4},
    DreameMowerProperty.CRUISE_SCHEDULE: {siid: 8, piid: 5},
    DreameMowerProperty.BLADES_TIME_LEFT: {siid: 9, piid: 1},
    DreameMowerProperty.BLADES_LEFT: {siid: 9, piid: 2},
    DreameMowerProperty.SIDE_BRUSH_TIME_LEFT: {siid: 10, piid: 1},
    DreameMowerProperty.SIDE_BRUSH_LEFT: {siid: 10, piid: 2},
    DreameMowerProperty.FILTER_LEFT: {siid: 11, piid: 1},
    DreameMowerProperty.FILTER_TIME_LEFT: {siid: 11, piid: 2},
    DreameMowerProperty.FIRST_CLEANING_DATE: {siid: 12, piid: 1},
    DreameMowerProperty.TOTAL_CLEANING_TIME: {siid: 12, piid: 2},
    DreameMowerProperty.CLEANING_COUNT: {siid: 12, piid: 3},
    DreameMowerProperty.TOTAL_CLEANED_AREA: {siid: 12, piid: 4},
    DreameMowerProperty.TOTAL_RUNTIME: {siid: 12, piid: 5},
    DreameMowerProperty.TOTAL_CRUISE_TIME: {siid: 12, piid: 6},
    DreameMowerProperty.MAP_SAVING: {siid: 13, piid: 1},
    DreameMowerProperty.SENSOR_DIRTY_LEFT: {siid: 16, piid: 1},
    DreameMowerProperty.SENSOR_DIRTY_TIME_LEFT: {siid: 16, piid: 2},
    DreameMowerProperty.TANK_FILTER_LEFT: {siid: 17, piid: 1},
    DreameMowerProperty.TANK_FILTER_TIME_LEFT: {siid: 17, piid: 2},
    DreameMowerProperty.SILVER_ION_TIME_LEFT: {siid: 19, piid: 1},
    DreameMowerProperty.SILVER_ION_LEFT: {siid: 19, piid: 2},
    DreameMowerProperty.LENSBRUSH_LEFT: {siid: 4, piid: 50},
    DreameMowerProperty.SQUEEGEE_LEFT: {siid: 24, piid: 1},
    DreameMowerProperty.SQUEEGEE_TIME_LEFT: {siid: 24, piid: 2},
    DreameMowerProperty.LENSBRUSH_STATUS: {siid: 27, piid: 4},
    DreameMowerProperty.AI_MAP_OPTIMIZATION_STATUS: {siid: 27, piid: 7},
    DreameMowerProperty.SECOND_CLEANING_STATUS: {siid: 27, piid: 8},
    DreameMowerProperty.ADD_CLEANING_AREA_STATUS: {siid: 27, piid: 10},
    DreameMowerProperty.ADD_CLEANING_AREA_RESULT: {siid: 27, piid: 11},
    DreameMowerProperty.CLEAN_EFFICIENCY: {siid: 28, piid: 9},
    DreameMowerProperty.FACTORY_TEST_STATUS: {siid: 99, piid: 1},
    DreameMowerProperty.FACTORY_TEST_RESULT: {siid: 99, piid: 3},
    DreameMowerProperty.SELF_TEST_STATUS: {siid: 99, piid: 8},
    DreameMowerProperty.LSD_TEST_STATUS: {siid: 99, piid: 9},
    DreameMowerProperty.DEBUG_SWITCH: {siid: 99, piid: 11},
    DreameMowerProperty.SERIAL: {siid: 99, piid: 14},
    DreameMowerProperty.CALIBRATION_STATUS: {siid: 99, piid: 15},
    DreameMowerProperty.VERSION: {siid: 99, piid: 17},
    DreameMowerProperty.PERFORMANCE_SWITCH: {siid: 99, piid: 24},
    DreameMowerProperty.AI_TEST_STATUS: {siid: 99, piid: 25},
    DreameMowerProperty.PUBLIC_KEY: {siid: 99, piid: 27},
    DreameMowerProperty.AUTO_PAIR: {siid: 99, piid: 28},
    DreameMowerProperty.MCU_VERSION: {siid: 99, piid: 31},
    DreameMowerProperty.PLATFORM_NETWORK: {siid: 99, piid: 95},
    DreameMowerProperty.STREAM_STATUS: {siid: 10001, piid: 1},
    DreameMowerProperty.STREAM_AUDIO: {siid: 10001, piid: 2},
    DreameMowerProperty.STREAM_RECORD: {siid: 10001, piid: 4},
    DreameMowerProperty.TAKE_PHOTO: {siid: 10001, piid: 5},
    DreameMowerProperty.STREAM_KEEP_ALIVE: {siid: 10001, piid: 6},
    DreameMowerProperty.STREAM_FAULT: {siid: 10001, piid: 7},
    DreameMowerProperty.CAMERA_LIGHT_BRIGHTNESS: {siid: 10001, piid: 9},
    DreameMowerProperty.CAMERA_LIGHT: {siid: 10001, piid: 10},
    DreameMowerProperty.STEAM_HUMAN_FOLLOW: {siid: 10001, piid: 110},
    DreameMowerProperty.STREAM_CRUISE_POINT: {siid: 10001, piid: 101},
    DreameMowerProperty.STREAM_PROPERTY: {siid: 10001, piid: 99},
    DreameMowerProperty.STREAM_TASK: {siid: 10001, piid: 103},
    DreameMowerProperty.STREAM_UPLOAD: {siid: 10001, piid: 1003},
    DreameMowerProperty.STREAM_CODE: {siid: 10001, piid: 1100},
    DreameMowerProperty.STREAM_SET_CODE: {siid: 10001, piid: 1101},
    DreameMowerProperty.STREAM_VERIFY_CODE: {siid: 10001, piid: 1102},
    DreameMowerProperty.STREAM_RESET_CODE: {siid: 10001, piid: 1103},
    DreameMowerProperty.STREAM_SPACE: {siid: 10001, piid: 2003},
}

# Dreame Mower action mapping
DreameMowerActionMapping = {
    DreameMowerAction.START_MOWING: {siid: 5, aiid: 1},
    DreameMowerAction.PAUSE: {siid: 5, aiid: 4},
    DreameMowerAction.DOCK: {siid: 5, aiid: 3},
    DreameMowerAction.STOP: {siid: 5, aiid: 2},
    DreameMowerAction.PULL_STATUS: {siid: 5, aiid: 10},
    DreameMowerAction.CLEAR_WARNING: {siid: 4, aiid: 3},
    DreameMowerAction.START_CUSTOM: {siid: 4, aiid: 1},
    DreameMowerAction.GET_PHOTO_INFO: {siid: 4, aiid: 6},
    DreameMowerAction.SHORTCUTS: {siid: 4, aiid: 8},
    DreameMowerAction.REQUEST_MAP: {siid: 6, aiid: 1},
    DreameMowerAction.UPDATE_MAP_DATA: {siid: 6, aiid: 2},
    DreameMowerAction.BACKUP_MAP: {siid: 6, aiid: 3},
    DreameMowerAction.WIFI_MAP: {siid: 6, aiid: 4},
    DreameMowerAction.LOCATE: {siid: 7, aiid: 1},
    DreameMowerAction.TEST_SOUND: {siid: 7, aiid: 2},
    DreameMowerAction.DELETE_SCHEDULE: {siid: 8, aiid: 1},
    DreameMowerAction.DELETE_CRUISE_SCHEDULE: {siid: 8, aiid: 2},
    DreameMowerAction.RESET_BLADES: {siid: 9, aiid: 1},
    DreameMowerAction.RESET_SIDE_BRUSH: {siid: 10, aiid: 1},
    DreameMowerAction.RESET_FILTER: {siid: 11, aiid: 1},
    DreameMowerAction.RESET_SENSOR: {siid: 16, aiid: 1},
    DreameMowerAction.RESET_TANK_FILTER: {siid: 17, aiid: 1},
    DreameMowerAction.RESET_SILVER_ION: {siid: 19, aiid: 1},
    DreameMowerAction.RESET_LENSBRUSH: {siid: 1, aiid: 3},
    DreameMowerAction.RESET_SQUEEGEE: {siid: 24, aiid: 1},
    DreameMowerAction.STREAM_VIDEO: {siid: 10001, aiid: 1},
    DreameMowerAction.STREAM_AUDIO: {siid: 10001, aiid: 2},
    DreameMowerAction.STREAM_PROPERTY: {siid: 10001, aiid: 3},
    DreameMowerAction.STREAM_CODE: {siid: 10001, aiid: 4},
}

PROPERTY_AVAILABILITY: Final = {
    DreameMowerProperty.CUSTOMIZED_CLEANING.name: lambda device: (
        not device.status.started
        and (device.status.has_saved_map or device.status.current_map is None)
        and not device.status.cleangenius_cleaning
    ),
    DreameMowerProperty.MULTI_FLOOR_MAP.name: lambda device: (
        not device.status.has_temporary_map and not device.status.started
    ),
    DreameMowerProperty.CLEANING_MODE.name: lambda device: (
        (not device.status.started)
        and not device.status.fast_mapping
        and not device.status.scheduled_clean
        and not device.status.cruising
        and (
            not device.status.customized_cleaning
            or not device.capability.custom_cleaning_mode
        )
        and not device.status.cleangenius_cleaning
        and not device.status.returning
        and not device.status.shortcut_task
    ),
    DreameMowerProperty.CLEANING_TIME.name: lambda device: (
        not device.status.fast_mapping and not device.status.cruising
    ),
    DreameMowerProperty.CLEANED_AREA.name: lambda device: (
        not device.status.fast_mapping and not device.status.cruising
    ),
    DreameMowerProperty.RELOCATION_STATUS.name: lambda device: (
        not device.status.fast_mapping
    ),
    DreameMowerProperty.INTELLIGENT_RECOGNITION.name: lambda device: (
        device.status.multi_map
    ),
    DreameMowerProperty.VOICE_ASSISTANT_LANGUAGE.name: lambda device: bool(
        device.get_property(DreameMowerProperty.VOICE_ASSISTANT) == 1
    ),
    DreameMowerProperty.STREAM_STATUS.name: lambda device: bool(
        device.get_property(DreameMowerProperty.STREAM_STATUS) is not None
    ),
    DreameMowerProperty.CAMERA_LIGHT_BRIGHTNESS.name: lambda device: bool(
        device.status.camera_light_brightness
        and device.status.camera_light_brightness != 101
        and device.status.stream_session is not None
    ),
    DreameMowerProperty.TASK_TYPE.name: lambda device: (
        device.status.task_type.value > 0
    ),
    DreameMowerProperty.CLEANING_PROGRESS.name: lambda device: bool(
        device.status.started and not device.status.cruising
    ),
    DreameMowerAutoSwitchProperty.WIDER_CORNER_COVERAGE.name: lambda device: (
        not device.status.started and not device.status.fast_mapping
    ),
    DreameMowerAutoSwitchProperty.STAIN_AVOIDANCE.name: lambda device: (
        device.status.ai_fluid_detection
    ),
    DreameMowerAutoSwitchProperty.CLEANGENIUS.name: lambda device: (
        not device.status.started
        and not device.status.fast_mapping
        and not device.status.cruising
        and not device.status.spot_cleaning
        and not device.status.zone_cleaning
    ),
    DreameMowerAutoSwitchProperty.FLOOR_DIRECTION_CLEANING.name: lambda device: (
        device.status.floor_direction_cleaning_available
    ),
    DreameMowerStrAIProperty.AI_HUMAN_DETECTION.name: lambda device: (
        device.status.ai_obstacle_detection
    ),
    DreameMowerAIProperty.AI_OBSTACLE_IMAGE_UPLOAD.name: lambda device: (
        device.status.ai_obstacle_detection
    ),
    DreameMowerAIProperty.AI_OBSTACLE_PICTURE.name: lambda device: (
        device.status.ai_obstacle_detection
    ),
    DreameMowerAIProperty.AI_PET_DETECTION.name: lambda device: (
        device.status.ai_obstacle_detection
    ),
    DreameMowerAIProperty.AI_FURNITURE_DETECTION.name: lambda device: (
        device.status.ai_obstacle_detection
    ),
    DreameMowerAIProperty.AI_FLUID_DETECTION.name: lambda device: (
        device.status.ai_obstacle_detection
    ),
    DreameMowerAIProperty.FUZZY_OBSTACLE_DETECTION.name: lambda device: (
        device.status.ai_obstacle_detection
    ),
    DreameMowerAIProperty.AI_PET_AVOIDANCE.name: lambda device: (
        device.status.ai_obstacle_detection and device.status.ai_pet_detection
    ),
    DreameMowerAIProperty.PET_PICTURE.name: lambda device: (
        device.status.ai_obstacle_detection and device.status.ai_pet_detection
    ),
    DreameMowerAIProperty.PET_FOCUSED_DETECTION.name: lambda device: (
        device.status.ai_obstacle_detection and device.status.ai_pet_detection
    ),
    DreameMowerAutoSwitchProperty.CLEANING_ROUTE.name: lambda device: (
        not device.status.has_temporary_map
        and device.status.segments
        and device.status.cleaning_route.value > 0
        and not device.status.fast_mapping
        and not device.status.started
        and (
            not device.status.customized_cleaning
            or not device.capability.custom_cleaning_mode
        )
        and not device.status.cleangenius_cleaning
    ),
    DreameMowerProperty.FIRST_CLEANING_DATE.name: lambda device: device.get_property(
        DreameMowerProperty.FIRST_CLEANING_DATE
    ),
    "map_rotation": lambda device: bool(
        device.status.selected_map is not None
        and device.status.selected_map.rotation is not None
        and not device.status.fast_mapping
        and device.status.has_saved_map
    ),
    "selected_map": lambda device: bool(
        device.status.multi_map
        and not device.status.fast_mapping
        and device.status.map_list
        and device.status.selected_map
        and device.status.selected_map.map_name
        and device.status.selected_map.map_id in device.status.map_list
    ),
    "current_zone": lambda device: (
        device.status.current_zone is not None and not device.status.fast_mapping
    ),
    "cleaning_history": lambda device: bool(
        device.status.last_cleaning_time is not None
    ),
    "cruising_history": lambda device: bool(
        device.status.last_cruising_time is not None
    ),
    "cleaning_sequence": lambda device: (
        not device.status.started
        and device.status.has_saved_map
        and device.status.current_segments
        and not device.status.cleangenius_cleaning
        and next(iter(device.status.current_segments.values())).order is not None
    ),
    "camera_light_brightness_auto": lambda device: (
        device.status.camera_light_brightness
        and device.status.stream_session is not None
    ),
    "dnd_start": lambda device: device.status.dnd,
    "dnd_end": lambda device: device.status.dnd,
    "off_peak_charging_start": lambda device: device.status.off_peak_charging,
    "off_peak_charging_end": lambda device: device.status.off_peak_charging,
}

ACTION_AVAILABILITY: Final = {
    DreameMowerAction.RESET_BLADES.name: lambda device: bool(
        device.status.blades_life < 100
    ),
    DreameMowerAction.RESET_SIDE_BRUSH.name: lambda device: bool(
        device.status.side_brush_life < 100
    ),
    DreameMowerAction.RESET_FILTER.name: lambda device: bool(
        device.status.filter_life < 100
    ),
    DreameMowerAction.RESET_SENSOR.name: lambda device: bool(
        device.status.sensor_dirty_life < 100
    ),
    DreameMowerAction.RESET_TANK_FILTER.name: lambda device: bool(
        device.status.tank_filter_life < 100
    ),
    DreameMowerAction.RESET_SILVER_ION.name: lambda device: bool(
        device.status.silver_ion_life < 100
    ),
    DreameMowerAction.RESET_LENSBRUSH.name: lambda device: bool(
        device.status.lensbrush_life < 100
    ),
    DreameMowerAction.RESET_SQUEEGEE.name: lambda device: bool(
        device.status.squeegee_life < 100
    ),
    DreameMowerAction.CLEAR_WARNING.name: lambda device: device.status.has_warning,
    DreameMowerAction.START_MOWING.name: lambda device: (
        not (device.status.started)
        or device.status.paused
        or device.status.returning
        or device.status.returning_paused
    ),
    DreameMowerAction.DOCK.name: lambda device: (
        not device.status.docked and not device.status.returning
    ),
    DreameMowerAction.PAUSE.name: lambda device: (
        device.status.started
        and not (device.status.returning_paused or device.status.paused)
    ),
    DreameMowerAction.STOP.name: lambda device: (
        device.status.started or device.status.returning or device.status.paused
    ),
    "start_fast_mapping": lambda device: device.status.mapping_available,
    "start_mapping": lambda device: device.status.mapping_available,
    "start_recleaning": lambda device: (
        not device.status.started and device.status.second_cleaning_available
    ),
}


def PIID(
    property: DreameMowerProperty, mapping=DreameMowerPropertyMapping
) -> int | None:
    if property in mapping:
        return mapping[property][piid]


def DIID(
    property: DreameMowerProperty, mapping=DreameMowerPropertyMapping
) -> str | None:
    if property in mapping:
        return f"{mapping[property][siid]}.{mapping[property][piid]}"


class RobotType(IntEnum):
    LIDAR = 0
    VSLAM = 1


class PathType(str, Enum):  # noqa: UP042
    LINE = "L"
    SWEEP = "S"


class ObstacleType(IntEnum):
    UNKNOWN = 0
    BASE = 128
    SCALE = 129
    THREAD = 130
    WIRE = 131
    TOY = 132
    SHOES = 133
    SOCK = 134
    POO = 135
    TRASH_CAN = 136
    FABRIC = 137
    POWER_STRIP = 138
    STAIN = 139
    OBSTACLE = 142
    PET = 158
    CLEANING_TOOLS = 163
    NEGLECTED_ZONE = 200
    EASY_TO_STUCK_FURNITURE = 201


class ObstacleIgnoreStatus(IntEnum):
    UNKNOWN = -1
    NOT_IGNORED = 0
    MANUALLY_IGNORED = 1
    AUTOMATICALLY_IGNORED = 2


class ObstaclePictureStatus(IntEnum):
    UNKNOWN = -1
    DISABLED = 0
    UPLOADING = 1
    UPLOADED = 2
    UPLOAD_FAILED = 3


class SegmentNeglectReason(IntEnum):
    BLOCKED_BY_VIRTUAL_WALL = 2
    BLOCKED_BY_DOOR = 3
    BLOCKED_BY_TRESHOLD = 4
    BLOCKED_BY_OBSTACLE = 5
    BLOCKED_BY_HIDDEN_OBSTACLE = 8
    BLOCKED_BY_DYNAMIC_OBSTACLE = 9
    PASSAGE_TOO_LOW = 10
    STEP_TOO_LOW = 27


class TaskInterruptReason(IntEnum):
    UNKNOWN = -1
    TASK_COMPLETED = 0
    ROBOT_LIFTED = 11
    ROBOT_FALLEN = 12
    CLIFF_SENSOR_ERROR = 13
    BRUSH_ENTANGLED_BY_OBSTACLE = 21
    BRUSH_ENTANGLED_BY_OBJECT = 23
    LASER_DISTANCE_SENSOR_ERROR = 24
    ROBOT_IS_STUCK_ON_STEP = 25
    ROBOT_IS_STUCK_ON_OBSTACLE = 26
    BASE_STATION_POWERED_OFF = 27
    ABNORMAL_DOCKING = 101
    CANNOT_FIND_BASE_STATION = 102


class FurnitureType(IntEnum):
    SINGLE_BED = 1
    DOUBLE_BED = 2
    ARM_CHAIR = 3
    TWO_SEAT_SOFA = 4
    THREE_SEAT_SOFA = 5
    DINING_TABLE = 6
    NIGHTSTANT = 7
    COFFEE_TABLE = 8
    TOILET = 9
    LITTER_BOX = 10
    PET_BED = 11
    FOOD_BOWL = 12
    PET_TOILET = 13
    REFRIGERATOR = 14
    WASHING_MACHINE = 15
    ENCLOSED_LITTER_BOX = 16
    AIR_CONDITIONER = 17
    TV_CABINET = 18
    BOOKSHELF = 19
    SHOE_CABINET = 20
    WARDROBE = 21
    GREENERY = 22
    FLOOR_MIRROR = 23
    L_SHAPED_SOFA = 24
    ROUND_COFFEE_TABLE = 25


class CleansetType(IntEnum):
    NONE = 0
    DEFAULT = 1
    CLEANING_MODE = 2
    CLEANING_ROUTE = 4


class DeviceCapability(IntEnum):
    OBSTACLE_IMAGE_CROP = 5
    FLOOR_DIRECTION_CLEANING = 10
    LARGE_PARTICLES_BOOST = 11
    SEGMENT_VISIBILITY = 12
    PET_FURNITURE = 16
    CLEANING_ROUTE = 17
    SEGMENT_SLOW_CLEAN_ROUTE = 19
    TASK_TYPE = 21
    EXTENDED_FURNITURES = 23
    CLEANGENIUS = 25
    CLEANGENIUS_AUTO = 26
    FLUID_DETECTION = 27
    AUTO_RENAME_SEGMENT = 31
    DISABLE_SENSOR_CLEANING = 32
    FLOOR_MATERIAL = 33
    GEN5 = 34
    NEW_FURNITURES = 35
    SAVED_FURNITURES = 36
    OBSTACLES = 37
    NEW_STATE = 45
    CAMERA_STREAMING = 46
    LENSBRUSH = 47


class DreameMowerDeviceCapability:
    def __init__(self, device) -> None:
        self.list = None
        self.lidar_navigation = True
        self.multi_floor_map = True
        self.ai_detection = False
        self.customized_cleaning = False
        self.auto_switch_settings = False
        self.wifi_map = False
        self.backup_map = False
        self.dnd = False
        self.dnd_task = False
        self.shortcuts = False
        self.fill_light = False
        self.voice_assistant = False
        self.pet_detective = False
        self.off_peak_charging = False
        self.max_suction_power = False
        self.obstacle_image_crop = False
        self.map_object_offset = True
        self.robot_type = RobotType.LIDAR
        self.floor_material = False
        self.floor_direction_cleaning = False
        self.segment_visibility = False
        self.cleangenius = False
        self.cleangenius_auto = False
        self.large_particles_boost = False
        self.fluid_detection = False
        self.cleaning_route = False
        self.segment_slow_clean_route = True
        self.pet_furniture = False
        self.task_type = False
        self.disable_sensor_cleaning = False
        self.auto_rename_segment = False
        self.saved_furnitures = False
        self.extended_furnitures = False
        self.new_furnitures = False
        self.pet_furnitures = False
        self.obstacles = False
        self.auto_charging = False
        self.new_state = False
        self.camera_streaming = False
        self.gen5 = False
        self.lensbrush = False
        self._custom_cleaning_mode = False
        self._device = device

    def refresh(self, device_capabilities):
        self.lidar_navigation = bool(
            self._device.get_property(DreameMowerProperty.MAP_SAVING) is None
        )
        self.multi_floor_map = bool(
            self._device.get_property(DreameMowerProperty.MULTI_FLOOR_MAP) is not None
            and self.lidar_navigation
        )
        self.ai_detection = bool(
            self._device.get_property(DreameMowerProperty.AI_DETECTION) is not None
        )
        self.customized_cleaning = bool(
            self._device.get_property(DreameMowerProperty.CUSTOMIZED_CLEANING)
            is not None
        )
        self.auto_switch_settings = bool(
            self._device.get_property(DreameMowerProperty.AUTO_SWITCH_SETTINGS)
            is not None
        )
        self.wifi_map = bool(
            self._device.get_property(DreameMowerProperty.WIFI_MAP) is not None
        )
        self.backup_map = bool(
            self._device.get_property(DreameMowerProperty.MAP_BACKUP_STATUS) is not None
        )
        self.dnd_task = bool(
            self._device.get_property(DreameMowerProperty.DND_TASK) is not None
        )
        self.dnd = bool(
            self.dnd_task
            or self._device.get_property(DreameMowerProperty.DND) is not None
        )
        self.shortcuts = bool(
            self._device.get_property(DreameMowerProperty.SHORTCUTS) is not None
        )
        self.off_peak_charging = bool(
            self._device.get_property(DreameMowerProperty.OFF_PEAK_CHARGING) is not None
        )
        camera_light = self._device.get_property(
            DreameMowerProperty.CAMERA_LIGHT_BRIGHTNESS
        )
        self.voice_assistant = bool(
            self._device.get_property(DreameMowerProperty.VOICE_ASSISTANT) is not None
        )

        model = ""
        if self._device.info and self._device.info.model:
            model = (
                self._device.info.model.replace("mower.", "")
                .replace("dreame.", "")
                .replace("xiaomi.", "")
            )
            device_capability = device_capabilities.get(model)
            while device_capability and isinstance(device_capability, str):
                device_capability = device_capabilities.get(device_capability)
            if device_capability:
                version = self._device.info.version if self._device.info.version else 1
                for v in device_capability:
                    capability = v[0]
                    if capability in DeviceCapability._value2member_map_:
                        capability = DeviceCapability(capability)
                        param = capability.name.lower()
                        if param and hasattr(self, param):
                            setattr(self, param, bool(version >= v[1]))

        # self.camera_streaming = bool(
        #    self.camera_streaming
        #    and (
        #        camera_light is not None
        #        or self._device.get_property(DreameMowerProperty.CRUISE_SCHEDULE)
        #        is not None
        #    )
        # )
        self.lensbrush = bool(
            self.lensbrush
            or self._device.get_property(DreameMowerProperty.LENSBRUSH_LEFT)
        )
        self.fill_light = bool(
            self.camera_streaming
            and camera_light is not None
            and len(camera_light) < 5
            and str(camera_light).isnumeric()
        )
        self.pet_detective = bool(
            self.pet_detective
            and self._device.get_property(DreameMowerProperty.PET_DETECTIVE) is not None
        )
        self.task_type = bool(
            self.task_type
            and self._device.get_property(DreameMowerProperty.TASK_TYPE) is not None
        )
        if not self.cleaning_route:
            self.segment_slow_clean_route = False
        self.disable_sensor_cleaning = (
            self.disable_sensor_cleaning
            or not self.lidar_navigation
            or self._device.get_property(DreameMowerProperty.SENSOR_DIRTY_LEFT) is None
            or (
                not self.camera_streaming
                and self._device.get_property(DreameMowerProperty.OBSTACLE_AVOIDANCE)
                is None
            )
        )
        self.lensbrush = bool("p2255" in model)
        self.map_object_offset = bool(self.lidar_navigation and "p20" in model)
        self.robot_type = RobotType.LIDAR

        self.list = [
            key
            for key, value in self.__dict__.items()
            if not callable(value) and not key.startswith("_") and value == True  # noqa: E712 - preserve exact legacy equality
        ]
        if self.custom_cleaning_mode:
            self.list.append("custom_cleaning_mode")
        if self.cruising:
            self.list.append("cruising")
        if self.map:
            self.list.append("map")

    @property
    def map(self) -> bool:
        """Returns true when mapping feature is available."""
        return bool(self._device._map_manager is not None)

    @property
    def custom_cleaning_mode(self) -> bool:
        """Returns true if customized cleaning mode can be set to segments."""
        if self.auto_switch_settings:
            return True
        segments = self._device.status.current_segments
        if not self._custom_cleaning_mode:
            if segments:
                if next(iter(segments.values())).cleaning_mode is not None:
                    self._custom_cleaning_mode = True
                    return True
        return self._custom_cleaning_mode and (
            not segments or next(iter(segments.values())).cleaning_mode is not None
        )

    @property
    def cruising(self) -> bool:
        if not self.lidar_navigation:
            return False
        return bool(
            (
                self._device.status.current_map
                and self._device.status.current_map.predefined_points is not None
            )
            or self._device.get_property(DreameMowerProperty.CRUISE_SCHEDULE)
            is not None
            or self._device.status.fill_light is not None
        )


@dataclass
class DirtyData:
    value: Any = None
    previous_value: Any = None
    update_time: float = None


@dataclass
class Shortcut:
    id: int = -1
    name: str = None
    map_id: int = None
    running: bool = False
    tasks: list[list[ShortcutTask]] = None


@dataclass
class ShortcutTask:
    segment_id: int = None
    cleaning_times: int = None
    cleaning_mode: int = None


@dataclass
class DNDTask:
    id: int = -1
    start_time: str = None
    end_time: str = None
    enabled: bool = False
    weekdays: int = 127
    st: int = 0


@dataclass
class GoToZoneSettings:
    x: int = None
    y: int = None
    stop: bool = False
    cleaning_mode: int = None
    size: int = 50


__all__ = [
    "SEGMENT_TYPE_CODE_TO_NAME",
    "SEGMENT_TYPE_CODE_TO_HA_ICON",
    "FURNITURE_TYPE_TO_DIMENSIONS",
    "FURNITURE_V2_TYPE_TO_DIMENSIONS",
    "piid",
    "siid",
    "aiid",
    "ATTR_A",
    "ATTR_X",
    "ATTR_X0",
    "ATTR_X1",
    "ATTR_X2",
    "ATTR_X3",
    "ATTR_Y",
    "ATTR_Y0",
    "ATTR_Y1",
    "ATTR_Y2",
    "ATTR_Y3",
    "ATTR_CHARGER",
    "ATTR_IS_EMPTY",
    "ATTR_NO_GO_AREAS",
    "ATTR_PREDEFINED_POINTS",
    "ATTR_VIRTUAL_WALLS",
    "ATTR_PATHWAYS",
    "ATTR_ZONES",
    "ATTR_ROBOT_POSITION",
    "ATTR_MAP_ID",
    "ATTR_MAP_NAME",
    "ATTR_ROTATION",
    "ATTR_TIMESTAMP",
    "ATTR_UPDATED",
    "ATTR_ACTIVE_AREAS",
    "ATTR_ACTIVE_POINTS",
    "ATTR_ACTIVE_CRUISE_POINTS",
    "ATTR_ACTIVE_SEGMENTS",
    "ATTR_FRAME_ID",
    "ATTR_MAP_INDEX",
    "ATTR_ZONE_ID",
    "ATTR_ZONE_ICON",
    "ATTR_UNIQUE_ID",
    "ATTR_FLOOR_MATERIAL",
    "ATTR_FLOOR_MATERIAL_DIRECTION",
    "ATTR_VISIBILITY",
    "ATTR_NAME",
    "ATTR_OUTLINE",
    "ATTR_CENTER",
    "ATTR_ORDER",
    "ATTR_CLEANING_TIMES",
    "ATTR_CLEANING_MODE",
    "ATTR_CLEANING_ROUTE",
    "ATTR_TYPE",
    "ATTR_INDEX",
    "ATTR_ICON",
    "ATTR_COLOR_INDEX",
    "ATTR_OBSTACLES",
    "ATTR_POSSIBILTY",
    "ATTR_PICTURE_STATUS",
    "ATTR_IGNORE_STATUS",
    "ATTR_ZONE",
    "ATTR_ROUTER_POSITION",
    "ATTR_FURNITURES",
    "ATTR_STARTUP_METHOD",
    "ATTR_RECOVERY_MAP_LIST",
    "ATTR_WIDTH",
    "ATTR_HEIGHT",
    "ATTR_SIZE_TYPE",
    "ATTR_ANGLE",
    "ATTR_SCALE",
    "ATTR_COMPLETED",
    "ATTR_FIRMWARE_VERSION",
    "ATTR_AP",
    "ATTR_COLOR_SCHEME",
    "DreameMowerChargingStatus",
    "DreameMowerErrorCode",
    "DreameMowerState",
    "DreameMowerStateOld",
    "DreameMowerCleaningMode",
    "DreameMowerRelocationStatus",
    "DreameMowerTaskStatus",
    "DreameMowerStatus",
    "DreameMowerDustCollection",
    "DreameMowerAutoEmptyStatus",
    "DreameMowerSelfCleanArea",
    "DreameMowerCleaningRoute",
    "DreameMowerWiderCornerCoverage",
    "DreameMowerCleanGenius",
    "DreameMowerSecondCleaning",
    "DreameMowerFloorMaterial",
    "DreameMowerFloorMaterialDirection",
    "DreameMowerSegmentVisibility",
    "DreameMowerVoiceAssistantLanguage",
    "DreameMowerStreamStatus",
    "DreameMowerTaskType",
    "DreameMapRecoveryStatus",
    "DreameMapBackupStatus",
    "DreameMowerProperty",
    "DreameMowerAutoSwitchProperty",
    "DreameMowerStrAIProperty",
    "DreameMowerAIProperty",
    "DreameMowerAction",
    "DreameMowerPropertyMapping",
    "DreameMowerActionMapping",
    "PROPERTY_AVAILABILITY",
    "ACTION_AVAILABILITY",
    "PIID",
    "DIID",
    "RobotType",
    "PathType",
    "ObstacleType",
    "ObstacleIgnoreStatus",
    "ObstaclePictureStatus",
    "SegmentNeglectReason",
    "TaskInterruptReason",
    "FurnitureType",
    "CleansetType",
    "DeviceCapability",
    "DreameMowerDeviceCapability",
    "DirtyData",
    "Shortcut",
    "ShortcutTask",
    "DNDTask",
    "GoToZoneSettings",
]
