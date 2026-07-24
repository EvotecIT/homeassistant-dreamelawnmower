"""Regression checks for config-flow auth diagnostics."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from custom_components.dreame_lawn_mower.config_flow import (
    DreameLawnMowerOptionsFlow,
    auth_error_key,
)
from custom_components.dreame_lawn_mower.const import (
    CONF_MAP_ROTATIONS,
    CONF_SCAN_INTERVAL,
)


def test_auth_error_key_detects_unsupported_account_type() -> None:
    assert auth_error_key(Exception("Unsupported account type: mi")) == (
        "invalid_account_type"
    )


def test_auth_error_key_detects_region_failures() -> None:
    assert auth_error_key(Exception("invalid region selected")) == "invalid_region"
    assert auth_error_key(Exception("country is not available")) == "invalid_region"


def test_auth_error_key_detects_connection_failures() -> None:
    assert auth_error_key(Exception("network timeout while connecting")) == (
        "cannot_connect"
    )


def test_auth_error_key_keeps_generic_auth_failures_generic() -> None:
    assert auth_error_key(Exception("invalid username or password")) == "cannot_auth"


def test_options_flow_preserves_internal_per_map_rotations() -> None:
    flow = DreameLawnMowerOptionsFlow(
        SimpleNamespace(options={CONF_MAP_ROTATIONS: {"0": 90, "1": 270}})
    )

    result = asyncio.run(
        flow.async_step_init(
            {
                CONF_SCAN_INTERVAL: 45,
            }
        )
    )

    assert result["data"] == {
        CONF_SCAN_INTERVAL: 45,
        CONF_MAP_ROTATIONS: {"0": 90, "1": 270},
    }
