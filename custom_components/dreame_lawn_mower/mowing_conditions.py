"""Evaluate garden conditions without templates or mower commands."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from .mowing_permission import permission_block


class MowingConditions:
    """Own hysteresis and continuous drying/settling waits for one runtime.

    A restart resets both waits. Unavailable or old soil observations reset the
    drying timer; evaluating a rule never makes the source measurement fresh.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self._wet = True
        self._dry_since: datetime | None = None
        self._irrigation_until: datetime | None = None

    def evaluate(self, hass: Any, now: datetime) -> tuple[bool, str]:
        """Return a permission and readable explanation, using fresh HA state."""
        config = self.config
        blocks: list[str] = []
        soil = config["soil_entity"]
        if soil:
            state = hass.states.get(soil)
            value = math.nan
            if state is not None:
                try:
                    value = float(state.state)
                except (TypeError, ValueError):
                    pass
            wrong_unit = state is not None and state.attributes.get(
                "unit_of_measurement"
            ) not in {None, "%"}
            if wrong_unit:
                self._dry_since = None
                blocks.append("Soil moisture must use percentage units")
            elif not math.isfinite(value) or not 0 <= value <= 100:
                self._dry_since = None
                blocks.append("Soil moisture is unavailable or outside 0–100%")
            elif (
                not 0
                <= (now - state.last_reported).total_seconds()
                <= config["source_max_age"]
            ):
                self._dry_since = None
                blocks.append("Soil moisture report is stale")
            else:
                if value > config["wet_threshold"]:
                    self._wet = True
                elif value < config["dry_threshold"]:
                    self._wet = False
                if self._wet:
                    self._dry_since = None
                    blocks.append("Soil is too wet; waiting for the dry threshold")
                else:
                    self._dry_since = self._dry_since or now
                    until = self._dry_since + timedelta(
                        minutes=config["drying_minutes"]
                    )
                    if now < until:
                        time = dt_util.as_local(until).strftime("%H:%M")
                        blocks.append(f"Soil drying wait until {time}")
        irrigation = config["irrigation_entity"]
        if irrigation:
            state = hass.states.get(irrigation)
            if state is None or state.state != "off":
                self._irrigation_until = None
                blocks.append("Irrigation is running or its state is unknown")
            else:
                self._irrigation_until = self._irrigation_until or (
                    now + timedelta(minutes=config["settling_minutes"])
                )
                if now < self._irrigation_until:
                    time = dt_util.as_local(self._irrigation_until).strftime("%H:%M")
                    blocks.append(f"Irrigation settling wait until {time}")
        external = permission_block(hass, config["permission_entity"], 1800, now=now)
        if external:
            blocks.append(external[1])
        return (
            (False, "; ".join(blocks))
            if blocks
            else (True, "Garden conditions permit mowing")
        )
