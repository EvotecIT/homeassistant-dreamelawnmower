"""Regression checks for Home Assistant service helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import voluptuous as vol
from homeassistant.exceptions import HomeAssistantError

from custom_components.dreame_lawn_mower.services import (
    REMOTE_CONTROL_STEP_SCHEMA,
    _guard_remote_control_step,
)


def _coordinator(snapshot: object) -> object:
    return SimpleNamespace(data=snapshot)


def _snapshot(**overrides: object) -> SimpleNamespace:
    values = {
        "activity": "docked",
        "mowing": False,
        "raw_attributes": {},
        "returning": False,
        "state": "charging",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_remote_control_step_schema_rejects_bool_values() -> None:
    with pytest.raises(vol.Invalid, match="rotation must be an integer"):
        REMOTE_CONTROL_STEP_SCHEMA(
            {
                "rotation": True,
                "velocity": 0,
            }
        )


def test_remote_control_step_schema_rejects_out_of_range_values() -> None:
    with pytest.raises(vol.Invalid, match="velocity must be between"):
        REMOTE_CONTROL_STEP_SCHEMA(
            {
                "rotation": 0,
                "velocity": 1001,
            }
        )


def test_remote_control_guard_blocks_active_mower() -> None:
    with pytest.raises(HomeAssistantError, match="mower is active"):
        _guard_remote_control_step(
            _coordinator(
                _snapshot(
                    activity="mowing",
                    mowing=True,
                    raw_attributes={"running": True},
                    state="mowing",
                )
            )
        )


def test_remote_control_guard_allows_existing_remote_control_session() -> None:
    _guard_remote_control_step(
        _coordinator(
            _snapshot(
                activity="mowing",
                mowing=True,
                raw_attributes={"running": True},
                state="remote_control",
            )
        )
    )


def test_remote_control_guard_blocks_mapping() -> None:
    with pytest.raises(HomeAssistantError, match="blocked while mapping"):
        _guard_remote_control_step(
            _coordinator(
                _snapshot(
                    raw_attributes={"mapping": True},
                )
            )
        )
