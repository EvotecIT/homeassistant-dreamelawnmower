"""Regression checks for the standalone mower Python package."""

from dreame_lawn_mower_client import DreameLawnMowerClient
from dreame_lawn_mower_client.client import (
    DreameLawnMowerClient as ClientFromModule,
)


def test_public_package_exports_client() -> None:
    assert DreameLawnMowerClient is ClientFromModule

