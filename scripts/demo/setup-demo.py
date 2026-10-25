"""Prepare a dev instance that uses the demo server for screenshots.

Assigns the demo devices to areas and floors, renames the entry to
"GWN Manager" and sets up the dashboard "Network" with both cards.
Run after dev-add-entry.py (GWN_URL=http://127.0.0.1:8899).

    .venv/bin/python scripts/demo/setup-demo.py --url http://127.0.0.1:8123 --token-file token.txt
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import aiohttp

FLOORS = (("Upper floor", 1), ("Ground floor", 0), ("Basement", -1))
AREAS = (("Office", "Upper floor"), ("Living Room", "Ground floor"), ("Utility Room", "Basement"))
DEVICE_AREAS = {
    "AP Living Room": "Living Room",
    "AP Office": "Office",
    "AP Basement": "Utility Room",
    "Gateway": "Utility Room",
    "Core Switch": "Utility Room",
}
DASHBOARD = {
    "views": [
        {"title": "Topology", "path": "topology", "panel": True,
         "cards": [{"type": "custom:gwn-topology-card", "title": "Home network"}]},
        {"title": "Network map", "path": "map", "panel": True,
         "cards": [{"type": "custom:gwn-network-card", "title": "Network map", "height": 640}]},
    ]
}


async def main(url: str, token: str) -> None:
    async with aiohttp.ClientSession() as session, session.ws_connect(f"{url}/api/websocket") as ws:
        await ws.receive_json()
        await ws.send_json({"type": "auth", "access_token": token})
        await ws.receive_json()
        counter = 0

        async def call(message: dict):
            nonlocal counter
            counter += 1
            await ws.send_json({"id": counter, **message})
            while True:
                reply = await ws.receive_json()
                if reply.get("id") == counter:
                    return reply.get("result") if reply.get("success") else None

        floors = {f["name"]: f["floor_id"] for f in await call({"type": "config/floor_registry/list"})}
        for name, level in FLOORS:
            if name not in floors:
                floors[name] = (await call({"type": "config/floor_registry/create", "name": name, "level": level}))["floor_id"]
        areas = {a["name"]: a["area_id"] for a in await call({"type": "config/area_registry/list"})}
        for name, floor in AREAS:
            if name in areas:
                await call({"type": "config/area_registry/update", "area_id": areas[name], "floor_id": floors[floor]})
            else:
                areas[name] = (await call({"type": "config/area_registry/create", "name": name, "floor_id": floors[floor]}))["area_id"]
        for device in await call({"type": "config/device_registry/list"}):
            if area := DEVICE_AREAS.get(device["name"]):
                await call({"type": "config/device_registry/update", "device_id": device["id"], "area_id": areas[area]})
        for entry in await call({"type": "config_entries/get", "domain": "gwn_manager"}):
            await call({"type": "config_entries/update", "entry_id": entry["entry_id"], "title": "GWN Manager"})

        dashboards = await call({"type": "lovelace/dashboards/list"})
        dashboard = next((d for d in dashboards if d["url_path"] == "gwn-netz"), None)
        if dashboard is None:
            dashboard = await call({"type": "lovelace/dashboards/create", "url_path": "gwn-netz", "title": "Network", "icon": "mdi:lan", "mode": "storage"})
        else:
            await call({"type": "lovelace/dashboards/update", "dashboard_id": dashboard["id"], "title": "Network"})
        await call({"type": "lovelace/config/save", "url_path": "gwn-netz", "config": DASHBOARD})
        print(f"Demo set up: {url}/gwn-netz/topology")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8123")
    parser.add_argument("--token-file", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(main(args.url, args.token_file.read_text().strip()))
