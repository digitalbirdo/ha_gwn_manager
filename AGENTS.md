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
  fast/             Optional fast sources: syslog listener, SNMP pollers, overlay state
  frontend/gwn-network-card.js   Network map card (plain web component, no build step)
  translations/en.json, de.json
tests/              pytest for the pure fast-source modules
scripts/            Dev instance, onboarding, adding the integration, card screenshots
scripts/demo/       Demo GWN API server with made-up data, README screenshots
docs/screenshots/   README screenshots (demo data only)
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

## Fast sources (`fast/`): syslog and SNMP from the devices

Optional (options `fast_syslog`, `fast_snmp`); `fast/manager.py` runs them, `fast/state.py` keeps
what they report and is laid over the API data in `GWNCoordinator._compose()`. Overlay entries
expire after `fast_max_age`, then the API wins again. `async_fast_update()` publishes without
`async_set_updated_data` (that would restart the poll timer and starve the API polling).

- Syslog (`fast/syslog_parse.py`): hostapd `<if>: AP-STA-CONNECTED/DISCONNECTED <mac>` and
  `Sta is freed: sta=…, bssid=…` (level notice); `gseventlog send {json}` (subtype 0/1, plus client
  info) only at level debug. Sender → device by source IP, else the MAC tag `GWNxxxx[<mac12>]`.
  ~40 lines/s at notice, ~150/s at debug – keep the substring pre-filter cheap.
- SNMP v2c (`fast/snmp.py`): enterprise 42397. APs (GWN76xx) client table `…42397.1.1.3.3.1`
  (2 MAC, 4 BSSID, 6 SNR dB, 11 rate), routers (GWN70xx) `…42397.1.2.1.3.1` (2 MAC without colons,
  3 BSSID, 6 band, 8 RSSI dBm). Only single-OID GETNEXT works: GETBULK times out, GET by index and
  GETNEXT with several OIDs return wrong data. ~0.25 s per client and column, so only the needed
  columns are walked. Detection via `…42397.1.1.2` (AP) / `…42397.1.2.1` (router).
  The SNMP engine must be built in an executor (`build_engine`, MIB loading blocks).
- Band of an AP BSSID isn't in SNMP; it's learned from clients the API lists with the same AP.
- Unit tests for the pure parts: `pip install -r requirements_test.txt && pytest tests`.

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
- **Always verify every change in the dev instance before committing or releasing** (restart,
  then check the behaviour in the browser, e.g. with Playwright). Syntax checks alone are not enough.

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
2. If the cards or entities changed visibly, retake the screenshots (see *Screenshots* below) and
   commit them with the release.
3. Commit and push to `main`; wait for the **Validate** workflow (hassfest + HACS) to pass.
4. `gh release create vX.Y.Z --target main --title "vX.Y.Z – <short summary>" --notes …`.
   Always give the title a short summary after the version: HACS shows the release title in its
   version list, a bare version number tells users nothing. Always pass real release notes too
   (a short bullet list of the changes) – never leave `--notes` empty or rely on
   `--generate-notes` alone; HACS shows the release body as the update description.
   Write the notes to a file and use `--notes-file` (quoted heredoc): backticks inside
   `--notes "…"` are run by the shell and once left only "-" as the body.
5. Users update through HACS and restart Home Assistant.

HACS installs from the tagged sources. Don't switch to `zip_release`: GitHub's
release-download redirect currently sends a ~9 KB header that HACS (aiohttp, 8190 byte limit)
can't read.

## Screenshots

README screenshots (`docs/screenshots/`) never come from a real network. They are taken from a
dev instance connected to the **demo server** `scripts/demo/gwn_demo_server.py`: a fake GWN open
API with made-up devices (gateway GWN7062, switch GWN7822P, three GWN7665), ~30 clients, the SSIDs
Home / Home-IoT / Guest and 192.168.50.x addresses; values change over time. Response shapes come
from `scripts/demo/fixtures.json` (sanitized templates of a real GWN Manager).

1. Start the demo server (product pictures optional, e.g. downloaded from your own GWN Manager
   `https://<gwn-manager>/img/topo/main_icon/<model>.png` into a local folder – don't commit them):
   `python scripts/demo/gwn_demo_server.py --images <folder>` (port 8899).
2. Use a **separate** dev instance (`scripts/dev-instance.sh setup ../ha-demo`, run, onboard; stop
   other instances on port 8123 first), then
   `GWN_URL=http://127.0.0.1:8899 GWN_APP_ID=demo GWN_APP_SECRET=demo .venv/bin/python
   ../ha_gwn_manager/scripts/dev-add-entry.py --token-file token.txt` and
   `.venv/bin/python ../ha_gwn_manager/scripts/demo/setup-demo.py --token-file token.txt`
   (areas/floors, entry title, dashboard "Network" with both cards).
3. For the graphs: stop Home Assistant, `python scripts/demo/backfill-history.py
   ../ha-demo/config/home-assistant_v2.db` (24 h of made-up history), start it again.
4. `node scripts/demo/screenshots.mjs ../ha-demo/credentials.txt docs/screenshots`
   (Playwright as for `card-screenshot.mjs`; dark mode, English, 2x).
5. Look at every image before committing: no real names, IPs, MACs or SSIDs. The network map is
   force-directed – retake it if labels overlap badly.

If the GWN API changes, rebuild the fixtures from a real GWN Manager with
`scripts/demo/make_fixtures.py` and review the result (it scrubs names, MACs, IPs, SSIDs, serials,
coordinates and keys, but check with `grep` for your own values before committing).

## Open ideas

- Zeroconf discovery: GWN devices announce `_gwn_controller._udp.local.` (TXT `product`, `mac`,
  `version`, `role`) and `_http._tcp` ("Grandstream <model> Web Management"); GWN Manager itself
  doesn't. So discovery could only offer "GWN devices found – set up GWN Manager?" (manifest
  `zeroconf` + `async_step_zeroconf`, abort if an entry exists); URL, App ID and secret stay manual.
  About an hour; mainly helps new users find the integration.
- Unit tests for the HA parts with `pytest-homeassistant-custom-component` and a mocked API
  (the pure fast-source modules already have tests in `tests/`).
- SNMPv3 for the fast sources (USM user, auth/priv), for networks that turned v2c off.
- Show which switch port each AP is connected to on the map (the API doesn't expose it yet).
- Change indication in the topology card, only for meaningful events (not every RSSI/rate update,
  those change for almost all clients every ~90 s): client connected (briefly highlighted),
  disconnected (stays greyed out for a few seconds; the card re-renders fully, so it has to keep
  removed clients), roamed (highlighted at the new AP, greyed at the old one), AP/port/internet
  status changes, signal only when it crosses a colour step. Reuse the coordinator's connect/
  disconnect/roam detection. Estimated about half a day.

The maintainer may keep a local, git-ignored `LIVING_DOCUMENT.md` with private project notes;
if it exists, read it first and keep it up to date.
