"""Public Python package wrapper for the Dreame lawn mower client."""

from ._loader import load_internal_module

_app_protocol = load_internal_module("app_protocol")
_camera_probe = load_internal_module("camera_probe")
_client = load_internal_module("client")
_map_probe = load_internal_module("map_probe")
_models = load_internal_module("models")

DreameLawnMowerClient = _client.DreameLawnMowerClient
DreameLawnMowerError = _client.DreameLawnMowerError
DreameLawnMowerAuthError = _client.DreameLawnMowerAuthError
DreameLawnMowerConnectionError = _client.DreameLawnMowerConnectionError
DreameLawnMowerTwoFactorRequiredError = _client.DreameLawnMowerTwoFactorRequiredError
DreameLawnMowerCameraFeatureSupport = _models.DreameLawnMowerCameraFeatureSupport
DreameLawnMowerDescriptor = _models.DreameLawnMowerDescriptor
DreameLawnMowerMapSummary = _models.DreameLawnMowerMapSummary
DreameLawnMowerMapView = _models.DreameLawnMowerMapView
DreameLawnMowerRemoteControlSupport = _models.DreameLawnMowerRemoteControlSupport
DreameLawnMowerSnapshot = _models.DreameLawnMowerSnapshot
DISPLAY_NAME_ALIASES = _models.DISPLAY_NAME_ALIASES
MODEL_NAME_MAP = _models.MODEL_NAME_MAP
MOWER_STATE_LABELS = _app_protocol.MOWER_STATE_LABELS
MOWER_STATE_PROPERTY_KEY = _app_protocol.MOWER_STATE_PROPERTY_KEY
MOWER_ERROR_PROPERTY_KEY = _app_protocol.MOWER_ERROR_PROPERTY_KEY
MOWER_PROPERTY_HINTS = _app_protocol.MOWER_PROPERTY_HINTS
MOWER_RAW_STATUS_PROPERTY_KEY = _app_protocol.MOWER_RAW_STATUS_PROPERTY_KEY
CAMERA_PROBE_PROPERTY_KEYS = _camera_probe.CAMERA_PROBE_PROPERTY_KEYS
MAP_PROBE_PROPERTY_KEYS = _map_probe.MAP_PROBE_PROPERTY_KEYS
SUPPORTED_ACCOUNT_TYPES = _models.SUPPORTED_ACCOUNT_TYPES
build_camera_probe_payload = _camera_probe.build_camera_probe_payload
build_cloud_property_summary = _map_probe.build_cloud_property_summary
build_map_probe_payload = _map_probe.build_map_probe_payload
display_name_for_model = _models.display_name_for_model
map_summary_from_map_data = _models.map_summary_from_map_data
map_summary_to_dict = _models.map_summary_to_dict
mower_error_label = _app_protocol.mower_error_label
mower_state_label = _app_protocol.mower_state_label

__all__ = [
    "DISPLAY_NAME_ALIASES",
    "DreameLawnMowerAuthError",
    "DreameLawnMowerCameraFeatureSupport",
    "DreameLawnMowerClient",
    "DreameLawnMowerConnectionError",
    "DreameLawnMowerDescriptor",
    "DreameLawnMowerError",
    "DreameLawnMowerMapSummary",
    "DreameLawnMowerMapView",
    "DreameLawnMowerRemoteControlSupport",
    "DreameLawnMowerSnapshot",
    "DreameLawnMowerTwoFactorRequiredError",
    "MODEL_NAME_MAP",
    "CAMERA_PROBE_PROPERTY_KEYS",
    "MAP_PROBE_PROPERTY_KEYS",
    "MOWER_ERROR_PROPERTY_KEY",
    "MOWER_PROPERTY_HINTS",
    "MOWER_RAW_STATUS_PROPERTY_KEY",
    "MOWER_STATE_LABELS",
    "MOWER_STATE_PROPERTY_KEY",
    "SUPPORTED_ACCOUNT_TYPES",
    "build_camera_probe_payload",
    "build_cloud_property_summary",
    "build_map_probe_payload",
    "display_name_for_model",
    "map_summary_from_map_data",
    "map_summary_to_dict",
    "mower_error_label",
    "mower_state_label",
]
