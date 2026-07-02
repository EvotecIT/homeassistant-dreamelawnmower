"""Tests for the camera stream probe output helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerCameraStreamRuntimeInputs,
)


def _load_probe_module() -> ModuleType:
    path = Path("examples/camera_stream_handshake_probe.py")
    spec = importlib.util.spec_from_file_location("camera_stream_handshake_probe", path)
    if spec is None or spec.loader is None:
        raise AssertionError("Could not load camera stream probe module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_inputs_summary_redacts_stable_identifiers() -> None:
    module = _load_probe_module()
    inputs = DreameLawnMowerCameraStreamRuntimeInputs(
        source="dreame_third_video_tx",
        did="device-id-1",
        channel_id="product-1/device-name-1",
        product_id="product-1",
        device_name="device-name-1",
        p2p_info="p2p-info-1",
        secret_key="secret-key-1",
    )

    summary = module._safe_runtime_inputs_summary(inputs)

    for key in ("did", "channel_id", "product_id", "device_name", "xp2p_id"):
        assert key not in summary
        assert summary[f"{key}_present"] is True
    assert "p2p_info" not in summary
    assert summary["p2p_info_present"] is True
    assert "secret_key" not in summary
    assert summary["secret_key_present"] is True
    assert summary["ready"] is True


def test_xp2p_request_summary_redacts_computed_stream_target() -> None:
    module = _load_probe_module()
    inputs = DreameLawnMowerCameraStreamRuntimeInputs(
        source="dreame_third_video_tx",
        did="device-id-1",
        channel_id="channel-1",
        product_id="product-1",
        device_name="device-name-1",
        p2p_info="p2p-info-1",
    )

    summary = module._safe_xp2p_request_summary(inputs)

    assert summary["available"] is True
    assert summary["ready"] is True
    for key in ("service_id", "product_id", "device_name", "p2p_info", "flv_path"):
        assert key not in summary
        assert summary[f"{key}_present"] is True
