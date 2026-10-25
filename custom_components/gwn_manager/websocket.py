"""WebSocket API that feeds the network map card."""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar, entity_registry as er, floor_registry as fr

from .const import DOMAIN
from .coordinator import GWNConfigEntry
from .entity import async_get_device, client_name
from .model_image import model_image_url


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    """Register the WebSocket commands."""
    websocket_api.async_register_command(hass, ws_subscribe_topology)


def _ports(ap: dict[str, Any]) -> list[dict[str, Any]]:
    """Ports of a switch or router in one shape for the cards."""
    if ap.get("ports"):
        return [
            {
                "id": str(port.get("portId")),
                "label": str(port.get("silkScreenPort") or int(port.get("portId", 0)) + 1),
                "up": port.get("operStatus") == "up",
                "speed": int(port["operSpeed"]) if str(port.get("operSpeed") or "").isdigit() else None,
                "poe": port.get("powerflag") == "on",
                "clients": int(port["clientCount"]) if str(port.get("clientCount") or "").isdigit() else None,
            }
            for port in ap["ports"]
        ]
    return [
        {
            "id": str(port.get("portId")),
            "label": port.get("silkScreenPort") or port.get("portName"),
            "up": port.get("linkStatus") == "1",
            "speed": None,
            "poe": False,
            "clients": None,
            "wan": port.get("role") == "1",
        }
        for port in ap.get("info", {}).get("portInfo") or []
    ]


# Client "staMode", e.g. "11NG_HT20", "11AC_VHT80", "11AXA_HE160", "11BE_EHT320"
STA_MODE = re.compile(r"_(HT|VHT|HE|EHT)(\d+)")
WIFI_GENERATION = {"HT": 4, "VHT": 5, "HE": 6, "EHT": 7}
# Radios in the AP details and the band names the clients use
RADIOS = (("g24", "2g4", "2.4G"), ("g5", "5g", "5G"), ("g6", "6g", "6G"))


def _sta_mode(client: dict[str, Any]) -> tuple[int | None, int | None]:
    """Wi-Fi generation and channel width (MHz) a client is connected with."""
    if match := STA_MODE.search(client.get("staMode") or ""):
        return WIFI_GENERATION[match.group(1)], int(match.group(2))
    return None, None


def _int(value: Any) -> int | None:
    try:
        return int(str(value).strip().rstrip("%"))
    except ValueError:
        return None


def _radios(
    ap: dict[str, Any], widths: dict[str, int], load_entity: Callable[[str], str | None]
) -> list[dict[str, Any]]:
    """Channel, transmit power and channel load of each active radio.

    The API doesn't report the channel width of a radio, only the one each
    client uses; the widest of those is shown.
    """
    details = ap.get("details") or {}
    radios = []
    for radio, key, band in RADIOS:
        values = {item.get("key"): item.get("value") for item in details.get(radio) or []}
        if not (channel := _int(values.get("channel"))):
            continue
        load = _int(details.get(f"channelload_{key}"))
        radios.append(
            {
                "band": band,
                "channel": channel,
                "power": _int(values.get("power")),
                "load": load if load is not None and 0 <= load <= 100 else None,
                "width": widths.get(band),
                # Sensor with the history of the channel load, for the card's graph
                "load_entity": load_entity(key),
            }
        )
    return radios


SSID_BANDS = {"2": "2.4G", "5": "5G", "6": "6G"}
GUEST_NAME = re.compile(r"gast|guest", re.IGNORECASE)


def _ssids(
    ap: dict[str, Any], configs: dict[str, dict[str, Any]], clients: dict[str, int]
) -> list[dict[str, Any]]:
    """SSIDs an access point broadcasts, with their bands and connected clients."""
    result = []
    for item in (ap.get("details") or {}).get("ssid") or []:
        for name in item if isinstance(item, dict) else ():
            config = configs.get(name, {})
            bands = str(config.get("ssidNewSsidBand") or config.get("ssidSsidBand") or "")
            result.append(
                {
                    "name": name,
                    "bands": [SSID_BANDS[b] for b in bands.split(",") if b in SSID_BANDS],
                    "hidden": config.get("ssidSsidHidden") == 1,
                    # Guest networks: client isolation, captive portal or the name says so
                    "guest": config.get("ssidIsolation") == 1
                    or config.get("ssidPortalEnable") == 1
                    or bool(GUEST_NAME.search(name)),
                    "clients": clients.get(name, 0),
                }
            )
    return result


