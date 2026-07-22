"""Integration-surface test for the downloaded diagnostics bundle."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from enum import IntFlag
from types import SimpleNamespace

from custom_components.dreame_lawn_mower import diagnostics as diagnostics_module
from custom_components.dreame_lawn_mower.const import DOMAIN
from custom_components.dreame_lawn_mower.diagnostic_events import (
    DreameLawnMowerDiagnosticEventStore,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerDescriptor,
    DreameLawnMowerSnapshot,
)


class _AnonymousFeature(IntFlag):
    """Feature flag with no named zero member, matching Home Assistant enums."""

    ENABLED = 1


def test_downloaded_diagnostics_combines_report_entities_and_recent_events(
    monkeypatch,
) -> None:
    descriptor = DreameLawnMowerDescriptor(
        did="device-1",
        name="Garden Mower",
        model="dreame.mower.g2568a",
        display_model="A2 1200",
        account_type="dreame",
        country="eu",
    )
    snapshot = DreameLawnMowerSnapshot(
        descriptor=descriptor,
        available=True,
        state="mowing",
        state_name="mowing",
        activity="mowing",
        capabilities=("camera_streaming",),
        raw_attributes={},
    )
    device = SimpleNamespace(
        name="Garden Mower",
        available=True,
        host=None,
        token=None,
        unknown_properties={},
        realtime_properties={},
        last_realtime_message=None,
        status=None,
        capability=None,
        info=None,
    )

    async def _refresh() -> None:
        return None

    event_store = DreameLawnMowerDiagnosticEventStore()
    event_store.record(
        code="video_cloud_start_failed",
        source="video_camera",
        message="accessToken=secret failed",
    )
    coordinator = SimpleNamespace(
        data=snapshot,
        client=SimpleNamespace(_device=device),
        diagnostic_events=event_store,
        last_update_success=True,
        last_exception=None,
        update_interval=timedelta(seconds=30),
        async_request_refresh=_refresh,
    )
    state = SimpleNamespace(
        state="idle",
        attributes={
            "anonymous_feature": _AnonymousFeature(0),
            "last_stream_error_code": "video_cloud_start_failed",
            "last_stream_error": "accessToken=secret failed",
        },
    )
    hass = SimpleNamespace(
        data={DOMAIN: {"entry-1": coordinator}},
        states=SimpleNamespace(get=lambda _entity_id: state),
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        data={"did": "device-1", "token": "secret"},
        options={
            "video_transport": "cloud",
            "xp2p_runner_command": "runner --token=secret",
        },
        state=SimpleNamespace(value="loaded"),
        disabled_by=None,
        version=1,
        minor_version=2,
    )
    registry_entry = SimpleNamespace(
        entity_id="camera.garden_live_video",
        original_name="Live Video",
        translation_key=None,
        entity_category=None,
        disabled_by=None,
    )

    async def _system_info(_hass):
        return {
            "installation_type": "Home Assistant OS",
            "version": "2026.7.1",
            "python_version": "3.13.5",
            "arch": "aarch64",
            "user": "private-user",
        }

    monkeypatch.setattr(
        diagnostics_module.system_info,
        "async_get_system_info",
        _system_info,
    )
    monkeypatch.setattr(
        diagnostics_module,
        "async_get_loaded_integration",
        lambda _hass, _domain: SimpleNamespace(version="0.3.0"),
    )
    monkeypatch.setattr(diagnostics_module.er, "async_get", lambda _hass: object())
    monkeypatch.setattr(
        diagnostics_module.er,
        "async_entries_for_config_entry",
        lambda _registry, _entry_id: [registry_entry],
    )

    payload = asyncio.run(
        diagnostics_module.async_get_config_entry_diagnostics(hass, entry)
    )

    assert payload["diagnostic_schema_version"] == 6
    assert payload["report_context"]["integration_version"] == "0.3.0"
    assert payload["report_context"]["home_assistant"]["arch"] == "aarch64"
    assert "user" not in payload["report_context"]["home_assistant"]
    assert payload["entry"]["did"] == "**REDACTED**"
    assert payload["entry_options"]["xp2p_runner_command"] == "**REDACTED**"
    assert payload["entities"][0]["original_name"] == "Live Video"
    assert payload["entities"][0]["attributes"]["last_stream_error"] == (
        "accessToken=**REDACTED** failed"
    )
    assert payload["entities"][0]["attributes"]["anonymous_feature"] == 0
    assert payload["recent_events"][0]["code"] == "video_cloud_start_failed"
    assert payload["recent_events"][0]["message"] == (
        "accessToken=**REDACTED** failed"
    )
