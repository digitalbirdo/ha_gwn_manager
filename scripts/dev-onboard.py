"""Onboard a fresh dev instance and print a long-lived access token.

    DIR/.venv/bin/python scripts/dev-onboard.py [--url http://127.0.0.1:8123] [--out DIR]

Creates the user "dev" with a random password, finishes onboarding and writes
credentials.txt and token.txt (mode 600) to --out (default: current directory).
Run it with the dev instance's Python, which has aiohttp.
"""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import secrets

import aiohttp


async def main(url: str, out: Path) -> None:
    password = secrets.token_urlsafe(12)
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{url}/api/onboarding/users",
            json={
                "client_id": f"{url}/",
                "name": "Dev",
                "username": "dev",
                "password": password,
                "language": "en",
            },
        ) as resp:
            resp.raise_for_status()
            auth_code = (await resp.json())["auth_code"]

        async with session.post(
            f"{url}/auth/token",
            data={"grant_type": "authorization_code", "code": auth_code, "client_id": f"{url}/"},
        ) as resp:
            resp.raise_for_status()
            access_token = (await resp.json())["access_token"]

        headers = {"Authorization": f"Bearer {access_token}"}
        for step in ("core_config", "analytics", "integration"):
            async with session.post(
                f"{url}/api/onboarding/{step}",
                headers=headers,
                json={"client_id": f"{url}/", "redirect_uri": f"{url}/?auth_callback=1"},
            ) as resp:
                resp.raise_for_status()

        async with session.ws_connect(f"{url}/api/websocket") as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": access_token})
            await ws.receive_json()
            await ws.send_json(
                {
                    "id": 1,
                    "type": "auth/long_lived_access_token",
                    "client_name": "dev",
                    "lifespan": 3650,
                }
            )
            token = (await ws.receive_json())["result"]

    out.mkdir(parents=True, exist_ok=True)
    for name, content in (
        ("credentials.txt", f"user: dev\npassword: {password}\n"),
        ("token.txt", f"{token}\n"),
    ):
        path = out / name
        path.write_text(content)
        os.chmod(path, 0o600)
    print(f"Onboarded. Login: dev / see {out / 'credentials.txt'}; token in {out / 'token.txt'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://127.0.0.1:8123")
    parser.add_argument("--out", type=Path, default=Path.cwd())
    args = parser.parse_args()
    asyncio.run(main(args.url.rstrip("/"), args.out))
