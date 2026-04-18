"""Regression checks for shared manual-control safety decisions."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.dreame_lawn_mower.manual_control import (
    remote_control_block_reason,
    remote_control_state_safe,
)


def _snapshot(**overrides: object) -> SimpleNamespace:
    values = {
        "activity": "docked",
        "battery_level": 80,
        "mowing": False,
        "raw_attributes": {},
        "returning": False,
        "state": "charging",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_remote_control_safety_blocks_mapping() -> None:
    snapshot = _snapshot(raw_attributes={"mapping": True})

    assert remote_control_state_safe(snapshot) is False
    assert remote_control_block_reason(snapshot) == (
        "Remote control is blocked while mapping."
    )


def test_remote_control_safety_blocks_low_battery() -> None:
    snapshot = _snapshot(battery_level=19)

    assert remote_control_state_safe(snapshot) is False
    assert remote_control_block_reason(snapshot) == (
        "Remote control is blocked while battery is low."
    )


def test_remote_control_safety_allows_unknown_battery_level() -> None:
    snapshot = _snapshot(battery_level=None)

    assert remote_control_state_safe(snapshot) is True
    assert remote_control_block_reason(snapshot) is None


def test_remote_control_safety_allows_active_remote_control_session() -> None:
    snapshot = _snapshot(
        activity="mowing",
        mowing=True,
        raw_attributes={"running": True},
        state="remote_control",
    )

    assert remote_control_state_safe(snapshot) is True
    assert remote_control_block_reason(snapshot) is None


def test_remote_control_safety_explains_missing_state() -> None:
    assert remote_control_state_safe(None) is False
    assert remote_control_block_reason(None) == "Mower state is not available yet."