@callback
def _topology(hass: HomeAssistant, entry: GWNConfigEntry) -> dict[str, Any]:
    """Build the network map of one config entry."""
    coordinator = entry.runtime_data
    ent_reg = er.async_get(hass)
    area_reg = ar.async_get(hass)
    floor_reg = fr.async_get(hass)

    def device_info(mac: str) -> tuple[str | None, str | None, ar.AreaEntry | None]:
        """Device ID, user-given device name and area."""
        device = async_get_device(hass, entry.entry_id, mac)
        if device is None:
            return None, None, None
        area = area_reg.async_get_area(device.area_id) if device.area_id else None
        return device.id, device.name_by_user, area

    bands: dict[str, dict[str, int]] = {}
    widths: dict[str, dict[str, int]] = {}
    ssid_clients: dict[str, dict[str, int]] = {}
    ssid_configs = {
        ssid.get("ssidName"): ssid.get("configuration") or {}
        for ssid in coordinator.data.ssids.values()
    }
    for client in coordinator.data.clients.values():
        if (
            client.get("connected")
            and client.get("wired") != 1
            and (ap_mac := (client.get("apId") or "").upper())
        ):
            band = client.get("channelClassStr") or "?"
            bands.setdefault(ap_mac, {})[band] = bands.setdefault(ap_mac, {}).get(band, 0) + 1
            if ssid := client.get("ssid"):
                per_ap = ssid_clients.setdefault(ap_mac, {})
                per_ap[ssid] = per_ap.get(ssid, 0) + 1
            if width := _sta_mode(client)[1]:
                ap_widths = widths.setdefault(ap_mac, {})
                ap_widths[band] = max(width, ap_widths.get(band, 0))

    aps = []
    for mac, ap in coordinator.data.aps.items():
        device_id, user_name, area = device_info(mac)
        floor = floor_reg.async_get_floor(area.floor_id) if area and area.floor_id else None
        aps.append(
            {
                "mac": mac,
                "name": user_name or ap.get("name") or mac,
                "model": ap.get("apType"),
                "image": model_image_url(ap.get("apType")),
                "online": ap.get("status") == 1,
                "clients": ap.get("clients"),
                "ip": ap.get("ipv4") or ap.get("ip"),
                "device_id": device_id,
                "area": area.name if area else None,
                # Floor of the AP's area, used by the card to arrange APs by floor.
                "floor": floor.name if floor else None,
                "floor_id": floor.floor_id if floor else None,
                "floor_level": floor.level if floor else None,
                "entity_id": ent_reg.async_get_entity_id(
                    "binary_sensor", DOMAIN, f"{mac}_online"
                ),
                "type": ap.get("deviceType"),
                "firmware": ap.get("versionFirmware"),
                "bands": bands.get(mac, {}),
                "radios": _radios(
                    ap,
                    widths.get(mac, {}),
                    lambda key, mac=mac: ent_reg.async_get_entity_id(
                        "sensor", DOMAIN, f"{mac}_channel_load_{key}"
                    ),
                ),
                "ssids": _ssids(ap, ssid_configs, ssid_clients.get(mac, {})),
                "ports": _ports(ap),
                "wan": ap.get("wan"),
                "wan_entities": {
                    "download": ent_reg.async_get_entity_id("sensor", DOMAIN, f"{mac}_wan_download"),
                    "upload": ent_reg.async_get_entity_id("sensor", DOMAIN, f"{mac}_wan_upload"),
                }
                if ap.get("wan")
                else None,
                # Physical uplink {parent, port, ownPort, source}, detected or manual
                "uplink": ap.get("uplink"),
            }
        )

    clients = []
    for mac, client in coordinator.data.clients.items():
        device_id, user_name, _ = device_info(mac)
        connected = bool(client.get("connected"))
        generation, width = _sta_mode(client) if connected else (None, None)
        clients.append(
            {
                "mac": mac,
                "name": user_name or client_name(mac, client),
                "connected": bool(client.get("connected")),
                "wired": client.get("wired") == 1,
                "port": client.get("port") or None,
                "ap": (client.get("apId") or "").upper() or None,
                "rssi": client.get("rssi") if client.get("connected") else None,
                "band": client.get("channelClassStr") if client.get("connected") else None,
                "channel": client.get("channel") if client.get("connected") else None,
                "width": width,
                "wifi": generation,
                "tx_rate": client.get("txRate") if connected else None,
                "rx_rate": client.get("rxRate") if connected else None,
                "ssid": client.get("ssid"),
                "ip": client.get("ipv4") or None,
                "device_id": device_id,
                "entity_id": ent_reg.async_get_entity_id("device_tracker", DOMAIN, mac),
                "signal_entity": ent_reg.async_get_entity_id("sensor", DOMAIN, f"{mac}_rssi"),
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
