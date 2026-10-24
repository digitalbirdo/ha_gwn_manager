"""Constants for the GWN Manager integration."""

DOMAIN = "gwn_manager"
MANUFACTURER = "Grandstream"

CONF_APP_ID = "app_id"
CONF_APP_SECRET = "app_secret"
CONF_SERVER_URL = "server_url"
CONF_TRACK_OFFLINE = "track_offline_clients"
CONF_PRESENCE_INTERVAL = "presence_interval"
CONF_AWAY_DELAY = "away_delay"

DEFAULT_SERVER_URL = "https://eu.gwn.cloud"
# Full update: all clients incl. offline, devices, device details. The GWN
# Manager itself refreshes measurements (RSSI, rates, ...) only every ~90 s.
DEFAULT_SCAN_INTERVAL = 90
MIN_SCAN_INTERVAL = 30
# Presence update: only the list of connected clients. (Dis)connects show up
# in the GWN Manager right away.
DEFAULT_PRESENCE_INTERVAL = 10
MIN_PRESENCE_INTERVAL = 5
# A client is only reported away after being disconnected this long, so
# phones briefly dropping Wi-Fi in standby don't flap.
DEFAULT_AWAY_DELAY = 180
DEFAULT_TRACK_OFFLINE = True

CONF_SSID_FILTER = "ssid_filter"

# Fired on the event bus, also shown in the logbook.
EVENT_CLIENT_CONNECTED = f"{DOMAIN}_client_connected"
EVENT_CLIENT_DISCONNECTED = f"{DOMAIN}_client_disconnected"
EVENT_CLIENT_ROAMED = f"{DOMAIN}_client_roamed"

# Byte counters of connected clients are refreshed by GWN Manager about every
# 90 s. If they did not change for this long, the client is idle.
THROUGHPUT_IDLE_AFTER = 240
