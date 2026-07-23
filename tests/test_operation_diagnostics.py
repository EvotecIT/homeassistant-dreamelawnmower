"""Privacy and shape tests for reusable staged-operation diagnostics."""

from __future__ import annotations

import json

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    operation_diagnostics,
)

build_operation_stage_diagnostics = (
    operation_diagnostics.build_operation_stage_diagnostics
)
summarize_operation_response = operation_diagnostics.summarize_operation_response


def test_operation_summary_keeps_shape_codes_and_sanitized_messages() -> None:
    response = {
        "code": 403,
        "message": (
            "accessToken=secret-access-token denied for deviceName=Mower-123456 "
            "at https://video.example.test/live?token=another-secret"
        ),
        "data": {
            "accessToken": "secret-access-token",
            "deviceName": "Mower-123456",
            "p2pInfo": "secret-p2p-material",
            "features": ["video", "audio"],
        },
    }

    summary = summarize_operation_response(response)
    serialized = json.dumps(summary)

    assert summary["codes"] == [{"path": "$.code", "value": 403}]
    data_field = next(
        field for field in summary["shape"]["fields"] if field["name"] == "data"
    )
    p2p_field = next(
        field
        for field in data_field["shape"]["fields"]
        if field["name"] == "p2pInfo"
    )
    assert p2p_field["shape"] == {"type": "string"}
    assert "accessToken=**REDACTED**" in summary["messages"][0]["text"]
    assert "deviceName=**REDACTED**" in summary["messages"][0]["text"]
    assert "**REDACTED_URL**" in summary["messages"][0]["text"]
    assert "secret-access-token" not in serialized
    assert "Mower-123456" not in serialized
    assert "secret-p2p-material" not in serialized
    assert "another-secret" not in serialized


def test_operation_summary_bounds_dynamic_response_fields() -> None:
    response = {f"field_{index}": index for index in range(45)}

    summary = summarize_operation_response(response)

    assert summary["shape"]["field_count"] == 45
    assert len(summary["shape"]["fields"]) == 40
    assert summary["shape"]["truncated"] is True


def test_operation_stage_error_never_requires_a_response_payload() -> None:
    stage = build_operation_stage_diagnostics(
        "cloud_access_token",
        request_context={"did_present": True, "os": 1},
        error=RuntimeError(
            "Bearer secret-access-token failed for user@example.test 12345678"
        ),
        sensitive_values=("secret-access-token",),
    )

    assert "response" not in stage
    assert stage["request"] == {"did_present": True, "os": 1}
    assert stage["error"]["type"] == "RuntimeError"
    assert stage["error"]["message"] == (
        "Bearer **REDACTED** failed for **REDACTED_EMAIL** **REDACTED_ID**"
    )
