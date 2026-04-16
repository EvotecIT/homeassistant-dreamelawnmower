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
DreameLawnMowerSnapshot = _models.DreameLawnMowerSnapshot
MODEL_NAME_MAP = _models.MODEL_NAME_MAP
SUPPORTED_ACCOUNT_TYPES = _models.SUPPORTED_ACCOUNT_TYPES

__all__ = [
    "DreameLawnMowerAuthError",
    "DreameLawnMowerClient",
    "DreameLawnMowerConnectionError",
    "DreameLawnMowerDescriptor",
    "DreameLawnMowerError",
    "DreameLawnMowerSnapshot",
    "DreameLawnMowerTwoFactorRequiredError",
    "MODEL_NAME_MAP",
    "SUPPORTED_ACCOUNT_TYPES",
]

