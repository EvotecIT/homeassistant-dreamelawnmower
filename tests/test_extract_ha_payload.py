"""Tests for Home Assistant log payload extraction helpers."""

from __future__ import annotations

import json

import pytest

from examples.extract_ha_payload import extract_first_payload, extract_payloads


def test_extract_first_payload_from_debug_log_line() -> None:
    payload = {
        "captured_at": "2026-04-16T21:28:07.783516+00:00",
        "snapshot": {"state": "charging_completed"},
    }
    log_line = (
        "Captured Dreame lawn mower debug snapshot for Dreame A2 (A2): "
        f"{json.dumps(payload)}"
    )

    assert extract_first_payload(log_line) == payload


def test_extract_payloads_keeps_log_kind_for_multiple_entries() -> None:
    debug_payload = {"snapshot": {"state": "docked"}}
    map_payload = {"map": {"source": "placeholder"}}
    log_text = "\n".join(
        [
            "noise before",
            "Captured Dreame lawn mower debug snapshot for Dreame A2 (A2): "
            f"{json.dumps(debug_payload)}",
            "another logger line",
            "Captured Dreame lawn mower map probe for Dreame A2 (A2): "
            f"{json.dumps(map_payload)}",
            "noise after",
        ]
    )

    payloads = extract_payloads(log_text)

    assert [payload.kind for payload in payloads] == ["debug_snapshot", "map_probe"]
    assert [payload.payload for payload in payloads] == [debug_payload, map_payload]


def test_extract_payloads_accepts_plain_json_diagnostics() -> None:
    payload = {"data": {"snapshot": {"battery_level": 100}}}

    assert extract_payloads(json.dumps(payload))[0].payload == payload


def test_extract_payloads_can_filter_by_kind() -> None:
    debug_payload = {"snapshot": {"state": "docked"}}
    map_payload = {"map": {"source": "placeholder"}}
    log_text = "\n".join(
        [
            "Captured Dreame lawn mower debug snapshot for Dreame A2 (A2): "
            f"{json.dumps(debug_payload)}",
            "Captured Dreame lawn mower map probe for Dreame A2 (A2): "
            f"{json.dumps(map_payload)}",
        ]
    )

    payloads = extract_payloads(log_text, kind="map_probe")

    assert len(payloads) == 1
    assert payloads[0].kind == "map_probe"
    assert payloads[0].payload == map_payload


def test_extract_first_payload_raises_when_no_payload_is_found() -> None:
    with pytest.raises(ValueError, match="No Dreame lawn mower JSON payload found"):
        extract_first_payload("plain traceback without mower payload")
