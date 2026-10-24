"""Add the GWN Manager integration to the dev instance and create a test dashboard.

    GWN_URL=https://192.168.1.10:8443 GWN_APP_ID=... GWN_APP_SECRET=... \\
    DIR/.venv/bin/python scripts/dev-add-entry.py --token-file DIR/token.txt [--verify-ssl]

Credentials are read from the environment so they never end up in files or the
shell history of scripts. Also creates the dashboard "GWN Netz" (/gwn-netz/netz)
with the network map card.
"""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

import aiohttp


async def main(url: str, token: str, verify_ssl: bool) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.post(
            f"{url}/api/config/config_entries/flow", json={"handler": "gwn_manager"}
        ) as resp:
            resp.raise_for_status()
            flow = await resp.json()
        async with session.post(
            f"{url}/api/config/config_entries/flow/{flow['flow_id']}",
            json={
                "server_url": os.environ["GWN_URL"],
                "app_id": os.environ["GWN_APP_ID"],
                "app_secret": os.environ["GWN_APP_SECRET"],
                "verify_ssl": verify_ssl,
            },
        ) as resp:
            resp.raise_for_status()
            result = await resp.json()
        if result.get("type") != "create_entry":
            raise SystemExit(f"Setup failed: {result.get('errors') or result.get('reason')}")
        print(f"Added {result['title']}: {result['result']['state']}")

        async with session.ws_connect(f"{url}/api/websocket") as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": token})
            await ws.receive_json()
            await ws.send_json(
                {
                    "id": 1,
                    "type": "lovelace/dashboards/create",
                    "url_path": "gwn-netz",
                    "title": "GWN Netz",
                    "icon": "mdi:lan",
                    "mode": "storage",
                    "show_in_sidebar": True,
                    "require_admin": False,
                }
            )
            await ws.receive_json()  # fails harmlessly if it exists
            await ws.send_json(
                {
                    "id": 2,
                    "type": "lovelace/config/save",
                    "url_path": "gwn-netz",
                    "config": {
                        "views": [
                            {
                                "title": "Netzwerk",
                                "path": "netz",
                                "type": "panel",
                                "cards": [{"type": "custom:gwn-network-card", "height": 640}],
                            }
                        ]
                    },
                }
            )
            if not (await ws.receive_json()).get("success"):
                raise SystemExit("Could not save the dashboard")
        print(f"Dashboard: {url}/gwn-netz/netz")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://127.0.0.1:8123")
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--verify-ssl", action="store_true", help="verify the GWN certificate")
    args = parser.parse_args()
    asyncio.run(main(args.url.rstrip("/"), args.token_file.read_text().strip(), args.verify_ssl))
