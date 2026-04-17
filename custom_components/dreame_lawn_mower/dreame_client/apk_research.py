"""Small APK string research helpers for Dreamehome protocol work."""

from __future__ import annotations

import re
import zipfile
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

DEFAULT_APK_RESEARCH_TERMS = (
    "device/info",
    "device/listV2",
    "iotstatus/props",
    "queryDevicePermit",
    "sendAction",
    "sendCommand",
    "STREAM",
    "stream",
    "session",
    "operType",
    "operation",
    "monitor",
    "intercom",
    "recordVideo",
    "takePhoto",
    "camera",
    "photo",
    "mower",
    "map",
)

DEFAULT_APK_ENDPOINT_PATTERNS = (
    "/dreame-",
    "device/info",
    "device/list",
    "iotstatus/props",
    "queryDevicePermit",
    "sendAction",
    "sendCommand",
)

PRINTABLE_RE = re.compile(rb"[ -~]{4,}")


def analyze_dreamehome_apk(
    apk_path: str | Path,
    *,
    terms: Sequence[str] | None = None,
    max_files_per_term: int = 30,
    max_strings_per_term: int = 40,
    max_endpoint_strings: int = 80,
    max_entry_size: int = 25_000_000,
    max_string_length: int = 500,
) -> dict[str, Any]:
    """Return a compact, JSON-safe string index for a Dreamehome APK.

    This is intentionally lighter than a real decompiler. It gives us a
    repeatable first pass over dex/assets/resources so protocol hypotheses can
    be backed by concrete package strings before live device testing.
    """

    path = Path(apk_path)
    normalized_terms = tuple(terms or DEFAULT_APK_RESEARCH_TERMS)
    term_file_hits: dict[str, list[str]] = {term: [] for term in normalized_terms}
    term_string_hits: dict[str, list[str]] = {term: [] for term in normalized_terms}
    endpoint_strings: list[str] = []
    dex_files: list[str] = []
    candidate_assets: list[str] = []

    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        for entry in entries:
            name = entry.filename
            if name.endswith(".dex"):
                dex_files.append(name)
            if _is_candidate_asset(name):
                candidate_assets.append(name)
            if entry.file_size > max_entry_size:
                continue

            try:
                data = archive.read(name)
            except (KeyError, RuntimeError, zipfile.BadZipFile):
                continue

            lower_data = data.lower()
            for term in normalized_terms:
                if term.lower().encode() in lower_data:
                    _append_limited(term_file_hits[term], name, max_files_per_term)

            strings = _printable_strings(data, max_string_length=max_string_length)
            for value in strings:
                lower_value = value.casefold()
                if _matches_any(lower_value, DEFAULT_APK_ENDPOINT_PATTERNS):
                    _append_limited(endpoint_strings, value, max_endpoint_strings)
                for term in normalized_terms:
                    if term.casefold() in lower_value:
                        _append_limited(
                            term_string_hits[term],
                            value,
                            max_strings_per_term,
                        )

    return {
        "apk": {
            "path": str(path),
            "filename": path.name,
            "size_bytes": path.stat().st_size,
        },
        "limits": {
            "max_entry_size": max_entry_size,
            "max_string_length": max_string_length,
        },
        "entry_count": len(entries),
        "dex_files": sorted(dex_files),
        "candidate_assets": sorted(candidate_assets)[:200],
        "terms": list(normalized_terms),
        "term_file_hits": {
            term: hits for term, hits in term_file_hits.items() if hits
        },
        "term_string_hits": {
            term: hits for term, hits in term_string_hits.items() if hits
        },
        "endpoint_strings": endpoint_strings,
    }


def _append_limited(values: list[str], value: str, limit: int) -> None:
    if value in values or len(values) >= limit:
        return
    values.append(value)


def _printable_strings(data: bytes, *, max_string_length: int) -> Iterable[str]:
    for match in PRINTABLE_RE.finditer(data):
        value = match.group(0).decode("utf-8", "ignore").strip()
        if value and len(value) <= max_string_length:
            yield value


def _matches_any(value: str, needles: Sequence[str]) -> bool:
    return any(needle.casefold() in value for needle in needles)


def _is_candidate_asset(name: str) -> bool:
    lowered = name.casefold()
    if not lowered.endswith((".json", ".xml", ".txt", ".js", ".dart")):
        return False
    return any(
        marker in lowered
        for marker in (
            "mower",
            "map",
            "camera",
            "video",
            "protocol",
            "device",
            "stream",
        )
    )
