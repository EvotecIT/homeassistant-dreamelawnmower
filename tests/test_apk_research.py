"""Regression checks for APK research helpers."""

from __future__ import annotations

import zipfile

from dreame_lawn_mower_client import (
    DEFAULT_APK_RESEARCH_TERMS,
    DEFAULT_DECOMPILED_SOURCE_SUFFIXES,
    DEFAULT_DREAMEHOME_ASSET_TERMS,
    analyze_decompiled_sources,
    analyze_dreamehome_apk,
    analyze_dreamehome_assets,
    build_jadx_command,
    run_jadx_decompile,
)
from dreame_lawn_mower_client.apk_research import (
    analyze_dreamehome_apk as module_analyze,
)


def test_apk_research_indexes_terms_and_candidate_assets(tmp_path) -> None:
    apk_path = tmp_path / "dreamehome.apk"
    with zipfile.ZipFile(apk_path, "w") as archive:
        archive.writestr(
            "classes.dex",
            (
                b"POST /dreame-user-iot/iotstatus/props "
                b"sendAction STREAM_VIDEO session monitor"
            ),
        )
        archive.writestr(
            "assets/home_device/common_mower_protocol.json",
            b'{"2.1":{"13":"Charging Completed"}}',
        )

    result = analyze_dreamehome_apk(
        apk_path,
        terms=("iotstatus/props", "STREAM_VIDEO", "monitor", "mower"),
        max_string_length=120,
    )

    assert result["apk"]["filename"] == "dreamehome.apk"
    assert result["limits"]["max_string_length"] == 120
    assert result["dex_files"] == ["classes.dex"]
    assert result["candidate_assets"] == [
        "assets/home_device/common_mower_protocol.json"
    ]
    assert result["term_file_hits"]["iotstatus/props"] == ["classes.dex"]
    assert "classes.dex" in result["term_file_hits"]["monitor"]
    assert result["term_string_hits"]["STREAM_VIDEO"] == [
        "POST /dreame-user-iot/iotstatus/props sendAction STREAM_VIDEO session monitor"
    ]
    assert result["endpoint_strings"] == [
        "POST /dreame-user-iot/iotstatus/props sendAction STREAM_VIDEO session monitor"
    ]


def test_apk_research_skips_noisy_long_strings(tmp_path) -> None:
    apk_path = tmp_path / "dreamehome.apk"
    with zipfile.ZipFile(apk_path, "w") as archive:
        archive.writestr("classes.dex", b"map " + (b"noise" * 100))

    result = analyze_dreamehome_apk(
        apk_path,
        terms=("map",),
        max_string_length=20,
    )

    assert result["term_file_hits"]["map"] == ["classes.dex"]
    assert "map" not in result.get("term_string_hits", {})


def test_decompiled_source_research_indexes_files_and_snippets(tmp_path) -> None:
    source_dir = tmp_path / "jadx"
    source_dir.mkdir()
    service = source_dir / "DreameStreamService.java"
    service.write_text(
        "\n".join(
            (
                "class DreameStreamService {",
                '  private static final String API = "sendAction";',
                '  void start() { call("STREAM_VIDEO", "operType", "monitor"); }',
                "}",
            )
        ),
        encoding="utf-8",
    )
    ignored_binary = source_dir / "classes.dex"
    ignored_binary.write_bytes(b"STREAM_VIDEO sendAction")

    result = analyze_decompiled_sources(
        source_dir,
        terms=("STREAM_VIDEO", "operType", "sendAction"),
    )

    assert result["source"]["scanned_files"] == 1
    assert "DreameStreamService.java" in result["candidate_files"]
    assert result["term_file_hits"]["STREAM_VIDEO"] == ["DreameStreamService.java"]
    assert result["term_snippets"]["operType"] == [
        {
            "file": "DreameStreamService.java",
            "line": 3,
            "text": 'void start() { call("STREAM_VIDEO", "operType", "monitor"); }',
        }
    ]
    assert result["endpoint_snippets"] == [
        {
            "file": "DreameStreamService.java",
            "line": 2,
            "text": 'private static final String API = "sendAction";',
        }
    ]


def test_dreamehome_asset_research_indexes_compact_map_hints(tmp_path) -> None:
    source_dir = tmp_path / "assets"
    source_dir.mkdir()
    protocol = source_dir / "common_mower_protocol.json"
    protocol.write_text(
        '{"device":"mower","keyDefine":{"2.1":{"en":{"13":"Charging Completed"}}}}',
        encoding="utf-8",
    )
    plugin = source_dir / "RNExecutorBase.jx"
    plugin.write_text(
        'function sendAction(){ return "M_PATH current_map object_name"; }',
        encoding="utf-8",
    )
    ignored_large = source_dir / "vendor.js"
    ignored_large.write_text("map" * 1000, encoding="utf-8")

    result = analyze_dreamehome_assets(
        source_dir,
        terms=("mower", "M_PATH", "object_name", "missing"),
        max_file_size=100,
    )

    assert "mower" in DEFAULT_DREAMEHOME_ASSET_TERMS
    assert result["source"]["scanned_files"] == 2
    assert result["source"]["skipped_large_files"] == 1
    assert result["term_file_hits"]["mower"] == ["common_mower_protocol.json"]
    assert result["term_file_hits"]["M_PATH"] == ["RNExecutorBase.jx"]
    assert result["term_snippets"]["object_name"] == [
        {
            "file": "RNExecutorBase.jx",
            "line": 1,
            "text": 'function sendAction(){ return "M_PATH current_map object_name"; }',
        }
    ]
    assert "missing" in result["missing_terms"]


def test_public_package_exports_apk_research_helpers() -> None:
    assert "sendAction" in DEFAULT_APK_RESEARCH_TERMS
    assert ".java" in DEFAULT_DECOMPILED_SOURCE_SUFFIXES
    assert "map" in DEFAULT_DREAMEHOME_ASSET_TERMS
    assert analyze_dreamehome_apk is module_analyze
    assert callable(analyze_dreamehome_assets)
    assert callable(analyze_decompiled_sources)
    assert callable(build_jadx_command)
    assert callable(run_jadx_decompile)


def test_build_jadx_command_uses_explicit_executable(tmp_path) -> None:
    command = build_jadx_command(
        tmp_path / "dreame.apk",
        tmp_path / "jadx-output",
        jadx_path="C:/Tools/jadx/bin/jadx.bat",
    )

    assert command == [
        "C:/Tools/jadx/bin/jadx.bat",
        "-d",
        str(tmp_path / "jadx-output"),
        str(tmp_path / "dreame.apk"),
    ]


def test_run_jadx_decompile_reports_missing_executable(tmp_path) -> None:
    result = run_jadx_decompile(
        tmp_path / "dreame.apk",
        tmp_path / "jadx-output",
        jadx_path=tmp_path / "missing-jadx.exe",
    )

    assert result["ok"] is False
    assert result["error"] == "jadx_failed_to_start"
