"""Regression checks for the batch device-data probe CLI helper."""

from __future__ import annotations

from examples.batch_device_data_probe import _build_parser


def test_batch_device_data_probe_parser_supports_raw_and_output() -> None:
    args = _build_parser().parse_args(
        [
            "--include-raw",
            "--map-index-hint",
            "1",
            "--device-index",
            "2",
            "--out",
            "batch-device-data.json",
        ]
    )

    assert args.include_raw is True
    assert args.map_index_hint == 1
    assert args.device_index == 2
    assert str(args.out) == "batch-device-data.json"
