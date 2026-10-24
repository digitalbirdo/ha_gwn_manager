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
- Per-client sensors: signal strength, access point, SSID, band, channel, last seen, IP address
  (disabled by default: connected since, link rates, traffic, VLAN)
- Clients are linked to the access point they are connected to ("Connected via"), following roaming
- **Area** sensor per client: the area of its current access point (assign areas to your APs for room-level presence)
- **Network access switch** per client (block / unblock), disabled by default
- Per access point / switch / router: online state, connected clients, uptime, firmware, IP address,
  temperature, CPU usage, channel utilization per band (disabled by default: memory, channel per band);
  sensors are only created for values the device model reports
- Timestamps are interpreted in the time zone configured for the GWN network
- **Throughput** per client (download / upload), derived from the byte counters
- **Connection events** – connected, disconnected, roamed – on the event bus
  (`gwn_manager_client_connected`, `gwn_manager_client_disconnected`, `gwn_manager_client_roamed`),
  in the logbook and as event entity per client (disabled by default)
- **Switches**: link and PoE state per port, port link speed and throughput (disabled by default),
  total PoE power, CPU and memory
- **WAN** of the gateway: internet connectivity, download/upload throughput, WAN IP (disabled by default)
- **Firmware update** entity per device (installs the version GWN Manager recommends) and a **restart** button
- **SSIDs** as devices with the number of connected clients and a **Wi-Fi QR code** image
  (contains the password, so disabled by default)
- New clients and devices are added automatically
- Works with a local GWN Manager (self-signed certificates supported) and GWN.Cloud
- Diagnostics download with credentials redacted

## Network map card

The integration ships a live, interactive network map card – no extra resource needed.
Access points hang off the GWN Manager, clients off their access point; link length and
colour follow the signal strength, and clients float over when they roam.

```yaml
type: custom:gwn-network-card
title: Netzwerk          # optional
height: 450              # px
show_offline: false      # also show disconnected clients
show_labels: true        # show client names (false: only on hover)
animate: true            # gentle floating of the bubbles
color_by: rssi           # rssi or band (2.4 / 5 / 6 GHz); can also be switched on the card
appearance: auto         # auto (follows the Home Assistant dark mode), dark or light
layout: auto             # floors, free or auto (floors as soon as an AP's area has a floor)
```

Assign your access points to areas and the areas to floors, and the map arranges the APs by
floor (highest floor at the top). The view zooms to fit automatically. Drag bubbles around, scroll to zoom, drag the background
to pan, double-click to fit again. Client names follow the device names you set in Home Assistant.
Hover for details, click to open the device tracker / AP status.

## Network topology card

A structured view in the style of UniFi dashboards: access points with clients per band,
switches with their ports (speed, PoE, what is connected), the internet gateway with WAN
throughput, and uplink wires between them.

```yaml
type: custom:gwn-topology-card
title: Mein Netzwerk     # optional
appearance: auto         # auto, dark or light
show_all_ports: false    # also show switch ports without link
uplinks:                 # which port each device is plugged into – the GWN API doesn't report it
  Living Room AP: Switch:17  # "<device>:<port>", or only the port number if there is one switch
  Office AP: 21
  Switch: Gateway:LAN1
```

Click a device to open its status.

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

- **Presence update interval** (default 10 s, min 5 s): how often the list of connected clients is
  fetched. Connects and disconnects show up in GWN Manager right away.
- **Consider away after** (default 180 s): a client is only reported away after being disconnected
  this long, so phones briefly dropping Wi-Fi in standby don't flap. Arrivals are reported immediately.
- **Update interval for all other data** (default 90 s): offline clients, devices and device details.
  GWN Manager itself refreshes signal strength and other measurements only about every 90 s.
- **Only track clients of these SSIDs** (default: all)
- **Track offline clients**: create entities for all clients known to GWN Manager, not only for currently connected ones

## API notes

- Uses the GWN open API (`/oapi/v1.0.0`), see the [API documentation](https://doc.grandstream.dev/GWN-API/EN/).
- `client/list` with `untilNow: 1` returns all known clients without live values;
  `untilNow: 0` returns only connected clients but with RSSI, link rates, channel, etc.
  The integration merges both.
- Endpoints without parameters must be called without a request body; a signed `{}` is
  rejected with `Authentication Failure`.

## Development

See [AGENTS.md](AGENTS.md) for the architecture, GWN API notes, conventions and how to run a
local Home Assistant test instance (`scripts/`).

## Releasing

1. Bump `version` in `custom_components/gwn_manager/manifest.json`
2. Create a GitHub release with the matching tag, e.g. `v0.2.1`

HACS installs the integration directly from the tagged sources. (Zip release assets are not
used: GitHub's release download redirect currently sends a header larger than aiohttp
accepts, so HACS cannot download them.)

## Events

Example automation trigger:

```yaml
triggers:
  - trigger: event
    event_type: gwn_manager_client_roamed
    event_data:
      mac: "AA:BB:CC:DD:EE:FF"
```

Event data: `mac`, `name`, `ip`, `ssid`, `ap_mac`, `ap_name` and, for roaming,
`previous_ap_mac` / `previous_ap_name`. Disconnects are reported immediately; the device
tracker applies *Consider away after*.

## Limitations

- PoE can't be switched per port: the GWN API has no PoE setting for switch ports.
- Throughput and other measurements are refreshed by GWN Manager about every 90 seconds.
