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
        output = [
            {"kind": payload.kind, "payload": payload.payload}
            for payload in payloads
        ]
    else:
        output = payloads[0].payload

    rendered = json.dumps(output, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.out:
        args.out.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
