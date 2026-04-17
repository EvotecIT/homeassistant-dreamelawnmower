"""Regression checks for APK research helpers."""

from __future__ import annotations

import zipfile

from dreame_lawn_mower_client import (
    DEFAULT_APK_RESEARCH_TERMS,
    analyze_dreamehome_apk,
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


def test_public_package_exports_apk_research_helpers() -> None:
    assert "sendAction" in DEFAULT_APK_RESEARCH_TERMS
    assert analyze_dreamehome_apk is module_analyze
