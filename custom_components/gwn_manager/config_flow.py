"""Config flow for the GWN Manager integration."""
from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_SCAN_INTERVAL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import GWNAuthError, GWNClient, GWNError
from .const import (
    CONF_APP_ID,
    CONF_APP_SECRET,
    CONF_AWAY_DELAY,
    CONF_MANUAL_UPLINKS,
    CONF_PRESENCE_INTERVAL,
    CONF_SERVER_URL,
    CONF_SSID_FILTER,
    CONF_TRACK_OFFLINE,
    DEFAULT_AWAY_DELAY,
    DEFAULT_PRESENCE_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    CONF_FAST_SNMP,
    CONF_FAST_SYSLOG,
    CONF_SNMP_COMMUNITY,
    CONF_SNMP_INTERVAL,
    CONF_SYSLOG_DEBUG_EVENTS,
    CONF_SYSLOG_PORT,
    DEFAULT_SERVER_URL,
    DEFAULT_SNMP_COMMUNITY,
    DEFAULT_SNMP_INTERVAL,
    DEFAULT_SYSLOG_PORT,
    DEFAULT_TRACK_OFFLINE,
    DOMAIN,
    MIN_PRESENCE_INTERVAL,
    MAX_SNMP_INTERVAL,
    MIN_SCAN_INTERVAL,
    MIN_SNMP_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _seconds(minimum: int, maximum: int) -> vol.All:
    return vol.All(
        NumberSelector(
            NumberSelectorConfig(
                min=minimum, max=maximum, unit_of_measurement="s", mode=NumberSelectorMode.BOX
            )
        ),
        vol.Coerce(int),
    )


async def _validate(hass: HomeAssistant, data: Mapping[str, Any]) -> dict[str, str]:
    """Try to log in and list the networks; return form errors."""
    client = GWNClient(
        async_get_clientsession(hass, verify_ssl=data.get(CONF_VERIFY_SSL, True)),
        data[CONF_SERVER_URL],
        data[CONF_APP_ID],
        data[CONF_APP_SECRET],
    )
    try:
        await client.authenticate()
        await client.get_networks()
    except GWNAuthError:
        return {"base": "invalid_auth"}
    except GWNError as err:
        _LOGGER.debug("Cannot connect: %s", err)
        return {"base": "cannot_connect"}
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Unexpected exception")
        return {"base": "unknown"}
    return {}


class GWNConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for GWN Manager."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_SERVER_URL] = user_input[CONF_SERVER_URL].rstrip("/")
            host = urlparse(user_input[CONF_SERVER_URL]).netloc or user_input[CONF_SERVER_URL]
            await self.async_set_unique_id(f"{host}_{user_input[CONF_APP_ID]}")
            self._abort_if_unique_id_configured()
            if not (errors := await _validate(self.hass, user_input)):
                return self.async_create_entry(title=f"GWN Manager ({host})", data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_SERVER_URL): str,
                        vol.Required(CONF_APP_ID): str,
                        vol.Required(CONF_APP_SECRET): PASSWORD,
                        vol.Required(CONF_VERIFY_SSL, default=True): bool,
                    }
                ),
                user_input or {CONF_SERVER_URL: DEFAULT_SERVER_URL},
            ),
            description_placeholders={
                "local_example": "https://192.168.1.10:8443",
                "cloud_example": DEFAULT_SERVER_URL,
            },
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, CONF_APP_SECRET: user_input[CONF_APP_SECRET]}
            if not (errors := await _validate(self.hass, data)):
                return self.async_update_reload_and_abort(entry, data=data)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_APP_SECRET): PASSWORD}),
            description_placeholders={"app_id": entry.data[CONF_APP_ID]},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> GWNOptionsFlow:
        return GWNOptionsFlow()


class GWNOptionsFlow(OptionsFlow):
    """Polling interval and which clients to track."""

    def _ssid_names(self) -> list[str]:
        """SSIDs currently known to the GWN Manager, plus the selected ones."""
        names = set(self.config_entry.options.get(CONF_SSID_FILTER, []))
        if (coordinator := getattr(self.config_entry, "runtime_data", None)) is not None:
            names.update(
                ssid["ssidName"]
                for ssid in coordinator.data.ssids.values()
                if ssid.get("ssidName")
            )
        return sorted(names)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_PRESENCE_INTERVAL,
                        default=options.get(CONF_PRESENCE_INTERVAL, DEFAULT_PRESENCE_INTERVAL),
                    ): _seconds(MIN_PRESENCE_INTERVAL, 600),
                    vol.Required(
                        CONF_AWAY_DELAY,
                        default=options.get(CONF_AWAY_DELAY, DEFAULT_AWAY_DELAY),
                    ): _seconds(0, 3600),
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=max(
                            options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                            MIN_SCAN_INTERVAL,
                        ),
                    ): _seconds(MIN_SCAN_INTERVAL, 3600),
                    vol.Required(
                        CONF_TRACK_OFFLINE,
                        default=options.get(CONF_TRACK_OFFLINE, DEFAULT_TRACK_OFFLINE),
                    ): bool,
                    vol.Optional(
                        CONF_SSID_FILTER, default=options.get(CONF_SSID_FILTER, [])
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=self._ssid_names(),
                            multiple=True,
                            mode=SelectSelectorMode.DROPDOWN,
                            custom_value=True,
                        )
                    ),
                    vol.Optional(
                        CONF_MANUAL_UPLINKS, default=options.get(CONF_MANUAL_UPLINKS, "")
                    ): TextSelector(TextSelectorConfig(multiline=True)),
                    vol.Required(
                        CONF_FAST_SYSLOG, default=options.get(CONF_FAST_SYSLOG, False)
                    ): bool,
                    vol.Required(
                        CONF_SYSLOG_PORT,
                        default=options.get(CONF_SYSLOG_PORT, DEFAULT_SYSLOG_PORT),
                    ): vol.All(
                        NumberSelector(
                            NumberSelectorConfig(min=1, max=65535, mode=NumberSelectorMode.BOX)
                        ),
                        vol.Coerce(int),
                    ),
                    vol.Required(
                        CONF_SYSLOG_DEBUG_EVENTS,
                        default=options.get(CONF_SYSLOG_DEBUG_EVENTS, False),
                    ): bool,
                    vol.Required(
                        CONF_FAST_SNMP, default=options.get(CONF_FAST_SNMP, False)
                    ): bool,
                    vol.Required(
                        CONF_SNMP_COMMUNITY,
                        default=options.get(CONF_SNMP_COMMUNITY, DEFAULT_SNMP_COMMUNITY),
                    ): str,
                    vol.Required(
                        CONF_SNMP_INTERVAL,
                        default=options.get(CONF_SNMP_INTERVAL, DEFAULT_SNMP_INTERVAL),
                    ): _seconds(MIN_SNMP_INTERVAL, MAX_SNMP_INTERVAL),
                }
            ),
        )
