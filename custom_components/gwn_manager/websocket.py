"""WebSocket API that feeds the network map card."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar, entity_registry as er

from .const import DOMAIN
from .coordinator import GWNConfigEntry
from .entity import async_get_device, client_name


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    """Register the WebSocket commands."""
    websocket_api.async_register_command(hass, ws_subscribe_topology)


@callback
def _topology(hass: HomeAssistant, entry: GWNConfigEntry) -> dict[str, Any]:
    """Build the network map of one config entry."""
    coordinator = entry.runtime_data
    ent_reg = er.async_get(hass)
    area_reg = ar.async_get(hass)

    def device_info(mac: str) -> tuple[str | None, str | None]:
        device = async_get_device(hass, entry.entry_id, mac)
        if device is None:
            return None, None
        area = area_reg.async_get_area(device.area_id) if device.area_id else None
        return device.id, area.name if area else None

    aps = []
    for mac, ap in coordinator.data.aps.items():
        device_id, area = device_info(mac)
        aps.append(
            {
                "mac": mac,
                "name": ap.get("name") or mac,
                "model": ap.get("apType"),
                "online": ap.get("status") == 1,
                "clients": ap.get("clients"),
                "ip": ap.get("ipv4") or ap.get("ip"),
                "device_id": device_id,
                "area": area,
                "entity_id": ent_reg.async_get_entity_id(
                    "binary_sensor", DOMAIN, f"{mac}_online"
                ),
            }
        )

    clients = []
    for mac, client in coordinator.data.clients.items():
        device_id, _ = device_info(mac)
        clients.append(
            {
                "mac": mac,
                "name": client_name(mac, client),
                "connected": bool(client.get("connected")),
                "ap": (client.get("apId") or "").upper() or None,
                "rssi": client.get("rssi") if client.get("connected") else None,
                "band": client.get("channelClassStr") if client.get("connected") else None,
                "ssid": client.get("ssid"),
                "ip": client.get("ipv4") or None,
                "device_id": device_id,
                "entity_id": ent_reg.async_get_entity_id("device_tracker", DOMAIN, mac),
            }
        )

    return {"entry_id": entry.entry_id, "title": entry.title, "aps": aps, "clients": clients}


@websocket_api.websocket_command(
    {
        vol.Required("type"): "gwn_manager/subscribe_topology",
        vol.Optional("entry_id"): str,
    }
)
@callback
def ws_subscribe_topology(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Send the network map now and after every update."""
    entries: list[GWNConfigEntry] = [
        entry
        for entry in hass.config_entries.async_loaded_entries(DOMAIN)
        if msg.get("entry_id") in (None, entry.entry_id)
    ]
    if not entries:
        connection.send_error(msg["id"], "not_found", "No loaded GWN Manager entry")
        return

    @callback
    def send() -> None:
        connection.send_message(
            websocket_api.event_message(
                msg["id"], {"networks": [_topology(hass, entry) for entry in entries]}
            )
        )

    unsubs = [entry.runtime_data.async_add_listener(send) for entry in entries]

    @callback
    def unsubscribe() -> None:
        for unsub in unsubs:
            unsub()

    connection.subscriptions[msg["id"]] = unsubscribe
    connection.send_result(msg["id"])
    send()
