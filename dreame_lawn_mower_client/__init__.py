"""Public Python package wrapper for the Dreame lawn mower client."""

from ._loader import load_internal_module

_client = load_internal_module("client")
_models = load_internal_module("models")

DreameLawnMowerClient = _client.DreameLawnMowerClient
DreameLawnMowerError = _client.DreameLawnMowerError
DreameLawnMowerAuthError = _client.DreameLawnMowerAuthError
DreameLawnMowerConnectionError = _client.DreameLawnMowerConnectionError
DreameLawnMowerTwoFactorRequiredError = _client.DreameLawnMowerTwoFactorRequiredError
DreameLawnMowerDescriptor = _models.DreameLawnMowerDescriptor
DreameLawnMowerMapSummary = _models.DreameLawnMowerMapSummary
DreameLawnMowerSnapshot = _models.DreameLawnMowerSnapshot
DISPLAY_NAME_ALIASES = _models.DISPLAY_NAME_ALIASES
MODEL_NAME_MAP = _models.MODEL_NAME_MAP
SUPPORTED_ACCOUNT_TYPES = _models.SUPPORTED_ACCOUNT_TYPES
display_name_for_model = _models.display_name_for_model
map_summary_from_map_data = _models.map_summary_from_map_data

__all__ = [
    "DISPLAY_NAME_ALIASES",
    "DreameLawnMowerAuthError",
    "DreameLawnMowerClient",
    "DreameLawnMowerConnectionError",
    "DreameLawnMowerDescriptor",
    "DreameLawnMowerError",
    "DreameLawnMowerMapSummary",
    "DreameLawnMowerSnapshot",
    "DreameLawnMowerTwoFactorRequiredError",
    "MODEL_NAME_MAP",
    "SUPPORTED_ACCOUNT_TYPES",
    "display_name_for_model",
    "map_summary_from_map_data",
]
