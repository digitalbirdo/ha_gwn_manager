"""Diagnostics support for GWN Manager."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_APP_ID, CONF_APP_SECRET, CONF_SNMP_COMMUNITY
from .coordinator import GWNConfigEntry

TO_REDACT = {
    CONF_APP_ID,
    CONF_APP_SECRET,
    CONF_SNMP_COMMUNITY,
    "email",
    "username",
    "authName",
    "socialName",
    "longitude",
    "latitude",
    "ssidWpaKey",
    "ip4Address",
    "wanMac",
    "wanIp",
    "ssidWepKey",
    "ppskProfile",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: GWNConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    return async_redact_data(
        {
            "entry": {"data": dict(entry.data), "options": dict(entry.options)},
            "data": asdict(entry.runtime_data.data),
            "fast_sources": (
                entry.runtime_data.fast_sources.diagnostics()
                if entry.runtime_data.fast_sources
                else None
            ),
        },
        TO_REDACT,
    )
