"""Extract JSON payloads from Home Assistant Dreame lawn mower log lines."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOG_MARKERS = {
    "debug_snapshot": "Captured Dreame lawn mower debug snapshot",
    "map_probe": "Captured Dreame lawn mower map probe",
}


@dataclass(frozen=True)
class ExtractedPayload:
    """One JSON payload extracted from a Home Assistant log export."""

    kind: str | None
    payload: dict[str, Any]


def summarize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a compact triage summary for a mower debug or probe payload."""

    payload = _payload_body(payload)
    snapshot = _as_mapping(payload.get("snapshot"))
    descriptor = _as_mapping(payload.get("descriptor") or snapshot.get("descriptor"))
    device = _as_mapping(payload.get("device"))
    reconciliation = _as_mapping(payload.get("state_reconciliation"))
    error = _as_mapping(reconciliation.get("error"))
    flags = _as_mapping(reconciliation.get("flags"))
    cloud_property_summary = _as_mapping(payload.get("cloud_property_summary"))
    map_payload = _as_mapping(payload.get("map"))

    summary: dict[str, Any] = {
        "captured_at": payload.get("captured_at"),
        "name": descriptor.get("name") or device.get("name"),
        "model": descriptor.get("display_model") or descriptor.get("model"),
        "activity": reconciliation.get("activity") or snapshot.get("activity"),
        "state": reconciliation.get("state") or snapshot.get("state"),
        "state_name": reconciliation.get("state_name") or snapshot.get("state_name"),
        "raw_mower_state": reconciliation.get("raw_mower_state")
        or _as_mapping(snapshot.get("raw_attributes")).get("mower_state"),
        "battery_level": snapshot.get("battery_level"),
        "error": {
            "active": error.get("active"),
            "code": error.get("code", snapshot.get("error_code")),
            "name": error.get("name", snapshot.get("error_name")),
            "display": error.get("display", snapshot.get("error_display")),
            "raw_attribute": error.get("raw_attribute"),
        },
        "flags": {
            "charging": flags.get("charging", snapshot.get("charging")),
            "docked": flags.get("docked", snapshot.get("docked")),
            "mowing": flags.get("mowing", snapshot.get("mowing")),
            "paused": flags.get("paused", snapshot.get("paused")),
            "returning": flags.get("returning", snapshot.get("returning")),
            "started": flags.get("started", snapshot.get("started")),
        },
        "warnings": reconciliation.get("warnings", []),
        "unknown_property_count": device.get(
            "unknown_property_count",
            snapshot.get("unknown_property_count"),
        ),
        "realtime_property_count": device.get(
            "realtime_property_count",
            snapshot.get("realtime_property_count"),
        ),
        "last_realtime_method": snapshot.get("last_realtime_method"),
    }

    if cloud_property_summary:
        summary["cloud_property_summary"] = {
            "requested_key_count": cloud_property_summary.get("requested_key_count"),
            "non_empty_keys": cloud_property_summary.get("non_empty_keys", []),
            "decoded_labels": cloud_property_summary.get("decoded_labels", {}),
            "blob_keys": cloud_property_summary.get("blob_keys", {}),
        }

    if map_payload:
        summary["map"] = {
            "source": map_payload.get("source"),
            "available": map_payload.get("available"),
            "has_image": map_payload.get("has_image"),
            "error": map_payload.get("error"),
        }

    return _drop_empty(summary)


def extract_payloads(text: str, *, kind: str | None = None) -> list[ExtractedPayload]:
    """Extract all mower JSON payloads from text.

    Home Assistant logs prefix the JSON with human-readable text, while downloaded
    diagnostics are already plain JSON. This helper accepts both forms so captures
    can be turned into test fixtures without manual trimming.
    """

    if kind is not None and kind not in LOG_MARKERS:
        raise ValueError(f"Unsupported payload kind: {kind}")

    stripped = text.strip()
    if stripped.startswith("{"):
        payload = json.loads(stripped)
        if not isinstance(payload, dict):
            raise ValueError("Expected a JSON object payload")
        return [ExtractedPayload(kind=kind, payload=payload)]

    decoder = json.JSONDecoder()
    payloads: list[ExtractedPayload] = []
    seen_offsets: set[int] = set()

    for detected_kind, offset in _candidate_json_offsets(text, kind=kind):
        if offset in seen_offsets:
            continue
        seen_offsets.add(offset)

        try:
            payload, _ = decoder.raw_decode(text[offset:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            payloads.append(ExtractedPayload(kind=detected_kind, payload=payload))

    return payloads


def extract_first_payload(text: str, *, kind: str | None = None) -> dict[str, Any]:
    """Extract the first mower payload from text."""

    payloads = extract_payloads(text, kind=kind)
    if not payloads:
        raise ValueError("No Dreame lawn mower JSON payload found")
    return payloads[0].payload


def _as_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _payload_body(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _drop_empty(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: cleaned
            for key, item in value.items()
            if (cleaned := _drop_empty(item)) not in (None, {}, [])
        }
    if isinstance(value, list):
        return [_drop_empty(item) for item in value]
    return value


def _candidate_json_offsets(
    text: str, *, kind: str | None
) -> list[tuple[str | None, int]]:
    markers = (
        {kind: LOG_MARKERS[kind]}
        if kind is not None
        else LOG_MARKERS
    )
    offsets: list[tuple[str | None, int]] = []

    for marker_kind, marker in markers.items():
        search_from = 0
        while True:
            marker_index = text.find(marker, search_from)
            if marker_index == -1:
                break

            json_index = text.find("{", marker_index)
            if json_index != -1:
                offsets.append((marker_kind, json_index))
            search_from = marker_index + len(marker)

    if offsets:
        return sorted(offsets, key=lambda item: item[1])

    first_json = text.find("{")
    if first_json != -1:
        offsets.append((kind, first_json))
    return offsets


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Extract Dreame lawn mower JSON from Home Assistant log lines or "
            "downloaded diagnostics."
        )
    )
    parser.add_argument("input", type=Path, help="Log or JSON file to parse")
    parser.add_argument(
        "--kind",
        choices=sorted(LOG_MARKERS),
        help="Only extract a specific log payload kind",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Return all matching payloads as a JSON array",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Write pretty JSON to this path instead of stdout",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print a compact triage summary instead of the full payload",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    text = args.input.read_text(encoding="utf-8", errors="replace")
    payloads = extract_payloads(text, kind=args.kind)
    if not payloads:
        raise SystemExit("No Dreame lawn mower JSON payload found")

    output: Any
    if args.all:
        output = []
        for payload in payloads:
            item = (
                summarize_payload(payload.payload)
                if args.summary
                else payload.payload
            )
            key = "summary" if args.summary else "payload"
            output.append({"kind": payload.kind, key: item})
    else:
        output = (
            summarize_payload(payloads[0].payload)
            if args.summary
            else payloads[0].payload
        )

    rendered = json.dumps(output, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.out:
        args.out.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
