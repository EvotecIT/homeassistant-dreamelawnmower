"""Config flow for Dreame lawn mower."""

from __future__ import annotations

from collections import Counter
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

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
    CONF_MAP_LABEL_SCALE,
    CONF_MAP_MARKER_IMAGE,
    CONF_MAP_MARKER_SCALE,
    CONF_MAP_ROTATION,
    CONF_MAP_ROTATIONS,
    CONF_MAP_STROKE_SCALE,
    CONF_MAP_THEME,
    CONF_MODEL,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    CONF_USERNAME,
    CONF_VIDEO_RETENTION,
    CONF_VIDEO_TRANSPORT,
    CONF_XP2P_LIBRARY_PATH,
    CONF_XP2P_RUNNER_COMMAND,
    CONF_XP2P_RUNNER_MODE,
    COUNTRY_OPTIONS,
    DEFAULT_COUNTRY,
    DEFAULT_MAP_LABEL_SCALE,
    DEFAULT_MAP_MARKER_SCALE,
    DEFAULT_MAP_ROTATION,
    DEFAULT_MAP_STROKE_SCALE,
    DEFAULT_MAP_THEME,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DEFAULT_VIDEO_RETENTION,
    DEFAULT_VIDEO_TRANSPORT,
    DOMAIN,
    MAP_ROTATION_OPTIONS,
    MAP_THEME_OPTIONS,
    MAX_MAP_LABEL_SCALE,
    MAX_SCAN_INTERVAL_SECONDS,
    MIN_MAP_LABEL_SCALE,
    MIN_SCAN_INTERVAL_SECONDS,
    VIDEO_RETENTION_OPTIONS,
    VIDEO_TRANSPORT_OPTIONS,
    XP2P_RUNNER_MODE_OPTIONS,
    XP2P_RUNNER_MODE_PROCESS,
)

CONF_DEVICE = "device"


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


