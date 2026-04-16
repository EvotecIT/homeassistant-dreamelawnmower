"""Compatibility wrapper for the reusable mower client package."""

from .dreame_client import (
    DreameLawnMowerAuthError,
    DreameLawnMowerClient,
    DreameLawnMowerConnectionError,
    DreameLawnMowerDescriptor,
    DreameLawnMowerError,
    DreameLawnMowerSnapshot,
    DreameLawnMowerTwoFactorRequiredError,
)

__all__ = [
    "DreameLawnMowerAuthError",
    "DreameLawnMowerClient",
    "DreameLawnMowerConnectionError",
    "DreameLawnMowerDescriptor",
    "DreameLawnMowerError",
    "DreameLawnMowerSnapshot",
    "DreameLawnMowerTwoFactorRequiredError",
]

