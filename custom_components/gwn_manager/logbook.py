"""Describe GWN Manager events in the logbook."""
from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.logbook import LOGBOOK_ENTRY_MESSAGE, LOGBOOK_ENTRY_NAME
from homeassistant.core import Event, HomeAssistant, callback

from .const import DOMAIN, EVENT_CLIENT_CONNECTED, EVENT_CLIENT_DISCONNECTED, EVENT_CLIENT_ROAMED


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event], dict[str, str]]], None],
) -> None:
    """Describe the client connection events."""

    @callback
    def describe(event: Event) -> dict[str, str]:
        data = event.data
        ap = data.get("ap_name") or data.get("ap_mac") or "?"
        if event.event_type == EVENT_CLIENT_CONNECTED:
            message = f"connected to {ap}"
        elif event.event_type == EVENT_CLIENT_ROAMED:
            previous = data.get("previous_ap_name") or data.get("previous_ap_mac") or "?"
            message = f"roamed from {previous} to {ap}"
        else:
            message = f"disconnected from {ap}"
        return {LOGBOOK_ENTRY_NAME: data.get("name") or data.get("mac"), LOGBOOK_ENTRY_MESSAGE: message}

    for event_type in (EVENT_CLIENT_CONNECTED, EVENT_CLIENT_DISCONNECTED, EVENT_CLIENT_ROAMED):
        async_describe_event(DOMAIN, event_type, describe)
