"""Native Home Assistant forms for mowing without YAML or templates."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlowResult, OptionsFlow
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.helpers import selector

from .mowing_automation_config import (
    AUTOMATION_SCHEMA,
    CONF_MOWING_AUTOMATION,
    automation_config,
)
from .runtime_data import DreameLawnMowerConfigEntry


class MowingAutomationOptionsFlow(OptionsFlow):
    """Collect a single optional automation for this mower's current map."""

    _source_entry: DreameLawnMowerConfigEntry
    _mowing_input: dict[str, Any]

    async def async_step_mowing_automation(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Pick an existing visual Schedule helper; do not edit its definition."""
        if tuple(int(part) for part in HA_VERSION.split(".")[:2]) < (2026, 7):
            return self.async_abort(reason="mowing_requires_2026_7")
        self._mowing_input = automation_config(
            self._source_entry.options.get(CONF_MOWING_AUTOMATION)
        ) or AUTOMATION_SCHEMA({})
        if user_input is not None:
            self._mowing_input.update(user_input)
            return await self.async_step_mowing_conditions()
        return self.async_show_form(
            step_id="mowing_automation",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "enabled", default=self._mowing_input["enabled"]
                    ): bool,
                    vol.Required(
                        "schedule_entity", default=self._mowing_input["schedule_entity"]
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="schedule")
                    ),
                }
            ),
        )

    async def async_step_mowing_conditions(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Select garden sensors and plain numeric thresholds."""
        errors: dict[str, str] = {}
        if user_input is not None:
            for key in ("soil_entity", "irrigation_entity", "permission_entity"):
                self._mowing_input[key] = user_input.get(key, "")
            self._mowing_input.update(user_input)
            if (
                self._mowing_input["dry_threshold"]
                >= self._mowing_input["wet_threshold"]
            ):
                errors["dry_threshold"] = "dry_threshold_below_wet"
            else:
                return await self.async_step_mowing_behavior()
        data = self._mowing_input
        schema: dict[Any, Any] = {}
        for key, domains in (
            ("soil_entity", ["sensor"]),
            ("irrigation_entity", ["switch", "binary_sensor", "input_boolean"]),
            ("permission_entity", ["switch", "binary_sensor", "input_boolean"]),
        ):
            schema[vol.Optional(key, description={"suggested_value": data[key]})] = (
                selector.EntitySelector(selector.EntitySelectorConfig(domain=domains))
            )
        for key, maximum, unit in (
            ("wet_threshold", 100, "%"),
            ("dry_threshold", 100, "%"),
            ("drying_minutes", 1440, "min"),
            ("settling_minutes", 1440, "min"),
            ("source_max_age", 86400, "s"),
        ):
            schema[vol.Required(key, default=data[key])] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=60 if key == "source_max_age" else 0,
                    max=maximum,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement=unit,
                )
            )
        return self.async_show_form(
            step_id="mowing_conditions",
            data_schema=vol.Schema(schema),
            errors=errors,
        )

    async def async_step_mowing_behavior(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Keep skip/start-only defaults; require explicit bounded recovery."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._mowing_input.update(user_input)
            data = self._mowing_input
            if data["resume_when_allowed"] and (
                data["interruption"] != "pause" or not data["recovery_minutes"]
            ):
                errors["base"] = "recovery_requires_pause_and_deadline"
            else:
                return await self.async_step_mowing_review()
        data = self._mowing_input
        schema: dict[Any, Any] = {}
        for key, minimum, maximum, unit in (
            ("minimum_battery", 20, 100, "%"),
            ("defer_minutes", 0, 1440, "min"),
            ("recovery_minutes", 0, 1440, "min"),
        ):
            schema[vol.Required(key, default=data[key])] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=minimum,
                    max=maximum,
                    unit_of_measurement=unit,
                    mode=selector.NumberSelectorMode.BOX,
                )
            )
        schema[vol.Required("interruption", default=data["interruption"])] = (
            selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["none", "pause", "dock"],
                    translation_key="mowing_interruption",
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        )
        for key in ("resume_when_allowed", "notify_outcome"):
            schema[vol.Required(key, default=data[key])] = bool
        return self.async_show_form(
            step_id="mowing_behavior",
            data_schema=vol.Schema(schema),
            errors=errors,
        )

    async def async_step_mowing_review(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Confirm schedule ownership and persist only this entry's options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._mowing_input.update(user_input)
            config = automation_config(self._mowing_input)
            if not config:
                errors["base"] = "native_plans_confirmation_required"
            else:
                # Revoke this wizard's existing intent before replacing its inputs.
                coordinator = getattr(self._source_entry, "runtime_data", None)
                runtime = getattr(coordinator, "mowing_automation", None)
                if runtime is not None and config != self._source_entry.options.get(
                    CONF_MOWING_AUTOMATION
                ):
                    await runtime.async_cancel_owned()
                options = dict(self._source_entry.options)
                options[CONF_MOWING_AUTOMATION] = config
                return self.async_create_entry(title="", data=options)
        return self.async_show_form(
            step_id="mowing_review",
            description_placeholders={
                "schedule": self._entity_name(self._mowing_input["schedule_entity"]),
                "soil": self._entity_name(self._mowing_input["soil_entity"]),
                "irrigation": self._entity_name(
                    self._mowing_input["irrigation_entity"]
                ),
                "defer": str(int(self._mowing_input["defer_minutes"])),
            },
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "native_plans_disabled",
                        default=self._mowing_input["native_plans_disabled"],
                    ): bool,
                }
            ),
            errors=errors,
        )

    def _entity_name(self, entity_id: str) -> str:
        """Show the names selected in the picker rather than configuration IDs."""
        state = self.hass.states.get(entity_id) if entity_id else None
        return state.name if state is not None else entity_id or "—"
