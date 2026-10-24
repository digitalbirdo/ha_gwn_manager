"""Config flow for Grandstream GWN Manager integration."""
from __future__ import annotations
from .gwn_manager_api import GWNClient

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .const import CONF_APP_ID, CONF_APP_SECRET, CONF_SERVER_URL, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SERVER_URL, default="https://eu.gwn.cloud"): str,
        vol.Required(CONF_APP_ID, default=""): str,
        vol.Required(CONF_APP_SECRET, default=""): str,
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    client = GWNClient(data[CONF_APP_ID], data[CONF_APP_SECRET], data[CONF_SERVER_URL])
    
    print("Authenticating...")
    _LOGGER.warning(f"Authenticating...")
    if not client.authenticate():
        _LOGGER.debug(f"Invalid Auth...")
        raise InvalidAuth
    _LOGGER.warning(f"Auth successful...")
    # TODO: Validate the connection to the GWN Manager API here
    # hub = Hub(data[CONF_HOST])
    # if not await hub.authenticate(data[CONF_USERNAME], data[CONF_PASSWORD]):
    #     raise InvalidAuth

    # Return info that you want to store in the config entry.
    return {"title": "GWN Manager"}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Grandstream GWN Manager."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
