"""Option updates preserve connections only for supported live changes."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.dreame_lawn_mower import _async_update_listener
from custom_components.dreame_lawn_mower.const import DOMAIN
from custom_components.dreame_lawn_mower.option_updates import EntryUpdateSnapshot


def _entry(options=None):
    return SimpleNamespace(entry_id="mower", data={"did": "a2"}, options=options or {})


@pytest.mark.parametrize("options", [
    {"map_theme": "mint"}, {"map_label_scale": 2.0},
    {"map_rotations": {"0": 270}}, {"scan_interval": 30}, {},
])
def test_live_options_preserve_coordinator(options):
    entry = _entry({"map_theme": "emerald"})
    coordinator = SimpleNamespace(
        applied_entry_update=EntryUpdateSnapshot.capture(entry),
        async_update_listeners=Mock(), update_interval=timedelta(seconds=60),
    )
    hass = SimpleNamespace(
        data={DOMAIN: {entry.entry_id: coordinator}},
        config_entries=SimpleNamespace(async_reload=AsyncMock()),
    )
    entry.options = options
    asyncio.run(_async_update_listener(hass, entry))
    hass.config_entries.async_reload.assert_not_called()
    coordinator.async_update_listeners.assert_called_once_with()
    assert coordinator.applied_entry_update.options == options
    assert coordinator.update_interval == timedelta(
        seconds=options.get("scan_interval", 60)
    )


@pytest.mark.parametrize("change", ["connection", "mixed", "unknown"])
def test_connection_and_unknown_options_still_reload(change):
    entry = _entry()
    coordinator = SimpleNamespace(
        applied_entry_update=EntryUpdateSnapshot.capture(entry),
        async_update_listeners=Mock(),
    )
    hass = SimpleNamespace(
        data={DOMAIN: {entry.entry_id: coordinator}},
        config_entries=SimpleNamespace(async_reload=AsyncMock()),
    )
    if change == "connection":
        entry.data = {"did": "replacement"}
    elif change == "mixed":
        entry.options = {"map_theme": "mint", "video_transport": "cloud"}
    else:
        entry.options = {"future_connection_option": True}
    asyncio.run(_async_update_listener(hass, entry))
    hass.config_entries.async_reload.assert_awaited_once_with("mower")
    coordinator.async_update_listeners.assert_not_called()


def test_nested_rotations_are_copied_and_repeated_update_is_noop():
    entry = _entry({"map_rotations": {"0": 0}})
    snapshot = EntryUpdateSnapshot.capture(entry)
    entry.options["map_rotations"]["0"] = 270
    assert snapshot.changed_options(entry.options) == {"map_rotations"}
    coordinator = SimpleNamespace(
        applied_entry_update=EntryUpdateSnapshot.capture(entry),
        async_update_listeners=Mock(),
    )
    hass = SimpleNamespace(
        data={DOMAIN: {entry.entry_id: coordinator}},
        config_entries=SimpleNamespace(async_reload=AsyncMock()),
    )
    asyncio.run(_async_update_listener(hass, entry))
    hass.config_entries.async_reload.assert_not_called()
    coordinator.async_update_listeners.assert_not_called()


@pytest.mark.parametrize("before,after,reload", [
    ({}, {"map_restart_preview": False, "map_theme": "mint"}, False),
    ({"map_restart_preview": False}, {}, False),
    ({}, {"map_restart_preview": True}, True),
    ({"map_restart_preview": True}, {}, True),
])
def test_preview_default_materialization_is_not_an_opt_in_change(
    before, after, reload
):
    snapshot = EntryUpdateSnapshot.capture(_entry(before))
    assert snapshot.requires_reload(_entry(after)) is reload
