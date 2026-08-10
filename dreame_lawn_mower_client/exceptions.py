"""Wrapper module for reusable mower exceptions."""

from ._loader import load_internal_module

_internal = load_internal_module()
DreameLawnMowerError = _internal.DreameLawnMowerError
DreameLawnMowerAuthError = _internal.DreameLawnMowerAuthError
DreameLawnMowerConnectionError = _internal.DreameLawnMowerConnectionError
DreameLawnMowerCommandRejectedError = (
    _internal.DreameLawnMowerCommandRejectedError
)
DreameLawnMowerTwoFactorRequiredError = _internal.DreameLawnMowerTwoFactorRequiredError

__all__ = [
    "DreameLawnMowerError",
    "DreameLawnMowerAuthError",
    "DreameLawnMowerConnectionError",
    "DreameLawnMowerCommandRejectedError",
    "DreameLawnMowerTwoFactorRequiredError",
]
