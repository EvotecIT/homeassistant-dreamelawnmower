"""Entry-owned coordinator access for platforms and integration-wide APIs."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import DreameLawnMowerCoordinator

type DreameLawnMowerConfigEntry = ConfigEntry[DreameLawnMowerCoordinator]


def get_coordinator(
    hass: HomeAssistant, entry_id: str,
) -> DreameLawnMowerCoordinator | None:
    """Resolve entry-owned data, including setup and failed-unload ownership."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        return None
    # The integration assigns this field only after its first refresh, and
    # clears it on failed setup or successful platform unload.
    return cast(
        "DreameLawnMowerCoordinator | None", getattr(entry, "runtime_data", None),
    )


def iter_coordinators(hass: HomeAssistant) -> Iterator[DreameLawnMowerCoordinator]:
    """Yield only coordinators currently owned by this integration's entries."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if (coordinator := get_coordinator(hass, entry.entry_id)) is not None:
            yield coordinator
