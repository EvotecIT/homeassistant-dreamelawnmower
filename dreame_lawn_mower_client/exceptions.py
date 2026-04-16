"""Wrapper module for reusable mower exceptions."""

from ._loader import load_internal_module

_internal = load_internal_module("client")
DreameLawnMowerError = _internal.DreameLawnMowerError
DreameLawnMowerAuthError = _internal.DreameLawnMowerAuthError
DreameLawnMowerConnectionError = _internal.DreameLawnMowerConnectionError
DreameLawnMowerTwoFactorRequiredError = _internal.DreameLawnMowerTwoFactorRequiredError

__all__ = [
    "DreameLawnMowerError",
    "DreameLawnMowerAuthError",
    "DreameLawnMowerConnectionError",
    "DreameLawnMowerTwoFactorRequiredError",
]
