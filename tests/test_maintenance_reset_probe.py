"""Regression checks for the maintenance reset probe CLI helper."""

from __future__ import annotations

from examples.maintenance_reset_probe import _build_parser


def test_maintenance_reset_probe_parser_defaults_to_dry_run() -> None:
    parser = _build_parser()

    args = parser.parse_args(["--item", "blade", "--item", "robot"])

    assert args.item == ["blade", "robot"]
    assert args.execute is False
    assert args.confirm_maintenance_reset is False
    assert args.device_index == 0
