"""Regression checks for the read-only map-source probe CLI helpers."""

from __future__ import annotations

from examples.app_map_probe import _build_parser as _build_app_map_parser
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


def test_app_map_probe_parser_supports_payload_opt_in() -> None:
    args = _build_app_map_parser().parse_args(
        [
            "--chunk-size",
            "400",
            "--include-payload",
            "--include-object-urls",
            "--probe-object-downloads",
            "--object-download-timeout",
            "3.5",
            "--object-download-user-agent",
            "test-agent",
            "--skip-objects",
            "--render-dir",
            "rendered-maps",
            "--device-index",
            "1",
            "--out",
            "app-map-current.json",
        ]
    )

    assert args.chunk_size == 400
    assert args.include_payload is True
    assert args.include_object_urls is True
    assert args.probe_object_downloads is True
    assert args.object_download_timeout == 3.5
    assert args.object_download_user_agent == "test-agent"
    assert args.skip_objects is True
    assert str(args.render_dir) == "rendered-maps"
    assert args.device_index == 1
    assert str(args.out) == "app-map-current.json"
