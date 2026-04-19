"""Small APK string research helpers for Dreamehome protocol work."""

from __future__ import annotations

import re
import shutil
import subprocess
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

DEFAULT_DECOMPILED_SOURCE_SUFFIXES = (
    ".java",
    ".kt",
    ".kts",
    ".smali",
    ".xml",
    ".json",
    ".dart",
    ".txt",
)

DEFAULT_DREAMEHOME_ASSET_TERMS = (
    "mower",
    "g2408",
    "10425",
    "1423",
    "map",
    "MAP",
    "M_PATH",
    "current_map",
    "object_name",
    "obj_name",
    "boundary",
    "polygon",
    "area",
    "zone",
    "room",
    "point",
    "path",
    "route",
    "sendAction",
    "sendCommand",
    "get_file",
    "file_url",
    "device_status",
    "iotKeyValue",
)

DEFAULT_DREAMEHOME_ASSET_SUFFIXES = (
    ".json",
    ".xml",
    ".txt",
    ".js",
    ".jx",
    ".html",
    ".dart",
    ".properties",
    ".conf",
)

DEFAULT_JADX_EXECUTABLES = ("jadx", "jadx.bat", "jadx.cmd")

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


def analyze_decompiled_sources(
    source_dir: str | Path,
    *,
    terms: Sequence[str] | None = None,
    suffixes: Sequence[str] = DEFAULT_DECOMPILED_SOURCE_SUFFIXES,
    max_files_per_term: int = 50,
    max_snippets_per_term: int = 40,
    max_endpoint_snippets: int = 120,
    max_file_size: int = 2_000_000,
    max_line_length: int = 500,
    context_chars: int = 160,
) -> dict[str, Any]:
    """Return a compact source index for a jadx/decompiled Dreamehome tree."""

    path = Path(source_dir)
    normalized_terms = tuple(terms or DEFAULT_APK_RESEARCH_TERMS)
    normalized_suffixes = tuple(suffix.casefold() for suffix in suffixes)
    term_file_hits: dict[str, list[str]] = {term: [] for term in normalized_terms}
    term_snippets: dict[str, list[dict[str, Any]]] = {
        term: [] for term in normalized_terms
    }
    endpoint_snippets: list[dict[str, Any]] = []
    candidate_files: list[str] = []
    scanned_files = 0

    for file_path in sorted(path.rglob("*")):
        if not file_path.is_file():
            continue
        if file_path.suffix.casefold() not in normalized_suffixes:
            continue
        try:
            stat = file_path.stat()
        except OSError:
            continue
        if stat.st_size > max_file_size:
            continue

        relative_name = _relative_name(file_path, path)
        scanned_files += 1
        if _is_candidate_source_file(relative_name):
            candidate_files.append(relative_name)

        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        lower_text = text.casefold()
        for term in normalized_terms:
            if term.casefold() not in lower_text:
                continue
            _append_limited(term_file_hits[term], relative_name, max_files_per_term)
            _append_snippets(
                term_snippets[term],
                text,
                term,
                relative_name,
                max_snippets_per_term,
                max_line_length=max_line_length,
                context_chars=context_chars,
            )

        for line_number, line in enumerate(text.splitlines(), start=1):
            snippet = line.strip()
            if not snippet or len(snippet) > max_line_length:
                continue
            if _matches_any(snippet.casefold(), DEFAULT_APK_ENDPOINT_PATTERNS):
                _append_limited_dict(
                    endpoint_snippets,
                    {
                        "file": relative_name,
                        "line": line_number,
                        "text": snippet,
                    },
                    max_endpoint_snippets,
                )

    return {
        "source": {
            "path": str(path),
            "scanned_files": scanned_files,
        },
        "limits": {
            "max_file_size": max_file_size,
            "max_line_length": max_line_length,
            "context_chars": context_chars,
        },
        "suffixes": list(normalized_suffixes),
        "terms": list(normalized_terms),
        "candidate_files": sorted(candidate_files)[:300],
        "term_file_hits": {
            term: hits for term, hits in term_file_hits.items() if hits
        },
        "term_snippets": {
            term: hits for term, hits in term_snippets.items() if hits
        },
        "endpoint_snippets": endpoint_snippets,
    }


def analyze_dreamehome_assets(
    source_dir: str | Path,
    *,
    terms: Sequence[str] | None = None,
    suffixes: Sequence[str] = DEFAULT_DREAMEHOME_ASSET_SUFFIXES,
    max_files_per_term: int = 40,
    max_snippets_per_term: int = 30,
    max_file_size: int = 2_000_000,
    max_line_length: int = 500,
    context_chars: int = 160,
) -> dict[str, Any]:
    """Scan extracted Dreamehome assets for compact protocol evidence.

    This is intentionally aimed at Flutter/plugin assets rather than full
    decompiled source trees. It ignores large binary blobs and returns only
    short snippets so APK research can be shared without noisy vendor payloads.
    """

    path = Path(source_dir)
    normalized_terms = tuple(terms or DEFAULT_DREAMEHOME_ASSET_TERMS)
    normalized_suffixes = tuple(suffix.casefold() for suffix in suffixes)
    term_file_hits: dict[str, list[str]] = {term: [] for term in normalized_terms}
    term_snippets: dict[str, list[dict[str, Any]]] = {
        term: [] for term in normalized_terms
    }
    candidate_files: list[str] = []
    scanned_files = 0
    skipped_large_files = 0

    for file_path in sorted(path.rglob("*")):
        if not file_path.is_file():
            continue
        if normalized_suffixes and file_path.suffix.casefold() not in normalized_suffixes:
            continue
        try:
            stat = file_path.stat()
        except OSError:
            continue
        if stat.st_size > max_file_size:
            skipped_large_files += 1
            continue

        relative_name = _relative_name(file_path, path)
        scanned_files += 1
        if _is_candidate_source_file(relative_name):
            candidate_files.append(relative_name)

        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        lower_text = text.casefold()
        for term in normalized_terms:
            if term.casefold() not in lower_text:
                continue
            _append_limited(term_file_hits[term], relative_name, max_files_per_term)
            _append_snippets(
                term_snippets[term],
                text,
                term,
                relative_name,
                max_snippets_per_term,
                max_line_length=max_line_length,
                context_chars=context_chars,
            )

    return {
        "source": {
            "path": str(path),
            "scanned_files": scanned_files,
            "skipped_large_files": skipped_large_files,
        },
        "limits": {
            "max_file_size": max_file_size,
            "max_line_length": max_line_length,
            "context_chars": context_chars,
        },
        "suffixes": list(normalized_suffixes),
        "terms": list(normalized_terms),
        "candidate_files": sorted(candidate_files)[:300],
        "term_file_hits": {
            term: hits for term, hits in term_file_hits.items() if hits
        },
        "term_snippets": {
            term: hits for term, hits in term_snippets.items() if hits
        },
        "missing_terms": [
            term for term, hits in term_file_hits.items() if not hits
        ],
    }