def auth_error_key(err: Exception) -> str:
    """Return a non-secret Home Assistant config-flow error key."""
    text = str(err).casefold()
    if "unsupported account type" in text:
        return "invalid_account_type"
    if any(marker in text for marker in ("country", "region", "location")):
        return "invalid_region"
    if any(
        marker in text
        for marker in (
            "connection",
            "connect",
            "dns",
            "host",
            "network",
            "ssl",
            "timeout",
            "timed out",
        )
    ):
        return "cannot_connect"
    return "cannot_auth"


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
            except DreameLawnMowerAuthError as err:
                self._errors["base"] = auth_error_key(err)
            else:
                if not devices:
                    self._errors["base"] = "no_devices"
                else:
                    configured_ids = self._async_current_ids()
                    self._devices = {
                        device.unique_id: device
                        for device in devices
                        if device.unique_id not in configured_ids
                    }
                    if not self._devices:
                        return self.async_abort(reason="already_configured")
                    if len(self._devices) == 1:
                        return await self._async_create_entry(
                            next(iter(self._devices.values()))
                        )
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
            return await self._async_create_entry(
                self._devices[user_input[CONF_DEVICE]]
            )

        title_counts = Counter(device.title for device in self._devices.values())
        options = [
            selector.SelectOptionDict(
                value=unique_id,
                label=(
                    device.title
                    if title_counts[device.title] == 1
                    else f"{device.title} - {unique_id}"
                ),
            )
            for unique_id, device in self._devices.items()
        ]

        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options)
                    )
                }
            ),
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
            except DreameLawnMowerAuthError as err:
                self._errors["base"] = auth_error_key(err)
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
        self._entry_options = dict(config_entry.options)

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        if user_input is not None:
            options = dict(user_input)
            if CONF_MAP_ROTATIONS in self._entry_options:
                options[CONF_MAP_ROTATIONS] = self._entry_options[CONF_MAP_ROTATIONS]
            return self.async_create_entry(title="", data=options)

        video_transport = self._entry_options.get(
            CONF_VIDEO_TRANSPORT,
            DEFAULT_VIDEO_TRANSPORT,
        )
        if video_transport not in VIDEO_TRANSPORT_OPTIONS:
            video_transport = DEFAULT_VIDEO_TRANSPORT
        video_retention = self._entry_options.get(
            CONF_VIDEO_RETENTION,
            DEFAULT_VIDEO_RETENTION,
        )
        if video_retention not in VIDEO_RETENTION_OPTIONS:
            video_retention = DEFAULT_VIDEO_RETENTION

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=self._entry_options.get(
                            CONF_SCAN_INTERVAL,
                            DEFAULT_SCAN_INTERVAL_SECONDS,
                        ),
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(
                            min=MIN_SCAN_INTERVAL_SECONDS,
                            max=MAX_SCAN_INTERVAL_SECONDS,
                        ),
                    ),
                    vol.Optional(
                        CONF_MAP_LABEL_SCALE,
                        default=self._entry_options.get(
                            CONF_MAP_LABEL_SCALE,
                            DEFAULT_MAP_LABEL_SCALE,
                        ),
                    ): vol.All(
                        vol.Coerce(float),
                        vol.Range(
                            min=MIN_MAP_LABEL_SCALE,
                            max=MAX_MAP_LABEL_SCALE,
                        ),
                    ),
                    vol.Optional(
                        CONF_MAP_ROTATION,
                        default=self._entry_options.get(
                            CONF_MAP_ROTATION,
                            DEFAULT_MAP_ROTATION,
                        ),
                    ): vol.In(MAP_ROTATION_OPTIONS),
                    vol.Optional(
                        CONF_MAP_THEME,
                        default=self._entry_options.get(
                            CONF_MAP_THEME,
                            DEFAULT_MAP_THEME,
                        ),
                    ): vol.In(MAP_THEME_OPTIONS),
                    vol.Optional(
                        CONF_MAP_STROKE_SCALE,
                        default=self._entry_options.get(
                            CONF_MAP_STROKE_SCALE,
                            DEFAULT_MAP_STROKE_SCALE,
                        ),
                    ): vol.All(vol.Coerce(float), vol.Range(min=0.5, max=3.0)),
                    vol.Optional(
                        CONF_MAP_MARKER_SCALE,
                        default=self._entry_options.get(
                            CONF_MAP_MARKER_SCALE,
                            DEFAULT_MAP_MARKER_SCALE,
                        ),
                    ): vol.All(vol.Coerce(float), vol.Range(min=0.5, max=3.0)),
                    vol.Optional(
                        CONF_MAP_MARKER_IMAGE,
                        default=self._entry_options.get(CONF_MAP_MARKER_IMAGE, ""),
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.TEXT,
                        )
                    ),
                    vol.Optional(
                        CONF_VIDEO_RETENTION,
                        default=video_retention,
                    ): vol.In(VIDEO_RETENTION_OPTIONS),
                    vol.Optional(
                        CONF_VIDEO_TRANSPORT,
                        default=video_transport,
                    ): vol.In(VIDEO_TRANSPORT_OPTIONS),
                    vol.Optional(
                        CONF_XP2P_LIBRARY_PATH,
                        default=self._entry_options.get(
                            CONF_XP2P_LIBRARY_PATH,
                            "",
                        ),
                    ): str,
                    vol.Optional(
                        CONF_XP2P_RUNNER_COMMAND,
                        default=self._entry_options.get(
                            CONF_XP2P_RUNNER_COMMAND,
                            "",
                        ),
                    ): str,
                    vol.Optional(
                        CONF_XP2P_RUNNER_MODE,
                        default=self._entry_options.get(
                            CONF_XP2P_RUNNER_MODE,
                            XP2P_RUNNER_MODE_PROCESS,
                        ),
                    ): vol.In(XP2P_RUNNER_MODE_OPTIONS),
                }
            ),
        )
