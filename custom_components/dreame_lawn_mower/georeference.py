"""Revision-bound calibrations between native map coordinates and GPS.

The calibration ties one native map point (usually the charging station) to its
latitude and longitude, plus the compass heading of the native +Y axis and the
metres per native unit. Every consumer (map API, GPS tracker, automations) reads
it through ``entry_georeference`` so that a single option answers the question.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import voluptuous as vol

from .const import CONF_GEOREFERENCE
from .control_options import active_map_index
from .dreame_lawn_mower_client.map_inventory import (
    map_revision_hash,
    selected_map_revision,
)

DEFAULT_GEOREFERENCE_SCALE = 0.001  # Native map units are millimetres.
SERVICE_SET_GEOREFERENCE = "set_georeference"
SERVICE_CLEAR_GEOREFERENCE = "clear_georeference"

ATTR_ANCHOR_X = "anchor_x"
ATTR_ANCHOR_Y = "anchor_y"
ATTR_LATITUDE = "latitude"
ATTR_LONGITUDE = "longitude"
ATTR_HEADING = "heading"
ATTR_SCALE = "scale"
GEOREFERENCE_FIELDS = (
    ATTR_ANCHOR_X,
    ATTR_ANCHOR_Y,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    ATTR_HEADING,
    ATTR_SCALE,
)


def _finite(value: Any, name: str) -> float:
    """Accept real numbers only; booleans and non-finite values are rejected."""
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number.")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as err:
        raise ValueError(f"{name} must be a number.") from err
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite.")
    return number


def validate_coordinate(value: Any, name: str = "Coordinate") -> float:
    """Validate a native map coordinate."""
    return _finite(value, name)


def validate_latitude(value: Any) -> float:
    """Validate a WGS84 latitude in degrees."""
    latitude = _finite(value, "Latitude")
    if not -90 <= latitude <= 90:
        raise ValueError("Latitude must be between -90 and 90 degrees.")
    return latitude


def validate_longitude(value: Any) -> float:
    """Validate a WGS84 longitude in degrees."""
    longitude = _finite(value, "Longitude")
    if not -180 <= longitude <= 180:
        raise ValueError("Longitude must be between -180 and 180 degrees.")
    return longitude


def normalize_heading(value: Any) -> float:
    """Return a compass heading in [0, 360), clockwise from north."""
    heading = _finite(value, "Heading") % 360.0
    # A tiny negative input can round up to exactly 360 after the modulo.
    return 0.0 if heading >= 360.0 else heading


def validate_scale(value: Any) -> float:
    """Validate metres per native map unit."""
    scale = _finite(value, "Scale")
    if scale <= 0:
        raise ValueError("Scale must be greater than zero.")
    return scale


def metres_per_degree(latitude: float) -> tuple[float, float]:
    """Return metres per degree of latitude and of longitude at a latitude."""
    phi = math.radians(latitude)
    per_latitude = 111132.954 - 559.822 * math.cos(2 * phi) + 1.175 * math.cos(4 * phi)
    per_longitude = 111412.84 * math.cos(phi) - 93.5 * math.cos(3 * phi)
    return per_latitude, per_longitude


@dataclass(frozen=True, slots=True)
class Georeference:
    """Anchor a native point to GPS and orient the native +Y axis by compass."""

    anchor_x: float
    anchor_y: float
    latitude: float
    longitude: float
    heading: float
    scale: float = DEFAULT_GEOREFERENCE_SCALE

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> Georeference:
        """Validate untrusted fields, raising ``ValueError`` on the first fault."""
        if not isinstance(value, Mapping):
            raise ValueError("Georeference must be a mapping.")
        missing = [key for key in GEOREFERENCE_FIELDS[:-1] if value.get(key) is None]
        if missing:
            raise ValueError(f"Georeference is missing {', '.join(missing)}.")
        scale = value.get(ATTR_SCALE)
        return cls(
            anchor_x=validate_coordinate(value[ATTR_ANCHOR_X], "Anchor X"),
            anchor_y=validate_coordinate(value[ATTR_ANCHOR_Y], "Anchor Y"),
            latitude=validate_latitude(value[ATTR_LATITUDE]),
            longitude=validate_longitude(value[ATTR_LONGITUDE]),
            heading=normalize_heading(value[ATTR_HEADING]),
            scale=(
                DEFAULT_GEOREFERENCE_SCALE if scale is None else validate_scale(scale)
            ),
        )

    @classmethod
    def from_mapping(cls, value: Any) -> Georeference | None:
        """Read a stored calibration; a damaged one is treated as absent."""
        try:
            return cls.parse(value)
        except ValueError:
            return None

    def as_dict(self) -> dict[str, float]:
        """Serialize the stored and published calibration shape."""
        return {
            ATTR_ANCHOR_X: self.anchor_x,
            ATTR_ANCHOR_Y: self.anchor_y,
            ATTR_LATITUDE: self.latitude,
            ATTR_LONGITUDE: self.longitude,
            ATTR_HEADING: self.heading,
            ATTR_SCALE: self.scale,
        }

    def to_gps(self, x: float, y: float) -> tuple[float, float]:
        """Convert a native map point to (latitude, longitude)."""
        dx = (x - self.anchor_x) * self.scale
        dy = (y - self.anchor_y) * self.scale
        heading = math.radians(self.heading)
        east = dx * math.cos(heading) + dy * math.sin(heading)
        north = -dx * math.sin(heading) + dy * math.cos(heading)
        per_latitude, per_longitude = metres_per_degree(self.latitude)
        return (
            self.latitude + north / per_latitude,
            self.longitude + east / per_longitude,
        )

    def to_native(self, latitude: float, longitude: float) -> tuple[float, float]:
        """Convert (latitude, longitude) back to a native map point."""
        per_latitude, per_longitude = metres_per_degree(self.latitude)
        north = (latitude - self.latitude) * per_latitude
        east = (longitude - self.longitude) * per_longitude
        heading = math.radians(self.heading)
        dx = east * math.cos(heading) - north * math.sin(heading)
        dy = east * math.sin(heading) + north * math.cos(heading)
        return self.anchor_x + dx / self.scale, self.anchor_y + dy / self.scale


def current_georeference_map_index(coordinator: Any) -> int | None:
    """Withhold alignment while a map switch or selection remains unsettled."""
    if getattr(coordinator, "_map_switch_inventory_pending_event", None) is not None:
        return None
    index = active_map_index(coordinator.app_maps)
    selected = getattr(coordinator, "selected_map_index", None)
    return index if selected in (None, index) else None


def entry_georeference(
    entry: Any, app_maps: Any, map_index: int | None
) -> Georeference | None:
    """Read only a calibration bound to the unique verified current revision."""
    revision = selected_map_revision(app_maps, map_index)
    if revision is None:
        return None
    options = getattr(entry, "options", None)
    if not isinstance(options, Mapping):
        return None
    stored = options.get(CONF_GEOREFERENCE)
    maps = stored.get("maps") if isinstance(stored, Mapping) else None
    value = maps.get(str(map_index)) if isinstance(maps, Mapping) else None
    if not isinstance(value, Mapping) or value.get("map_revision") != revision:
        return None
    return Georeference.from_mapping(value)


def store_georeference(
    hass: Any,
    entry: Any,
    georeference: Georeference | None,
    *,
    app_maps: Any,
    map_index: int | None,
    expected_map_index: int | None = None,
    expected_map_revision: str | None = None,
) -> None:
    """Change only the active map calibration, rejecting stale editor context."""
    revision = selected_map_revision(app_maps, map_index)
    if revision is None:
        raise ValueError("The current map revision could not be verified.")
    if (
        expected_map_index is not None
        and expected_map_index != map_index
        or expected_map_revision is not None
        and expected_map_revision != revision
    ):
        raise ValueError("The map changed. Reopen alignment before saving.")
    options = dict(entry.options)
    stored = options.get(CONF_GEOREFERENCE)
    existing = stored.get("maps") if isinstance(stored, Mapping) else None
    maps = dict(existing) if isinstance(existing, Mapping) else {}
    key = str(map_index)
    if georeference is None:
        if key not in maps:
            return
        maps.pop(key)
    else:
        maps[key] = {**georeference.as_dict(), "map_revision": revision}
    if maps:
        options[CONF_GEOREFERENCE] = {"maps": maps}
    else:
        options.pop(CONF_GEOREFERENCE, None)
    hass.config_entries.async_update_entry(entry, options=options)


def _schema_field(validator: Callable[[Any], float]) -> Callable[[Any], float]:
    """Reuse the stored-calibration judges inside an action schema."""

    def validate(value: Any) -> float:
        try:
            return validator(value)
        except ValueError as err:
            raise vol.Invalid(str(err)) from err

    return validate


SET_GEOREFERENCE_SCHEMA: dict[str | vol.Marker, Any] = {
    vol.Required(ATTR_ANCHOR_X): _schema_field(
        lambda value: validate_coordinate(value, "Anchor X")
    ),
    vol.Required(ATTR_ANCHOR_Y): _schema_field(
        lambda value: validate_coordinate(value, "Anchor Y")
    ),
    vol.Required(ATTR_LATITUDE): _schema_field(validate_latitude),
    vol.Required(ATTR_LONGITUDE): _schema_field(validate_longitude),
    vol.Required(ATTR_HEADING): _schema_field(normalize_heading),
    vol.Optional(ATTR_SCALE, default=DEFAULT_GEOREFERENCE_SCALE): _schema_field(
        validate_scale
    ),
}


def _map_index(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise vol.Invalid("Map index must be a non-negative integer.")
    return value


def _map_revision(value: Any) -> str:
    revision = map_revision_hash(value)
    if revision is None:
        raise vol.Invalid("Map revision must be a 32-digit hexadecimal hash.")
    return revision


MAP_CONTEXT_SCHEMA: dict[str | vol.Marker, Any] = {
    vol.Optional("map_index"): _map_index,
    vol.Optional("map_revision"): _map_revision,
}
SET_GEOREFERENCE_SCHEMA.update(MAP_CONTEXT_SCHEMA)


def register_georeference_services(platform: Any) -> None:
    """Register the calibration actions on the mower entity platform."""
    platform.async_register_entity_service(
        SERVICE_SET_GEOREFERENCE, SET_GEOREFERENCE_SCHEMA, "async_set_georeference"
    )
    platform.async_register_entity_service(
        SERVICE_CLEAR_GEOREFERENCE, MAP_CONTEXT_SCHEMA, "async_clear_georeference"
    )
