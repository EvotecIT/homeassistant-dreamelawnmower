"""Regression checks for live probe safety gates."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from examples.field_trip_probe import (
    _raise_if_unsafe_execute as raise_if_unsafe_field_trip,
)
from examples.remote_control_probe import _support_probe_payload
from examples.remote_control_smoke import (
    _raise_if_unsafe_execute as raise_if_unsafe_remote_control,
)
from examples.remote_control_smoke import (
    _snapshot_summary as remote_control_snapshot_summary,
)


def test_remote_control_smoke_blocks_low_battery() -> None:
    with pytest.raises(RuntimeError, match="battery is low"):
        raise_if_unsafe_remote_control(
            SimpleNamespace(
                activity="docked",
                battery_level=19,
                mowing=False,
                returning=False,
                raw_attributes={},
            )
        )


def test_remote_control_smoke_allows_safe_docked_snapshot() -> None:
    raise_if_unsafe_remote_control(
        SimpleNamespace(
            activity="docked",
            battery_level=80,
            mowing=False,
            returning=False,
            raw_attributes={},
        )
    )


def test_remote_control_smoke_summary_includes_manual_drive_safety() -> None:
    summary = remote_control_snapshot_summary(
        SimpleNamespace(
            activity="docked",
            battery_level=80,
            charging=False,
            descriptor=SimpleNamespace(title="Garage Mower (A2)"),
            docked=True,
            error_code=-1,
            error_display=None,
            mowing=False,
            paused=False,
            raw_attributes={},
            realtime_property_count=0,
            returning=False,
            state="charging_completed",
        ),
        SimpleNamespace(as_dict=lambda: {"supported": True}),
    )

    assert summary["manual_drive_safe"] is True
    assert summary["manual_drive_block_reason"] is None


def test_remote_control_probe_payload_includes_support_and_safety() -> None:
    payload = _support_probe_payload(
        SimpleNamespace(
            activity="docked",
            battery_level=80,
            descriptor=SimpleNamespace(title="Garage Mower (A2)"),
            raw_attributes={},
            state="charging_completed",
        ),
        SimpleNamespace(
            as_dict=lambda: {
                "supported": True,
                "state_safe": True,
                "state_block_reason": None,
            }
        ),
    )

    assert payload == {
        "device": "Garage Mower (A2)",
        "state": "charging_completed",
        "activity": "docked",
        "battery_level": 80,
        "manual_drive_safe": True,
        "manual_drive_block_reason": None,
        "remote_control_support": {
            "supported": True,
            "state_safe": True,
            "state_block_reason": None,
        },
    }


def test_field_trip_blocks_mapping_snapshot() -> None:
    with pytest.raises(RuntimeError, match="mapping"):
        raise_if_unsafe_field_trip(
            {
                "snapshot": {
                    "activity": "docked",
                    "battery_level": 80,
                    "mowing": False,
                    "returning": False,
                    "raw_state_signals": {"mapping": True},
                }
            }
        )


def test_field_trip_prefers_operation_snapshot_manual_drive_guard() -> None:
    with pytest.raises(RuntimeError, match="battery is low"):
        raise_if_unsafe_field_trip(
            {
                "snapshot": {
                    "activity": "docked",
                    "battery_level": 80,
                    "manual_drive_safe": False,
                    "manual_drive_block_reason": (
                        "Remote control is blocked while battery is low."
                    ),
                    "mowing": False,
                    "returning": False,
                    "raw_state_signals": {},
                }
            }
        )


def test_field_trip_allows_unknown_battery_level() -> None:
    raise_if_unsafe_field_trip(
        {
            "snapshot": {
                "activity": "docked",
                "battery_level": None,
                "mowing": False,
                "returning": False,
                "raw_state_signals": {},
            }
        }
    )
