"""Config-entry ownership fixtures for isolated API tests."""

from types import SimpleNamespace

from custom_components.dreame_lawn_mower.const import DOMAIN


def runtime_hass(coordinators, **attributes):
    """Build a small HA boundary with coordinators owned by config entries."""
    entries = {
        entry_id: SimpleNamespace(
            entry_id=entry_id, domain=DOMAIN, runtime_data=coordinator
        )
        for entry_id, coordinator in coordinators.items()
    }
    config_entries = attributes.pop("config_entries", SimpleNamespace())
    config_entries.async_get_entry = entries.get
    config_entries.async_entries = lambda domain: [
        entry for entry in entries.values() if entry.domain == domain
    ]
    return SimpleNamespace(data={}, config_entries=config_entries, **attributes)
