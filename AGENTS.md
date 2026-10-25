# AGENTS.md – working on the GWN Manager integration

Guide for developers and coding agents (Claude Code, Codex, Cursor, …) to continue work on this
Home Assistant custom integration. `CLAUDE.md` only imports this file, so keep everything here.

## What this is

A Home Assistant custom integration (domain **`gwn_manager`**) for Grandstream **GWN Manager**
(self-hosted) and **GWN.Cloud**, built around the **clients** of the network: presence,
signal, access point, band/channel, throughput, connection events and a live network map card.
Installed through HACS from this repository. Requires Home Assistant **2026.8+**.

Not to be confused with *Grandstream GWN Manager Bridge* (shopsD, domain `grandstream_gwn`),
which focuses on controlling SSIDs/devices. Both can be installed side by side; don't reuse
that domain.

## Layout

```
custom_components/gwn_manager/
  api.py            Async GWN open API client (aiohttp): token, request signing, pagination
  coordinator.py    DataUpdateCoordinator: presence + full polling, data model, events, throughput
  entity.py         Base entities (AP, client, switch port, SSID) and dynamic entity creation
  __init__.py       Setup, platforms, "connected via" linking, serves the card, WebSocket API
  config_flow.py    Setup, reauth, options
  sensor.py binary_sensor.py device_tracker.py switch.py button.py update.py image.py event.py
  websocket.py      gwn_manager/subscribe_topology (feeds the card)
  model_image.py    /gwn_manager/model/<model>.png: proxies the product pictures of the GWN Manager
  logbook.py        Logbook texts for the client events
  diagnostics.py    Diagnostics with redaction
  frontend/gwn-network-card.js   Network map card (plain web component, no build step)
  translations/en.json, de.json
scripts/            Dev instance, onboarding, adding the integration, card screenshots
.github/workflows/validate.yml   hassfest + HACS validation on every push
```

## How it works

- **Two polling rates** in one coordinator: every *presence interval* (default 10 s) only the
  connected clients (`client/list`, `untilNow: 0`); every *scan interval* (default 90 s) the full
  data set (networks, devices, device details, switch ports, firmware, SSIDs, all known clients).
- **Clients** = full list (incl. offline, no live values) overlaid with the live list (RSSI,
  rates, channel, band, …). Throughput is derived from the session byte counters.
- **Events** (`gwn_manager_client_connected` / `_disconnected` / `_roamed`) are detected by
  comparing consecutive presence updates; they go to the event bus, logbook and event entities.
- Entities are described with `EntityDescription`s + `value_fn`/`exists_fn`; new clients,
  devices, ports and SSIDs are added at runtime by the `async_setup_*_entities` helpers.
- The card is served at `/gwn_manager/gwn-network-card.js` and added to every frontend with
  `add_extra_js_url`; it gets live data via the WebSocket subscription.

## GWN API notes (verified against a real GWN Manager)

Docs: https://doc.grandstream.dev/GWN-API/EN/ – they are incomplete; these were found by testing:

- Signature: `sha256("&access_token=…&appID=…&secretKey=…&timestamp=…&" + sha256(body) + "&")`.
  Send exactly the bytes you hashed (compact JSON).
- Endpoints without parameters must be called **without a body**; a signed `{}` returns
  `50003 Authentication Failure`.
- `client/list`: `untilNow: 1` = all known clients without live values;
  `untilNow: 0` = connected clients with RSSI, `txRate`/`rxRate` (Mbit/s), channel, band, etc.
- GWN Manager reports (dis)connects immediately, but refreshes RSSI, rates and byte counters
  only about every 90 s – polling those faster gains nothing.
- `v2.0.0/ap/info` accepts a MAC list but only answers for the first MAC.
  Some models report nonsense (CPU `192224%`, memory `0 MB`) – values are sanity-checked.
- `upgrade/version` returns MACs without colons (`normalize_mac`).
- The client `wired` flag is unreliable: GWN Manager sometimes reports Wi-Fi clients of an AP with
  `wired: 1`. The coordinator derives it from SSID/RSSI/band instead (`is_wired`).
- The channel width of a radio can't be read (`ap_*_width` exists only in `ap/config/edit`).
  Each live client has it in `staMode`, e.g. `11AXA_HE80` = Wi-Fi 6 at 80 MHz (HT/VHT/HE/EHT = Wi-Fi 4/5/6/7).
- Product pictures: `/img/topo/main_icon/<model>.png`, no login (GWN Manager and GWN.Cloud);
  unknown models return the web UI's HTML with status 200.
- Switch port rates are bit/s; PoE per port can be read (`powerflag`) but **not** switched.
- Timestamps without offset (`lastSeen`) are in the network's time zone (`network/detail`).
- `subscribe/*` push only supports BLE clients and alerts – no client connection push.
- Undocumented `app/*` endpoints (web UI, username/password) are deliberately not used.

## Local test instance

