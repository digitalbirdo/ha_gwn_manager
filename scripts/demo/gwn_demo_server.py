"""Fake GWN Manager open API with made-up data, for screenshots and demos.

Serves a small network (gateway, switch, three APs, ~30 clients, three SSIDs)
with values that change over time. Response shapes come from fixtures.json,
sanitized templates of a real GWN Manager (see make_fixtures.py).

    python scripts/demo/gwn_demo_server.py [--port 8899] [--images DIR]

--images: folder with <model>.png product pictures, e.g. downloaded from your
own GWN Manager (https://<gwn-manager>/img/topo/main_icon/GWN7665.png).
Without it the cards show their built-in icons.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import time
from pathlib import Path

from aiohttp import web

ARGS = argparse.ArgumentParser(description=__doc__.splitlines()[0])
ARGS.add_argument("--port", type=int, default=8899)
ARGS.add_argument("--images", type=Path)
ARGS.add_argument("--fixtures", type=Path, default=Path(__file__).with_name("fixtures.json"))
OPTIONS = ARGS.parse_args()
FIXTURES = json.loads(OPTIONS.fixtures.read_text())
START = time.time()

NET_ID = 1
NET_NAME = "Home"

# Devices: key -> (mac, model, name, ip, type)
DEVICES = {
    "router": ("00:0B:82:5A:00:01", "GWN7062", "Gateway", "192.168.50.1", "ROUTER"),
    "switch": ("00:0B:82:5A:00:02", "GWN7822P", "Core Switch", "192.168.50.2", "SWITCH"),
    "ap1": ("00:0B:82:5A:00:11", "GWN7665", "AP Living Room", "192.168.50.11", "AP"),
    "ap2": ("00:0B:82:5A:00:12", "GWN7665", "AP Office", "192.168.50.12", "AP"),
    "ap3": ("00:0B:82:5A:00:13", "GWN7665", "AP Basement", "192.168.50.13", "AP"),
}
# AP -> switch port (silkscreen number); switch -> router LAN1 via port 11
AP_PORTS = {"ap1": 17, "ap2": 21, "ap3": 23}
SWITCH_UPLINK_PORT = 11
RADIOS = {  # channel 2.4 / 5 / 6 GHz, power
    "router": (1, 36, None),
    "ap1": (6, 44, 37),
    "ap2": (11, 100, 69),
    "ap3": (1, 149, 101),
}
SSIDS = [  # id, name, bands, isolation
    (1, "Home", "2,5,6", 0),
    (2, "Home-IoT", "2", 0),
    (3, "Guest", "2,5", 1),
]

# Wireless clients: name, device, ssid id, band, sta mode, base rssi, rate
WIFI = [
    ("Laptop", "ap2", 1, "5G", "11AXA_HE80", -52, 600),
    ("Phone", "ap1", 1, "5G", "11AXA_HE80", -58, 480),
    ("Tablet", "ap1", 1, "5G", "11AC_VHT80", -63, 390),
    ("Smart TV", "ap1", 1, "5G", "11AC_VHT80", -49, 433),
    ("Phone 2", "ap2", 1, "6G", "11AXA_HE160", -55, 1200),
    ("Watch", "ap2", 1, "2.4G", "11NG_HT20", -66, 65),
    ("Robot Vacuum", "ap1", 2, "2.4G", "11NG_HT20", -61, 72),
    ("Thermostat", "ap1", 2, "2.4G", "11NG_HT20", -45, 72),
    ("Smart Plug 1", "ap1", 2, "2.4G", "11NG_HT20", -57, 65),
    ("Smart Plug 2", "ap2", 2, "2.4G", "11NG_HT20", -69, 52),
    ("LED Strip", "ap2", 2, "2.4G", "11NG_HT20", -48, 72),
    ("Doorbell", "ap1", 2, "2.4G", "11NG_HT20", -71, 39),
    ("Air Purifier", "ap2", 2, "2.4G", "11NG_HT20", -59, 72),
    ("Speaker Kitchen", "ap1", 2, "2.4G", "11NG_HT20", -53, 72),
    ("Speaker Office", "ap2", 2, "2.4G", "11NG_HT20", -42, 72),
    ("Washing Machine", "ap3", 2, "2.4G", "11NG_HT20", -64, 58),
    ("Heat Pump", "ap3", 2, "2.4G", "11NG_HT20", -55, 72),
    ("Water Meter", "ap3", 2, "2.4G", "11NG_HT20", -79, 13),
    ("3D Printer", "router", 2, "2.4G", "11NG_HT20", -47, 72),
    ("Garage Door", "router", 2, "2.4G", "11NG_HT20", -73, 26),
    ("Energy Meter", "router", 2, "2.4G", "11NG_HT20", -38, 72),
    ("Guest Phone", "ap1", 3, "5G", "11AXA_HE80", -67, 360),
]
# Wired clients: name, device, port
WIRED = [
    ("NAS", "switch", 10),
    ("Home Assistant", "switch", 12),
    ("Desktop PC", "switch", 14),
    ("EV Charger", "switch", 13),
    ("Solar Inverter", "switch", 15),
    ("Zigbee Gateway", "router", None),
]


def mac_of(index: int) -> str:
    return f"02:00:00:00:{index // 256:02X}:{index % 256:02X}"


def ip_of(index: int) -> str:
    return f"192.168.50.{100 + index}"


def wave(seed: int, period: float, amplitude: float) -> float:
    """Smooth pseudo-random variation over time."""
    t = time.time() - START
    return amplitude * (math.sin(t / period + seed) + 0.5 * math.sin(t / (period / 3.1) + seed * 2))


# ------------------------------------------------------------ templates


def template(section: str, model: str) -> dict:
    """Response template of a device model."""
    return copy.deepcopy(FIXTURES[section][model])


CLIENT_TEMPLATE = FIXTURES["client_wifi"]
WIRED_TEMPLATE = FIXTURES["client_wired"]
AP_LIST_TEMPLATE = FIXTURES["ap_list"]
SSID_LIST_TEMPLATE = FIXTURES["ssid_list"]
SSID_CONF_TEMPLATE = FIXTURES["ssid_conf"]
PORT_TEMPLATE = FIXTURES["switch_port"]

# ------------------------------------------------------------ live state

BYTES: dict[str, list[float]] = {}  # mac -> [tx, rx]
LAST_TICK = [time.time()]
WAN_BYTES = [5.0e9, 4.0e10]  # tx, rx


def tick() -> None:
    """Advance the byte counters."""
    now = time.time()
    dt = now - LAST_TICK[0]
    LAST_TICK[0] = now
    for index, (name, *_rest) in enumerate(WIFI):
        tx, rx = BYTES.setdefault(mac_of(index), [1e6 * (index + 1), 5e6 * (index + 1)])
        load = max(0.0, 1 + wave(index, 300, 0.8))
        BYTES[mac_of(index)] = [tx + 20_000 * load * dt, rx + 150_000 * load * dt]
    down = max(0.2e6, 8e6 + wave(1, 400, 6e6)) / 8
    up = max(0.1e6, 1.5e6 + wave(2, 500, 1.2e6)) / 8
    WAN_BYTES[0] += up * dt
    WAN_BYTES[1] += down * dt


def ssid_name(ssid_id: int) -> str:
    return next(name for i, name, *_ in SSIDS if i == ssid_id)


def wifi_client(index: int, live: bool) -> dict:
    name, device, ssid_id, band, mode, rssi, rate = WIFI[index]
    dev_mac, _, dev_name, _, dev_type = DEVICES[device]
    channel = RADIOS[device][{"2.4G": 0, "5G": 1, "6G": 2}[band]]
    client = copy.deepcopy(CLIENT_TEMPLATE)
    tx, rx = BYTES.get(mac_of(index), [0, 0])
    client.update(
        {
            "clientId": mac_of(index),
            "id": 1000 + index,
            "name": name,
            "dhcpHostname": name.lower().replace(" ", "-"),
            "clientName": None,
            "apId": dev_mac,
            "apName": dev_name,
            "deviceType": dev_type,
            "ipv4": ip_of(index),
            "ssid": ssid_name(ssid_id),
            "ssidId": ssid_id,
            "channel": channel,
            "channelClassStr": band,
            "channelClass": {"2.4G": 2, "5G": 5, "6G": 6}[band],
            "staMode": mode,
            "rssi": round(rssi + wave(index * 3, 120, 3)) if live else None,
            "txRate": max(1, round(rate * (0.8 + wave(index, 200, 0.15)))) if live else None,
            "rxRate": max(1, round(rate * (0.9 + wave(index + 5, 200, 0.1)))) if live else None,
            "txBytes": int(tx) if live else None,
            "rxBytes": int(rx) if live else None,
            "wired": 2,
            "port": "1/0/11" if device == "router" else "",
            "bssid": dev_mac.lower(),
            "online": 1,
            "assocSecond": 3600 * (index + 2),
            "lastSeen": time.strftime("%Y-%m-%d %H:%M"),
            "firstSeen": "2026-09-01 10:00",
            "vlanId": "1",
            "block": 1,
            "isGuest": 1 if ssid_id == 3 else 0,
        }
    )
    return client


def wired_client(index: int) -> dict:
    name, device, port = WIRED[index]
    dev_mac, _, dev_name, _, dev_type = DEVICES[device]
    client = copy.deepcopy(WIRED_TEMPLATE)
    client.update(
        {
            "clientId": mac_of(200 + index),
            "id": 2000 + index,
            "name": name,
            "dhcpHostname": name.lower().replace(" ", "-"),
            "apId": dev_mac,
            "apName": dev_name,
            "deviceType": dev_type,
            "ipv4": ip_of(60 + index),
            "port": f"1/0/{port}" if port else "",
            "switchPortName": f"1/0/{port}" if port else "",
            "wired": 1,
            "online": 1,
            "ssid": "",
            "ssidId": -1,
            "rssi": None,
            "channel": None,
            "channelClassStr": "",
            "staMode": None,
            "bssid": "",
            "lastSeen": time.strftime("%Y-%m-%d %H:%M"),
            "vlanId": "1",
            "block": 1,
        }
    )
    return client


def clients_on(device: str) -> list[int]:
    return [i for i, c in enumerate(WIFI) if c[1] == device]


# ------------------------------------------------------------ responses


def ap_list() -> dict:
    result = []
    for key, (mac, model, name, ip, _type) in DEVICES.items():
        item = copy.deepcopy(AP_LIST_TEMPLATE)
        item.update(
            {
                "mac": mac, "apType": model, "name": name, "ip": ip, "ipv4": ip, "status": 1,
                "clients": len(clients_on(key)) + sum(1 for w in WIRED if w[1] == key),
                "versionFirmware": {"GWN7665": "1.0.27.20", "GWN7062": "1.0.9.58", "GWN7822P": "1.0.15.137"}[model],
                "upTime": 860_000 + int(time.time() - START), "networkId": NET_ID,
            }
        )
        result.append(item)
    return {"result": result, "total": len(result), "totalPage": 1, "currentPage": 1}


def ap_info(key: str) -> dict:
    mac, model, name, ip, _type = DEVICES[key]
    info = template("ap_info", model)
    info.update({"mac": mac, "deviceName": name, "ip": ip, "ipv4": ip, "network": NET_NAME, "upTime": str(860_000 + int(time.time() - START))})
    seed = int(mac[-2:], 16)
    if key in RADIOS:
        ch24, ch5, ch6 = RADIOS[key]
        info["g24"] = [{"value": str(ch24), "key": "channel"}, {"value": "0", "key": "clients"}, {"value": "17", "key": "power"}]
        info["g5"] = [{"value": str(ch5), "key": "channel"}, {"value": "0", "key": "clients"}, {"value": "20", "key": "power"}]
        info["g6"] = [{"value": str(ch6 or ""), "key": "channel"}, {"value": "0", "key": "clients"}, {"value": "20" if ch6 else "", "key": "power"}]
        if key != "router":
            info["channelload_2g4"] = max(3, round(24 + wave(seed, 600, 12)))
            info["channelload_5g"] = max(1, round(6 + wave(seed + 1, 500, 4)))
            info["channelload_6g"] = max(1, round(2 + wave(seed + 2, 700, 1.5)))
            info["temperature"] = f"{52 + seed % 5}℃"
            info["cpuUsage"] = f"{8 + seed % 7}.0%"
        ssids = []
        for ssid_id, ssid, _bands, _iso in SSIDS:
            members = [i for i in clients_on(key) if WIFI[i][2] == ssid_id]
            per_band: dict = {}
            for i in members:
                band_key = {"2.4G": "2g4", "5G": "5g", "6G": "6g"}[WIFI[i][3]]
                per_band.setdefault(band_key, []).append({"rssi": WIFI[i][5], "clientMac": mac_of(i).replace(":", "").lower()})
            ssids.append({ssid: per_band})
        info["ssid"] = ssids
    if key in AP_PORTS:
        info["linkSpeed"] = [
            {"key1": "port0_speed", "key2": "port0_full_duplex", "value2": "1", "value1": "2500", "type": 0, "status": "connected"},
            {"key1": "port1_speed", "key2": "port1_full_duplex", "value2": "0", "value1": "0", "type": 0, "status": "disconnected"},
        ]
    return info


def device_info(key: str) -> dict:
    mac, model, name, ip, dev_type = DEVICES[key]
    info = template("device_info", model)
    info["deviceType"] = dev_type
    for item in info.get("result") or []:
        if item.get("key") == "name":
            item["value"] = name
        elif item.get("key") in ("ip", "ipV4"):
            item["value"] = ip
        elif item.get("key") == "mac":
            item["value"] = mac
        elif item.get("key") == "upTime":
            item["value"] = str(860_000 + int(time.time() - START))
    if dev_type == "ROUTER":
        for port in info.get("portInfo") or []:
            if port.get("role") == "1":
                port["connectDuration"] = str(500_000 + int(time.time() - START))
                port["ipv4Info"]["ip4Address"] = "203.0.113.25"
                port["ipv4Info"]["gateway"] = "203.0.113.1"
                port["ipv4Info"]["firstDns"] = "203.0.113.1"
                port["aggregate"] = {"txPackets": "1", "txBytes": str(int(WAN_BYTES[0])), "rxPackets": "1", "rxBytes": str(int(WAN_BYTES[1]))}
            elif port.get("silkScreenPort") == "LAN1":
                port["linkStatus"] = "1"
    return info


def switch_ports() -> list[dict]:
    used = {p: k for k, p in AP_PORTS.items()}
    wired = {w[2]: w for w in WIRED if w[1] == "switch"}
    ports = []
    for number in range(1, 29):
        port = copy.deepcopy(PORT_TEMPLATE)
        up = number in used or number in wired or number == SWITCH_UPLINK_PORT
        speed = "2500" if number in used else "1000" if up else None
        load = max(0, wave(number, 300, 1) + 1)
        port.update(
            {
                "portId": str(number - 1), "silkScreenPort": str(number), "name": f"1/0/{number}",
                "operStatus": "up" if up else "down", "operSpeed": speed,
                "inRate": str(int(load * 4e6)) if up else "0", "outRate": str(int(load * 9e5)) if up else "0",
                "powerflag": "on" if number in used else "off",
                "clientCount": str(len(clients_on(used[number])) if number in used else 1 if number in wired else 0),
                "type": "fiber" if number > 24 else "coper",
            }
        )
        ports.append(port)
    return ports


def ssid_list() -> dict:
    result = []
    for ssid_id, name, _bands, isolation in SSIDS:
        item = copy.deepcopy(SSID_LIST_TEMPLATE)
        item.update({"id": ssid_id, "ssidName": name, "clientIsolationEnabled": isolation, "onlineDevices": sum(1 for c in WIFI if c[2] == ssid_id)})
        result.append(item)
    return {"result": result, "total": len(result), "pages": 1, "pageSize": 100, "pageNum": 1}


def ssid_configuration(ssid_id: int) -> dict:
    _id, name, bands, isolation = next(s for s in SSIDS if s[0] == ssid_id)
    conf = copy.deepcopy(SSID_CONF_TEMPLATE)
    conf.update({"id": ssid_id, "ssidSsid": name, "ssidNewSsidBand": bands, "ssidSsidBand": "", "ssidIsolation": isolation, "ssidWpaKey": "demo-password", "ssidSsidHidden": 0})
    return {"configuration": conf}


def firmware() -> dict:
    result = []
    for key, (mac, model, *_rest) in DEVICES.items():
        current = {"GWN7665": "1.0.27.20", "GWN7062": "1.0.9.58", "GWN7822P": "1.0.15.137"}[model]
        latest = "1.0.27.32" if key == "ap3" else current
        result.append({"mac": mac.replace(":", ""), "currentVersion": current, "lastVersion": latest, "type": model})
    return {"result": result}


def client_list(body: dict) -> dict:
    tick()
    if body.get("untilNow") == 0:
        result = [wifi_client(i, True) for i in range(len(WIFI))] + [wired_client(i) for i in range(len(WIRED))]
    else:
        result = [wifi_client(i, False) for i in range(len(WIFI))]
    return {"result": result, "total": len(result), "totalPage": 1, "currentPage": 1}


def by_mac(mac: str) -> str:
    mac = mac.upper().replace(":", "")
    return next(k for k, d in DEVICES.items() if d[0].replace(":", "") == mac)


async def oapi(request: web.Request) -> web.Response:
    path = request.match_info["path"]
    body = json.loads(await request.text() or "{}")
    if path == "v1.0.0/network/list":
        data = {"result": [{"id": NET_ID, "networkName": NET_NAME}], "total": 1, "pages": 1, "pageSize": 100, "pageNum": 1}
    elif path == "v1.0.0/network/detail":
        data = {"networkName": NET_NAME, "id": NET_ID, "timezone": "Europe/Berlin", "country": "DE"}
    elif path == "v1.0.0/ap/list":
        data = ap_list()
    elif path == "v2.0.0/ap/info":
        data = ap_info(by_mac(body["mac"][0]))
    elif path == "v1.0.0/device/info":
        data = device_info(by_mac(body["mac"]))
    elif path == "v1.0.0/switch/port/info":
        if by_mac(body["mac"]) != "switch":
            return web.json_response({"retCode": 48103, "msg": "this device is not a switch"})
        data = switch_ports()
    elif path == "v1.0.0/upgrade/version":
        data = firmware()
    elif path == "v1.0.0/ssid/list":
        data = ssid_list()
    elif path == "v1.0.0/ssid/configuration":
        data = ssid_configuration(body["id"])
    elif path == "v1.0.0/client/list":
        data = client_list(body)
    else:
        return web.json_response({"retCode": 40004, "msg": f"demo: {path} not supported"})
    return web.json_response({"retCode": 0, "msg": "", "data": data})


async def token(request: web.Request) -> web.Response:
    return web.json_response({"access_token": "demo-token", "expires_in": 3600})


async def image(request: web.Request) -> web.Response:
    path = OPTIONS.images / f"{request.match_info['model']}.png" if OPTIONS.images else None
    if not path or not path.exists():
        # Like GWN Manager for unknown models: the web UI's HTML page
        return web.Response(text="<html></html>", content_type="text/html")
    return web.Response(body=path.read_bytes(), content_type="image/png")


app = web.Application()
app.router.add_post("/oauth/token", token)
app.router.add_post("/oapi/{path:.+}", oapi)
app.router.add_get("/img/topo/main_icon/{model}.png", image)
web.run_app(app, host="127.0.0.1", port=OPTIONS.port)
