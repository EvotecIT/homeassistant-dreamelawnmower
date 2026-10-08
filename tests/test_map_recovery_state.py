"""Recovery preserves saved maps when cloud data is unavailable."""

from unittest.mock import Mock

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    map_manager as map_manager_module,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map import (
    DreameMapMowerMapManager,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_types import (
    MapData,
    RecoveryMapInfo,
)


class _DummyProtocol:
    """No network calls are needed for local recovery state."""


@pytest.mark.parametrize("raw_map", [None, "invalid-map", "x"])
def test_restore_map_keeps_saved_state_when_recovery_cannot_decode(raw_map):
    manager = DreameMapMowerMapManager(_DummyProtocol())
    saved = MapData()
    saved.rotation = 0
    manager._map_list = [7]
    manager._saved_map_data = {7: saved}
    manager.schedule_update = Mock()
    manager._map_data_updated = Mock()
    recovery = RecoveryMapInfo(7, {"thb": raw_map})

    manager.editor.restore_map(recovery)

    assert manager._saved_map_data[7] is saved
    manager.schedule_update.assert_called_once_with(15)
    manager._map_data_updated.assert_not_called()


@pytest.mark.parametrize("wifi_flag", [False, True])
@pytest.mark.parametrize("selected", [False, True])
def test_restore_map_updates_embedded_wifi_data_and_preserves_saved_metadata(
    monkeypatch, wifi_flag, selected,
):
    manager = DreameMapMowerMapManager(_DummyProtocol())
    saved = MapData()
    saved.rotation = 90
    saved.custom_name = "Garden"
    saved.map_name = "Garden map"
    saved.map_index = 2
    saved.timestamp_ms = 123
    manager._map_list = [7]
    manager._saved_map_data = {7: saved}
    if selected:
        manager._selected_map_id = 7
    manager.schedule_update = Mock()
    manager._map_data_updated = Mock()
    recovery = RecoveryMapInfo(7, {})
    restored = MapData()
    restored.wifi_map = wifi_flag
    restored.wifi_map_data = MapData()
    recovery.map_data = restored
    monkeypatch.setattr(map_manager_module.time, "time", lambda: 1000.0)

    manager.editor.restore_map(recovery)

    assert manager._saved_map_data[7] is restored
    assert restored.saved_map is True and restored.recovery_map is False
    assert (restored.rotation, restored.custom_name, restored.map_name) == (
        90, "Garden", "Garden map"
    )
    assert (restored.map_index, restored.timestamp_ms) == (2, 123)
    assert restored.last_updated == restored.wifi_map_data.last_updated == 1000.0
    assert manager._need_map_request and manager._need_map_list_request
    if selected:
        assert manager._map_data.restored_map is True
