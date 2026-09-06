"""Constants for the Dreame lawn mower integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "dreame_lawn_mower"
CONFIG_ENTRY_VERSION = 1
CONFIG_ENTRY_MINOR_VERSION = 2

CONF_ACCOUNT_TYPE = "account_type"
CONF_COUNTRY = "country"
CONF_DID = "did"
CONF_HOST = "host"
CONF_MAC = "mac"
CONF_MODEL = "model"
CONF_NAME = "name"
CONF_NOTIFICATION_MODE = "notification_mode"
CONF_PASSWORD = "password"
CONF_MAP_LABEL_SCALE = "map_label_scale"
CONF_MAP_ROTATION = "map_rotation"
CONF_MAP_ROTATIONS = "map_rotations"
CONF_MAP_THEME = "map_theme"
CONF_MAP_STROKE_SCALE = "map_stroke_scale"
CONF_MAP_MARKER_SCALE = "map_marker_scale"
CONF_MAP_MARKER_IMAGE = "map_marker_image"
CONF_MAP_SPOT_AREA_STYLE = "map_spot_area_style"
CONF_MAP_MOWING_PATH_STYLE = "map_mowing_path_style"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_TOKEN = "token"
CONF_USERNAME = "username"
CONF_VIDEO_RETENTION = "video_retention"
CONF_VIDEO_TRANSPORT = "video_transport"
CONF_XP2P_LIBRARY_PATH = "xp2p_library_path"
CONF_XP2P_RUNNER_COMMAND = "xp2p_runner_command"
CONF_XP2P_RUNNER_MODE = "xp2p_runner_mode"

DEFAULT_COUNTRY = "eu"
DEFAULT_MAP_LABEL_SCALE = 1.0
DEFAULT_MAP_ROTATION = 0
DEFAULT_MAP_THEME = "emerald"
DEFAULT_MAP_STROKE_SCALE = 1.0
DEFAULT_MAP_MARKER_SCALE = 1.0
DEFAULT_MAP_SPOT_AREA_STYLE = "hidden"
DEFAULT_MAP_MOWING_PATH_STYLE = "subtle"
DEFAULT_SCAN_INTERVAL_SECONDS = 60
NOTIFICATION_MODE_OFF = "off"
NOTIFICATION_MODE_FAULTS = "faults"
NOTIFICATION_MODE_FAULTS_AND_WARNINGS = "faults_and_warnings"
DEFAULT_NOTIFICATION_MODE = NOTIFICATION_MODE_OFF
NOTIFICATION_MODE_OPTIONS = {
    NOTIFICATION_MODE_OFF: "Off",
    NOTIFICATION_MODE_FAULTS: "Hard faults",
    NOTIFICATION_MODE_FAULTS_AND_WARNINGS: "Hard faults and warnings",
}
MIN_MAP_LABEL_SCALE = 0.5
MAX_MAP_LABEL_SCALE = 4.0
MIN_SCAN_INTERVAL_SECONDS = 15
MAX_SCAN_INTERVAL_SECONDS = 300
MAP_ROTATION_OPTIONS = {
    0: "0 degrees",
    90: "90 degrees clockwise",
    180: "180 degrees",
    270: "270 degrees clockwise",
}
MAP_THEME_OPTIONS = {
    "emerald": "Emerald",
    "mint": "Mint (decorative stripes)",
    "dark": "Dark",
    "midnight": "Midnight",
    "high_contrast": "High contrast",
}
MAP_SPOT_AREA_STYLE_OPTIONS = {
    "hidden": "Hidden (recommended)",
    "outline": "Outline only",
    "filled": "Filled",
}
MAP_MOWING_PATH_STYLE_OPTIONS = {
    "hidden": "Hidden",
    "subtle": "Subtle (recommended)",
    "detailed": "Detailed",
}

ACCOUNT_TYPE_DREAME = "dreame"
ACCOUNT_TYPE_MOVA = "mova"
ACCOUNT_TYPE_OPTIONS = {
    ACCOUNT_TYPE_DREAME: "Dreamehome",
    ACCOUNT_TYPE_MOVA: "MOVAhome",
}
COUNTRY_OPTIONS = ["cn", "eu", "us", "ru", "sg"]

XP2P_RUNNER_MODE_PROCESS = "process"
XP2P_RUNNER_MODE_ONE_SHOT = "one-shot"
XP2P_RUNNER_MODE_OPTIONS = {
    XP2P_RUNNER_MODE_PROCESS: "Persistent process",
    XP2P_RUNNER_MODE_ONE_SHOT: "One-shot command",
}

VIDEO_TRANSPORT_AUTO = "auto"
VIDEO_TRANSPORT_LAN = "lan"
VIDEO_TRANSPORT_CLOUD = "cloud"
DEFAULT_VIDEO_TRANSPORT = VIDEO_TRANSPORT_AUTO
VIDEO_TRANSPORT_OPTIONS = {
    VIDEO_TRANSPORT_AUTO: "Automatic XP2P with cached restart",
    VIDEO_TRANSPORT_CLOUD: "XP2P (cloud provisioned)",
}

VIDEO_RETENTION_BALANCED = "balanced"
VIDEO_RETENTION_BATTERY_SAVER = "battery_saver"
VIDEO_RETENTION_PRIORITY = "video_priority"
DEFAULT_VIDEO_RETENTION = VIDEO_RETENTION_BALANCED
VIDEO_RETENTION_OPTIONS = {
    VIDEO_RETENTION_BALANCED: "Balanced (live viewing stays ready)",
    VIDEO_RETENTION_BATTERY_SAVER: "Battery saver (stop after viewers leave)",
    VIDEO_RETENTION_PRIORITY: "Video priority (snapshots also stay ready)",
}

PLATFORMS: list[Platform] = [
    Platform.CAMERA,
    Platform.CALENDAR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.LAWN_MOWER,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
    Platform.UPDATE,
]

ACTIVITY_MOWING = "mowing"
ACTIVITY_DOCKED = "docked"
ACTIVITY_PAUSED = "paused"
ACTIVITY_RETURNING = "returning"
ACTIVITY_ERROR = "error"
ACTIVITY_IDLE = "idle"
