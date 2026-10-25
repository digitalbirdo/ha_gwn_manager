"""Build fixtures.json (response templates of the demo server) from a real GWN Manager.

Only needed when the API changes. Captures one response of each kind and
removes everything that identifies the network: names, MACs, IPs, SSIDs,
serial numbers, coordinates, e-mail addresses, Wi-Fi keys. Review the result
before committing it (`grep` for your own names, IPs and MACs).

    GWN_URL=https://<gwn-manager>:8443 GWN_APP_ID=<id> GWN_APP_SECRET=<secret> \\
      python scripts/demo/make_fixtures.py scripts/demo/fixtures.json
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import aiohttp

API = Path(__file__).parents[2] / "custom_components" / "gwn_manager" / "api.py"
SENSITIVE_KEYS = {
    "sn", "SN", "PN", "partNumber", "latitude", "longitude", "email", "ssidWpaKey", "wanMac",
    "name", "deviceName", "apName", "ssidSsid", "ssidName", "ssid", "dhcpHostname", "clientName",
    "networkName", "network", "remarks", "deviceRemarks", "ssidRemark", "bssid", "ssids",
}


def scrub(value, key=None):
    """Remove anything that identifies the network from a response."""
    if isinstance(value, dict):
        if value.get("key") in SENSITIVE_KEYS:  # [{"key": "SN", "value": "..."}] lists
            return {**value, "value": ""}
        # Keys can be MACs too (e.g. "ssidClientInfo")
        return {scrub(k): scrub(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v, key) for v in value]
    if key in SENSITIVE_KEYS:
        return "" if isinstance(value, str) else None
    if isinstance(value, str):
        if value.lower().startswith("fe80:"):
            return ""
        value = re.sub(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "192.168.50.250", value)
        value = re.sub(r"(?i)\b[0-9a-f]{2}([:-]?[0-9a-f]{2}){5}\b", "00:0B:82:5A:FF:FF", value)
    return value


async def capture() -> dict:
    spec = importlib.util.spec_from_file_location("gwn_api", API)
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
        client = api.GWNClient(session, os.environ["GWN_URL"], os.environ["GWN_APP_ID"], os.environ["GWN_APP_SECRET"])
        network = (await client.get_networks())[0]["id"]
        devices = await client.get_access_points(network)
        fixtures: dict = {"ap_info": {}, "device_info": {}}
        for device in devices:
            model = device.get("apType")
            if model in fixtures["ap_info"]:
                continue
            info = await client.get_device_details(device["mac"])
            info.pop("ssid", None)  # rebuilt by the demo server
            fixtures["ap_info"][model] = info
            fixtures["device_info"][model] = await client.get_device_info(network, device["mac"])
            fixtures["device_info"][model].pop("ssidClientInfo", None)  # clients by MAC with their SSID
            if not fixtures.get("switch_port") and fixtures["device_info"][model].get("deviceType") == "SWITCH":
                fixtures["switch_port"] = (await client.get_switch_ports(network, device["mac"]))[0]
        fixtures["ap_list"] = devices[0]
        ssid = (await client.get_ssids(network))[0]
        fixtures["ssid_list"] = ssid
        fixtures["ssid_conf"] = (await client.get_ssid_configuration(ssid["id"])).get("configuration")
        live = await client.get_clients(network, connected_only=True)
        fixtures["client_wifi"] = next(c for c in live if c.get("rssi") is not None)
        fixtures["client_wired"] = next(c for c in live if c.get("rssi") is None)
        return scrub(fixtures)


if __name__ == "__main__":
    Path(sys.argv[1]).write_text(json.dumps(asyncio.run(capture()), indent=1, ensure_ascii=False) + "\n")
    print(f"Wrote {sys.argv[1]} - review it before committing.")