def find_jadx_executable(
    *,
    candidates: Sequence[str] = DEFAULT_JADX_EXECUTABLES,
) -> str | None:
    """Return the first jadx executable found on PATH."""

    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def build_jadx_command(
    apk_path: str | Path,
    output_dir: str | Path,
    *,
    jadx_path: str | Path | None = None,
) -> list[str]:
    """Build a jadx command for decompiling a Dreamehome APK."""

    executable = str(jadx_path or "jadx")
    return [executable, "-d", str(Path(output_dir)), str(Path(apk_path))]


def run_jadx_decompile(
    apk_path: str | Path,
    output_dir: str | Path,
    *,
    jadx_path: str | Path | None = None,
    overwrite: bool = False,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Run jadx and return a JSON-safe process summary."""

    executable = str(jadx_path) if jadx_path is not None else find_jadx_executable()
    if not executable:
        return {
            "ok": False,
            "error": "jadx_not_found",
            "message": "Install jadx or pass --jadx-path to decompile APK sources.",
        }

    apk = Path(apk_path)
    output = Path(output_dir)
    if output.exists() and any(output.iterdir()) and not overwrite:
        return {
            "ok": False,
            "error": "output_exists",
            "message": "Output directory is not empty. Pass --overwrite to reuse it.",
            "output_dir": str(output),
        }
    output.mkdir(parents=True, exist_ok=True)

    command = build_jadx_command(apk, output, jadx_path=executable)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )
    except OSError as err:
        return {
            "ok": False,
            "error": "jadx_failed_to_start",
            "message": str(err),
            "command": command,
        }
    except subprocess.TimeoutExpired as err:
        return {
            "ok": False,
            "error": "jadx_timeout",
            "message": str(err),
            "command": command,
        }

    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "command": command,
        "output_dir": str(output),
        "stdout_tail": _tail_text(completed.stdout),
        "stderr_tail": _tail_text(completed.stderr),
    }


def _append_limited(values: list[str], value: str, limit: int) -> None:
    if value in values or len(values) >= limit:
        return
    values.append(value)


def _append_limited_dict(
    values: list[dict[str, Any]],
    value: dict[str, Any],
    limit: int,
) -> None:
    if value in values or len(values) >= limit:
        return
    values.append(value)


def _append_snippets(
    values: list[dict[str, Any]],
    text: str,
    term: str,
    relative_name: str,
    limit: int,
    *,
    max_line_length: int,
    context_chars: int,
) -> None:
    if len(values) >= limit:
        return

    lower_text = text.casefold()
    lower_term = term.casefold()
    offset = 0
    while len(values) < limit:
        index = lower_text.find(lower_term, offset)
        if index == -1:
            break
        line_number = text.count("\n", 0, index) + 1
        line_start = text.rfind("\n", 0, index) + 1
        line_end = text.find("\n", index)
        if line_end == -1:
            line_end = len(text)

        line = text[line_start:line_end].strip()
        snippet = (
            _context_snippet(text, index, len(term), context_chars=context_chars)
            if not line or len(line) > max_line_length
            else line
        )
        if len(snippet) <= max_line_length:
            _append_limited_dict(
                values,
                {
                    "file": relative_name,
                    "line": line_number,
                    "text": snippet,
                },
                limit,
            )
        offset = index + len(lower_term)


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


def _is_candidate_source_file(name: str) -> bool:
    lowered = name.casefold()
    return any(
        marker in lowered
        for marker in (
            "mower",
            "map",
            "camera",
            "video",
            "stream",
            "photo",
            "device",
            "protocol",
        )
    )


def _context_snippet(text: str, index: int, term_length: int, *, context_chars: int) -> str:
    start = max(0, index - context_chars)
    end = min(len(text), index + term_length + context_chars)
    snippet = text[start:end].replace("\r", " ").replace("\n", " ").strip()
    if start > 0:
        snippet = f"...{snippet}"
    if end < len(text):
        snippet = f"{snippet}..."
    return snippet


def _relative_name(file_path: Path, root: Path) -> str:
    try:
        return file_path.relative_to(root).as_posix()
    except ValueError:
        return file_path.as_posix()


def _tail_text(value: str, *, max_chars: int = 4000) -> str:
    if len(value) <= max_chars:
        return value
    return value[-max_chars:]
