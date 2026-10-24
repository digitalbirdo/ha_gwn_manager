"""Switches for GWN Manager clients."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import GWNError
from .coordinator import GWNConfigEntry, GWNCoordinator
from .entity import GWNClientEntity, async_setup_client_entities

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GWNConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a network access switch for every client."""
    async_setup_client_entities(
        entry,
        async_add_entities,
        lambda coordinator, mac: [GWNClientAccessSwitch(coordinator, mac)],
    )


class GWNClientAccessSwitch(GWNClientEntity, SwitchEntity):
    """Network access of a client; turning it off blocks the client."""

    _attr_translation_key = "network_access"
    _attr_entity_category = EntityCategory.CONFIG
    # Blocking is a deliberate action; users enable the switches they need.
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: GWNCoordinator, mac: str) -> None:
        super().__init__(coordinator, mac, "network_access")

    @property
    def is_on(self) -> bool | None:
        # API: 0 = blocked, 1 = not blocked
        block = self.client.get("block")
        return None if block is None else block != 0

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set_blocked(False)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set_blocked(True)

    async def _set_blocked(self, blocked: bool) -> None:
        client = self.client
        try:
            await self.coordinator.client.set_client_blocked(
                client["networkId"], self._mac, blocked
            )
        except GWNError as err:
            raise HomeAssistantError(f"Could not change network access: {err}") from err
        # Show the new state right away; the next refresh confirms it.
        client["block"] = 0 if blocked else 1
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
