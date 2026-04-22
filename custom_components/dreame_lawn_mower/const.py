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
CONF_SCAN_INTERVAL = "scan_interval"
CONF_TOKEN = "token"
CONF_USERNAME = "username"

DEFAULT_COUNTRY = "eu"
DEFAULT_SCAN_INTERVAL_SECONDS = 60
MIN_SCAN_INTERVAL_SECONDS = 15
MAX_SCAN_INTERVAL_SECONDS = 300

ACCOUNT_TYPE_DREAME = "dreame"
ACCOUNT_TYPE_MOVA = "mova"
ACCOUNT_TYPE_OPTIONS = {
    ACCOUNT_TYPE_DREAME: "Dreamehome",
    ACCOUNT_TYPE_MOVA: "MOVAhome",
}
COUNTRY_OPTIONS = ["cn", "eu", "us", "ru", "sg"]

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
