"""Home Assistant test fixtures for the Dreame lawn mower component."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow Home Assistant to load this custom integration in component tests."""
    yield
