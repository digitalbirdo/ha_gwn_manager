"""Connection events of GWN Manager clients."""
from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import GWNConfigEntry, GWNCoordinator
from .entity import GWNClientEntity, async_setup_client_entities

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GWNConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a connection event entity for every client."""
    async_setup_client_entities(
        entry,
        async_add_entities,
        lambda coordinator, mac: [GWNClientConnectionEvent(coordinator, mac)],
    )


class GWNClientConnectionEvent(GWNClientEntity, EventEntity):
    """Connected, disconnected and roamed events of a client.

    Disabled by default; the same events are fired on the event bus
    (gwn_manager_client_connected / _disconnected / _roamed) for every client.
    """

    _attr_event_types = ["connected", "disconnected", "roamed"]
    _attr_translation_key = "connection"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: GWNCoordinator, mac: str) -> None:
        super().__init__(coordinator, mac, "connection")

    @callback
    def _handle_coordinator_update(self) -> None:
        for mac, event_type, data in self.coordinator.data.events:
            if mac == self._mac:
                self._trigger_event(event_type, data)
        super()._handle_coordinator_update()
