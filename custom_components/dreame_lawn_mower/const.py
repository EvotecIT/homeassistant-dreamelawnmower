"""Constants for the Dreame lawn mower integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "dreame_lawn_mower"

CONF_ACCOUNT_TYPE = "account_type"
CONF_COUNTRY = "country"
CONF_DID = "did"
CONF_HOST = "host"
CONF_MAC = "mac"
CONF_MODEL = "model"
CONF_NAME = "name"
CONF_PASSWORD = "password"
CONF_MAP_LABEL_SCALE = "map_label_scale"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_TOKEN = "token"
CONF_USERNAME = "username"
CONF_VIDEO_TRANSPORT = "video_transport"
CONF_XP2P_LIBRARY_PATH = "xp2p_library_path"
CONF_XP2P_RUNNER_COMMAND = "xp2p_runner_command"
CONF_XP2P_RUNNER_MODE = "xp2p_runner_mode"

DEFAULT_COUNTRY = "eu"
DEFAULT_MAP_LABEL_SCALE = 1.0
DEFAULT_SCAN_INTERVAL_SECONDS = 60
MIN_MAP_LABEL_SCALE = 0.5
MAX_MAP_LABEL_SCALE = 4.0
MIN_SCAN_INTERVAL_SECONDS = 15
MAX_SCAN_INTERVAL_SECONDS = 300

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
DEFAULT_VIDEO_TRANSPORT = VIDEO_TRANSPORT_CLOUD
VIDEO_TRANSPORT_OPTIONS = {
    VIDEO_TRANSPORT_AUTO: "Prefer same-LAN, then cached/cloud XP2P",
    VIDEO_TRANSPORT_LAN: "Same-LAN only (requires device firmware support)",
    VIDEO_TRANSPORT_CLOUD: "XP2P (cloud provisioned)",
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
    Platform.UPDATE,
]

ACTIVITY_MOWING = "mowing"
ACTIVITY_DOCKED = "docked"
ACTIVITY_PAUSED = "paused"
ACTIVITY_RETURNING = "returning"
ACTIVITY_ERROR = "error"
ACTIVITY_IDLE = "idle"
