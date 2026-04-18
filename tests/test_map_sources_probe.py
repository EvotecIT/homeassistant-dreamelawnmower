"""Regression checks for the read-only map-source probe CLI helpers."""

from __future__ import annotations

from examples.map_sources_probe import _build_parser


def test_map_sources_probe_parser_supports_output_and_device_index() -> None:
    args = _build_parser().parse_args(
        [
            "--timeout",
            "8",
            "--interval",
            "0.25",
            "--device-index",
            "1",
            "--out",
            "map-sources-current.json",
        ]
    )

    assert args.timeout == 8
    assert args.interval == 0.25
    assert args.device_index == 1
    assert str(args.out) == "map-sources-current.json"
