"""Config flow for Dreame lawn mower."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.data_entry_flow import FlowResult

from .api import (
    DreameLawnMowerAuthError,
    DreameLawnMowerClient,
    DreameLawnMowerDescriptor,
    DreameLawnMowerTwoFactorRequiredError,
)
from .const import (
    ACCOUNT_TYPE_DREAME,
    ACCOUNT_TYPE_OPTIONS,
    CONF_ACCOUNT_TYPE,
    CONF_COUNTRY,
    CONF_DID,
    CONF_HOST,
    CONF_MAC,
    CONF_MODEL,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    CONF_USERNAME,
    COUNTRY_OPTIONS,
    DEFAULT_COUNTRY,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
    MAX_SCAN_INTERVAL_SECONDS,
    MIN_SCAN_INTERVAL_SECONDS,
)


async def async_discover_devices(
    *,
    username: str,
    password: str,
    country: str,
    account_type: str,
) -> list[DreameLawnMowerDescriptor]:
    """Helper for device discovery, separated for easier testing."""
    return list(
        await DreameLawnMowerClient.async_discover_devices(
            username=username,
            password=password,
            country=country,
            account_type=account_type,
        )
    )


class DreameLawnMowerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Dreame lawn mower config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._devices: dict[str, DreameLawnMowerDescriptor] = {}
        self._username = ""
        self._password = ""
        self._country = DEFAULT_COUNTRY
        self._account_type = ACCOUNT_TYPE_DREAME
        self._errors: dict[str, str] = {}

    @staticmethod
    def async_get_options_flow(config_entry):
        """Return the options flow."""
        return DreameLawnMowerOptionsFlow(config_entry)

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle the initial config flow."""
        self._errors = {}

        if user_input is not None:
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]
            self._country = user_input[CONF_COUNTRY]
            self._account_type = user_input[CONF_ACCOUNT_TYPE]

            try:
                devices = await async_discover_devices(
                    username=self._username,
                    password=self._password,
                    country=self._country,
                    account_type=self._account_type,
                )
            except DreameLawnMowerTwoFactorRequiredError:
                self._errors["base"] = "2fa_required"
            except DreameLawnMowerAuthError:
                self._errors["base"] = "cannot_auth"
            else:
                if not devices:
                    self._errors["base"] = "no_devices"
                elif len(devices) == 1:
                    return await self._async_create_entry(devices[0])
                else:
                    self._devices = {device.title: device for device in devices}
                    return await self.async_step_device()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ACCOUNT_TYPE, default=self._account_type): vol.In(
                        ACCOUNT_TYPE_OPTIONS
                    ),
                    vol.Required(CONF_USERNAME, default=self._username): str,
                    vol.Required(CONF_PASSWORD, default=self._password): str,
                    vol.Required(CONF_COUNTRY, default=self._country): vol.In(
                        COUNTRY_OPTIONS
                    ),
                }
            ),
            errors=self._errors,
        )

    async def async_step_device(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle selection when multiple mowers are discovered."""
        if user_input is not None:
            return await self._async_create_entry(self._devices[user_input["device"]])

        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema({vol.Required("device"): vol.In(self._devices)}),
        )

    async def async_step_reauth(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle credential refresh."""
        self._errors = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            try:
                devices = await async_discover_devices(
                    username=user_input[CONF_USERNAME],
                    password=user_input[CONF_PASSWORD],
                    country=user_input[CONF_COUNTRY],
                    account_type=entry.data[CONF_ACCOUNT_TYPE],
                )
            except DreameLawnMowerTwoFactorRequiredError:
                self._errors["base"] = "2fa_required"
            except DreameLawnMowerAuthError:
                self._errors["base"] = "cannot_auth"
            else:
                selected = next(
                    (item for item in devices if item.did == entry.data[CONF_DID]),
                    None,
                )
                if selected is None:
                    self._errors["base"] = "no_devices"
                else:
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={
                            CONF_USERNAME: user_input[CONF_USERNAME],
                            CONF_PASSWORD: user_input[CONF_PASSWORD],
                            CONF_COUNTRY: user_input[CONF_COUNTRY],
                            CONF_HOST: selected.host,
                            CONF_MAC: selected.mac,
                            CONF_MODEL: selected.model,
                            CONF_NAME: selected.name,
                            CONF_TOKEN: selected.token,
                        },
                    )

        return self.async_show_form(
            step_id="reauth",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME, default=entry.data[CONF_USERNAME]): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Required(
                        CONF_COUNTRY,
                        default=entry.data[CONF_COUNTRY],
                    ): vol.In(COUNTRY_OPTIONS),
                }
            ),
            errors=self._errors,
        )

    async def _async_create_entry(
        self,
        descriptor: DreameLawnMowerDescriptor,
    ) -> FlowResult:
        await self.async_set_unique_id(descriptor.unique_id)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=descriptor.title,
            data={
                CONF_ACCOUNT_TYPE: descriptor.account_type,
                CONF_COUNTRY: descriptor.country,
                CONF_DID: descriptor.did,
                CONF_HOST: descriptor.host,
                CONF_MAC: descriptor.mac,
                CONF_MODEL: descriptor.model,
                CONF_NAME: descriptor.name,
                CONF_PASSWORD: self._password,
                CONF_TOKEN: descriptor.token,
                CONF_USERNAME: self._username,
            },
        )


class DreameLawnMowerOptionsFlow(OptionsFlow):
    """Handle integration options."""

    def __init__(self, config_entry) -> None:
        self.config_entry = config_entry

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_SCAN_INTERVAL,
                            DEFAULT_SCAN_INTERVAL_SECONDS,
                        ),
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(
                            min=MIN_SCAN_INTERVAL_SECONDS,
                            max=MAX_SCAN_INTERVAL_SECONDS,
                        ),
                    )
                }
            ),
        )