No Docker needed: Home Assistant Core runs in a Python venv. Requires
[uv](https://docs.astral.sh/uv/); Python 3.14 is downloaded by uv if missing.

```sh
scripts/dev-instance.sh setup ../ha-dev    # venv + HA + minimal config + symlink to the integration
scripts/dev-instance.sh run ../ha-dev      # http://localhost:8123, log in ../ha-dev/config/home-assistant.log
```

In a second shell, once the web UI answers:

```sh
cd ../ha-dev
.venv/bin/python ../ha_gwn_manager/scripts/dev-onboard.py --out .       # user "dev", writes credentials.txt + token.txt (mode 600)
GWN_URL=https://<gwn-manager>:8443 GWN_APP_ID=<app id> GWN_APP_SECRET=<secret> \
  .venv/bin/python ../ha_gwn_manager/scripts/dev-add-entry.py --token-file token.txt
#   add --verify-ssl for GWN.Cloud or a GWN Manager with a valid certificate
```

`dev-add-entry.py` also creates the dashboard **GWN Netz** (`/gwn-netz/netz`) with the map card.
Create the App ID/secret in GWN Manager under *System → API Developer Mode*.

Notes:

- The config is minimal on purpose: `default_config` needs ffmpeg/go2rtc, which a bare venv
  lacks. Add integrations you need for testing to `configuration.yaml`.
- The integration folder is a **symlink** into the repo: edit, then restart the instance.
  Python changes need a restart; the card JS is served uncached, a browser reload is enough.
- Stop the instance with `pkill -f "[b]in/hass -c"` (the brackets keep `pkill` from matching
  its own shell).
- Debug logging for `custom_components.gwn_manager` is on in the generated config; at runtime:
  `curl -X POST -H "Authorization: Bearer $(cat token.txt)" -d '{"custom_components.gwn_manager":"debug"}' http://127.0.0.1:8123/api/services/logger/set_level`.
- Running in a container (e.g. Docker on Unraid) without a published port? Forward one from the
  host: `docker run -d --name ha-dev-proxy -p 8124:8124 alpine/socat tcp-listen:8124,fork,reuseaddr tcp-connect:<container-ip>:8123`,
  then open `http://<host>:8124`.

### Useful checks against the dev instance

- Entry state: `GET /api/config/config_entries/entry` (look for `domain == "gwn_manager"`).
- Entities/devices: WebSocket `config/entity_registry/list`, `config/device_registry/list`.
- Card data: WebSocket `{"type": "gwn_manager/subscribe_topology"}`.
- Options: `POST /api/config/config_entries/options/flow {"handler": "<entry_id>"}`.
- Diagnostics: `GET /api/diagnostics/config_entry/<entry_id>` (secrets must be redacted).

### Testing the card in a browser

```sh
npm i playwright && npx playwright install chromium-headless-shell
node scripts/card-screenshot.mjs ../ha-dev/credentials.txt /gwn-netz/netz map.png
```

Prints the number of map nodes/links and page errors. The bubbles move, so hover tests are
flaky – call the card's methods via `page.evaluate` instead (e.g. `_showTooltip`).

## Conventions

- Match the existing style: `from __future__ import annotations`, typed, short docstrings,
  `EntityDescription`s, `has_entity_name` + `translation_key`.
- **Every user-facing string in both `translations/en.json` and `translations/de.json`.**
  hassfest rejects URLs in translation strings – pass them as description placeholders.
- No blocking I/O in the event loop (time zones via `dt_util.async_get_time_zone`, etc.).
- Device registry lookups: `async_get_device_by_identifier(identifier, entry_id)`
  (the old `async_get_device` is deprecated).
- New entities that act on the network (block, restart, firmware) or contain secrets
  (Wi-Fi QR code) or would flood the registry are **disabled by default**.
- Keep `diagnostics.py` redaction up to date when new sensitive fields appear.
- `python -m py_compile` the changed files and `node --check` the card before restarting.

## Privacy and safety

- This repository is **public**. Never commit credentials, IP addresses, MACs, device names or
  screenshots of a real network. Use sample data for docs.
- Never hard-code App IDs/secrets (not even as config-flow defaults) – pass them via the
  environment as shown above.
- Don't trigger actions on a real network (block a client, restart a device, install firmware)
  without the owner's explicit consent.

## Release

1. Bump `version` in `custom_components/gwn_manager/manifest.json` and the version constants of
   both cards (`VERSION` in `gwn-network-card.js`, `TOPOLOGY_VERSION` in `gwn-topology-card.js`).
2. Commit and push to `main`; wait for the **Validate** workflow (hassfest + HACS) to pass.
3. `gh release create vX.Y.Z --target main --title "vX.Y.Z – <short summary>" --notes …`.
   Always give the title a short summary after the version: HACS shows the release title in its
   version list, a bare version number tells users nothing.
4. Users update through HACS and restart Home Assistant.

HACS installs from the tagged sources. Don't switch to `zip_release`: GitHub's
release-download redirect currently sends a ~9 KB header that HACS (aiohttp, 8190 byte limit)
can't read.

## Open ideas

- Unit tests with `pytest-homeassistant-custom-component` and a mocked API.
- Show which switch port each AP is connected to on the map (the API doesn't expose it yet).
- README screenshot of the card with sample data.

The maintainer may keep a local, git-ignored `LIVING_DOCUMENT.md` with private project notes;
if it exists, read it first and keep it up to date.
