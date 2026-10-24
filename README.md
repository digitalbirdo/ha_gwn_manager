# GWN Manager for Home Assistant

Custom integration for **Grandstream GWN Manager** (self-hosted) and **GWN.Cloud**,
focused on the **clients** of your network: presence detection and per-client details
for every device connected to your GWN access points.

> Not affiliated with or endorsed by Grandstream.
> Looking for control of SSIDs / devices? Have a look at
> [Grandstream GWN Manager Bridge](https://github.com/shopsD/Grandstream-GWN-HomeAssistant-MQTT);
> both integrations can be installed side by side.

## Features

- **Device tracker** per client (`home` / `not_home`), usable for presence and the `person` integration
- Per-client sensors: signal strength, access point, SSID, band, last seen, IP address
  (disabled by default: connected since, channel, link rates, traffic, VLAN)
- Clients are linked to the access point they are connected to ("Connected via"), following roaming
- **Area** sensor per client: the area of its current access point (assign areas to your APs for room-level presence)
- **Network access switch** per client (block / unblock), disabled by default
- Per access point / switch / router: online state, connected clients, uptime, firmware, IP address,
  temperature, CPU usage, channel utilization per band (disabled by default: memory, channel per band);
  sensors are only created for values the device model reports
- Timestamps are interpreted in the time zone configured for the GWN network
- New clients and devices are added automatically
- Works with a local GWN Manager (self-signed certificates supported) and GWN.Cloud
- Diagnostics download with credentials redacted

## Installation

### HACS (custom repository)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=digitalbirdo&repository=ha_gwn_manager&category=integration)

1. HACS → ⋮ → *Custom repositories* → add `https://github.com/digitalbirdo/ha_gwn_manager`, type *Integration*
2. Install **GWN Manager** and restart Home Assistant

### Manual

Copy `custom_components/gwn_manager` into your `config/custom_components/` folder and restart Home Assistant.

## Configuration

1. In GWN Manager / GWN.Cloud enable **System → API Developer Mode** and create an App ID and secret.
2. In Home Assistant: **Settings → Devices & services → Add integration → GWN Manager**
3. Enter
   - **Server URL**: your GWN Manager, e.g. `https://192.168.1.10:8443`, or your GWN.Cloud region, e.g. `https://eu.gwn.cloud`
   - **App ID** and **App secret**
   - **Verify SSL certificate**: turn off for a GWN Manager with a self-signed certificate

### Options

- **Update interval** (default 30 s)
- **Track offline clients**: create entities for all clients known to GWN Manager, not only for currently connected ones

## API notes

- Uses the GWN open API (`/oapi/v1.0.0`), see the [API documentation](https://doc.grandstream.dev/GWN-API/EN/).
- `client/list` with `untilNow: 1` returns all known clients without live values;
  `untilNow: 0` returns only connected clients but with RSSI, link rates, channel, etc.
  The integration merges both.
- Endpoints without parameters must be called without a request body; a signed `{}` is
  rejected with `Authentication Failure`.

## Releasing

1. Bump `version` in `custom_components/gwn_manager/manifest.json`
2. Create a GitHub release with the matching tag, e.g. `v0.2.1`

HACS installs the integration directly from the tagged sources. (Zip release assets are not
used: GitHub's release download redirect currently sends a header larger than aiohttp
accepts, so HACS cannot download them.)
