"""Tests for the debug/manual OTA catalog summary helpers."""

from dreame_lawn_mower_client.debug_ota_catalog import (
    build_debug_ota_catalog_url,
    normalize_debug_ota_catalog_payload,
)


def test_build_debug_ota_catalog_url() -> None:
    assert (
        build_debug_ota_catalog_url("g2408")
        == "https://ota.tsingting.tech/api/version/g2408/?name=g2408"
    )


def test_normalize_debug_ota_catalog_payload_summarizes_tracks() -> None:
    payload = {
        "code": 0,
        "data": {
            "BUILD": [
                {
                    "id": 2,
                    "version": "4.3.6_0320",
                    "sha256sum": "aaa",
                    "url_endpoint": "https://packages.example",
                    "url": "G2408/BUILD/2/Release-arm/G2408_update-4.3.6_0320.img",
                },
                {
                    "id": 3,
                    "version": "4.3.6_0320_debug",
                    "sha256sum": "bbb",
                    "url_endpoint": "https://packages.example",
                    "url": (
                        "G2408/BUILD/2/Debug-arm/"
                        "G2408_update-4.3.6_0320_debug_debug.img"
                    ),
                },
                {
                    "id": 4,
                    "version": "4.3.6_0562",
                    "sha256sum": "ccc",
                    "url_endpoint": "https://packages.example",
                    "url": "G2408/BUILD/57/Release-arm/G2408_update-4.3.6_0562.img",
                },
            ],
            "FEATURE": [
                {
                    "id": 10,
                    "version": "202604181249Feature_G2408-0415-3294",
                    "sha256sum": "ddd",
                    "url_endpoint": "https://packages.example",
                    "url": (
                        "G2408/FEATURE/105/Release-arm/"
                        "G2408_update-202604181249Feature_G2408-0415-3294.img"
                    ),
                }
            ],
            "PREBUILD": [
                {
                    "id": 8,
                    "version": "4.3.6_0550",
                    "sha256sum": "eee",
                    "url_endpoint": "https://packages.example",
                    "url": (
                        "G2408/PREBUILD/20260411-11/Release-arm/"
                        "G2408_update-4.3.6_0550.img"
                    ),
                }
            ],
        },
    }

    result = normalize_debug_ota_catalog_payload(
        payload,
        model_name="g2408",
        current_version="4.3.6_0320",
    )

    assert result["available"] is True
    assert result["model_name"] == "g2408"
    assert result["current_version"] == "4.3.6_0320"
    assert result["current_version_present"] is True
    assert result["changelog_available"] is False
    assert "debug_catalog_has_no_changelog" in result["warnings"]
    assert result["tracks"]["BUILD"]["entry_count"] == 3
    assert result["tracks"]["BUILD"]["release_entry_count"] == 2
    assert result["tracks"]["BUILD"]["current_version_present"] is True
    assert result["tracks"]["BUILD"]["latest_release_version"] == "4.3.6_0562"
    assert result["tracks"]["PREBUILD"]["latest_release_version"] == "4.3.6_0550"
    assert (
        result["tracks"]["FEATURE"]["latest_release_version"]
        == "202604181249Feature_G2408-0415-3294"
    )
    assert result["latest_release_candidates"][0]["track"] == "BUILD"
    assert (
        result["latest_release_candidates"][0]["latest_release_download_url"]
        == "https://packages.example/G2408/BUILD/57/Release-arm/G2408_update-4.3.6_0562.img"
    )


def test_normalize_debug_ota_catalog_payload_handles_missing_data() -> None:
    result = normalize_debug_ota_catalog_payload({})

    assert result["available"] is False
    assert result["errors"] == [
        {
            "stage": "parse",
            "error": "Debug OTA catalog payload has no data map.",
        }
    ]
