"""Classify entry updates without reconnecting for presentation changes."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .const import (
    CONF_MAP_LABEL_SCALE,
    CONF_MAP_MARKER_IMAGE,
    CONF_MAP_MARKER_SCALE,
    CONF_MAP_MOWING_PATH_STYLE,
    CONF_MAP_ROTATION,
    CONF_MAP_ROTATIONS,
    CONF_MAP_SPOT_AREA_STYLE,
    CONF_MAP_STROKE_SCALE,
    CONF_MAP_THEME,
    CONF_SCAN_INTERVAL,
)
from .map_preview import CONF_MAP_RESTART_PREVIEW

PRESENTATION_OPTIONS = frozenset({
    CONF_MAP_LABEL_SCALE,
    CONF_MAP_MARKER_IMAGE,
    CONF_MAP_MARKER_SCALE,
    CONF_MAP_MOWING_PATH_STYLE,
    CONF_MAP_ROTATION,
    CONF_MAP_ROTATIONS,
    CONF_MAP_SPOT_AREA_STYLE,
    CONF_MAP_STROKE_SCALE,
    CONF_MAP_THEME,
})
HOT_UPDATE_OPTIONS = PRESENTATION_OPTIONS | {CONF_SCAN_INTERVAL}


@dataclass(slots=True)
class EntryUpdateSnapshot:
    """Retain applied configuration so mixed/unknown changes fail to reload."""

    data: dict[str, Any]
    options: dict[str, Any]

    @classmethod
    def capture(cls, entry: Any) -> EntryUpdateSnapshot:
        """Copy nested per-map settings rather than aliasing mutable values."""
        return cls(deepcopy(dict(entry.data)), deepcopy(dict(entry.options)))

    def changed_options(self, options: Mapping[str, Any]) -> frozenset[str]:
        """Include removed keys as changes, including explicit empty values."""
        return frozenset(
            key for key in self.options.keys() | options.keys()
            if (key in self.options) != (key in options)
            or self.options.get(key) != options.get(key)
        )

    def requires_reload(self, entry: Any) -> bool:
        """Connection, platform, and unknown options retain full reload semantics."""
        reload_changes = self.changed_options(entry.options) - HOT_UPDATE_OPTIONS
        if self.options.get(CONF_MAP_RESTART_PREVIEW, False) == entry.options.get(
            CONF_MAP_RESTART_PREVIEW, False
        ):
            # The options form materializes this new opt-in default for existing
            # entries. Saving a presentation change must not reload merely to
            # spell out the already-disabled persistence setting.
            reload_changes -= {CONF_MAP_RESTART_PREVIEW}
        return self.data != dict(entry.data) or bool(
            reload_changes
        )
