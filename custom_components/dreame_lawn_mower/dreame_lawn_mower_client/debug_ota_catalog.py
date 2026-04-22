"""Helpers for Dreame's debug/manual OTA catalog endpoint."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

DEBUG_OTA_LIST_URL = "https://ota.tsingting.tech/api/version/{model_name}/?name={model_name}"
DEBUG_OTA_TRACKS = ("BUILD", "FEATURE", "PREBUILD")
_CHANGELOG_FIELDS = (
    "changelog",
    "description",
    "note",
    "releaseNote",
    "releaseNotes",
)


def build_debug_ota_catalog_url(model_name: str) -> str:
    """Return the public debug OTA catalog URL for a short model name."""
    return DEBUG_OTA_LIST_URL.format(model_name=model_name)


def normalize_debug_ota_catalog_payload(
    payload: Mapping[str, Any],
    *,
    model_name: str | None = None,
    current_version: str | None = None,
    include_raw: bool = False,
) -> dict[str, Any]:
    """Summarize the public debug OTA catalog without treating it as approved OTA."""
    result: dict[str, Any] = {
        "source": "debug_ota_catalog",
        "available": False,
        "model_name": model_name,
        "current_version": current_version,
        "current_version_present": None,
        "changelog_available": False,
        "warnings": [
            "debug_catalog_unverified",
            "debug_catalog_not_device_approved",
        ],
        "tracks": {},
        "latest_release_candidates": [],
        "errors": [],
    }
    if include_raw:
        result["raw"] = payload

    data = payload.get("data")
    if not isinstance(data, Mapping):
        result["errors"].append(
            {"stage": "parse", "error": "Debug OTA catalog payload has no data map."}
        )
        return result

    current_version_present = False if current_version else None
    latest_release_candidates: list[dict[str, Any]] = []
    changelog_available = False
    available = False

    for track in DEBUG_OTA_TRACKS:
        entries = _normalize_track_entries(data.get(track))
        available = available or bool(entries)
        release_entries = [entry for entry in entries if not entry["debug_build"]]
        latest_release = release_entries[-1] if release_entries else None
        track_current_present = None
        if current_version:
            track_current_present = any(
                entry.get("version") == current_version for entry in release_entries
            )
            current_version_present = bool(
                current_version_present or track_current_present
            )

        track_changelog_available = any(
            _entry_changelog(entry) for entry in release_entries
        )
        changelog_available = changelog_available or track_changelog_available

        track_summary = {
            "entry_count": len(entries),
            "release_entry_count": len(release_entries),
            "current_version_present": track_current_present,
            "changelog_available": track_changelog_available,
            "recent_release_versions": [
                entry["version"]
                for entry in release_entries[-5:]
                if entry.get("version")
            ],
        }
        if latest_release:
            track_summary.update(_latest_release_summary(latest_release))
            latest_release_candidates.append(
                {
                    "track": track,
                    **_latest_release_summary(latest_release),
                }
            )
        result["tracks"][track] = track_summary

    if not changelog_available:
        result["warnings"].append("debug_catalog_has_no_changelog")

    result["available"] = available
    result["current_version_present"] = current_version_present
    result["changelog_available"] = changelog_available
    result["latest_release_candidates"] = latest_release_candidates
    return result


def _normalize_track_entries(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            continue
        version = item.get("version")
        normalized.append(
            {
                "index": index,
                "id": _positive_int(item.get("id")),
                "version": str(version) if version is not None else None,
                "sha256sum": _as_optional_str(item.get("sha256sum")),
                "url_endpoint": _as_optional_str(item.get("url_endpoint")),
                "url": _as_optional_str(item.get("url")),
                "download_url": _download_url(item),
                "debug_build": _is_debug_build(item),
                "changelog": _entry_changelog(item),
            }
        )

    normalized.sort(
        key=lambda entry: (entry["id"] is None, entry["id"], entry["index"])
    )
    return normalized


def _latest_release_summary(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "latest_release_version": entry.get("version"),
        "latest_release_id": entry.get("id"),
        "latest_release_sha256": entry.get("sha256sum"),
        "latest_release_path": entry.get("url"),
        "latest_release_download_url": entry.get("download_url"),
    }


def _download_url(entry: Mapping[str, Any]) -> str | None:
    endpoint = _as_optional_str(entry.get("url_endpoint"))
    path = _as_optional_str(entry.get("url"))
    if endpoint and path:
        return f"{endpoint.rstrip('/')}/{path.lstrip('/')}"
    return endpoint or path


def _entry_changelog(entry: Mapping[str, Any]) -> str | None:
    for field in _CHANGELOG_FIELDS:
        value = _as_optional_str(entry.get(field))
        if value:
            return value
    return None


def _is_debug_build(entry: Mapping[str, Any]) -> bool:
    version = _as_optional_str(entry.get("version")) or ""
    if version.endswith("_debug"):
        return True

    url = _as_optional_str(entry.get("url")) or ""
    return "/debug-" in url.lower() or "_debug" in url.lower()


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None
