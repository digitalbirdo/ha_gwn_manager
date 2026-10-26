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
# Lines "<client name or MAC> = <device name or MAC>[:<port>]"
CONF_MANUAL_UPLINKS = "manual_uplinks"

# Fired on the event bus, also shown in the logbook.
EVENT_CLIENT_CONNECTED = f"{DOMAIN}_client_connected"
EVENT_CLIENT_DISCONNECTED = f"{DOMAIN}_client_disconnected"
EVENT_CLIENT_ROAMED = f"{DOMAIN}_client_roamed"

# Byte counters of connected clients are refreshed by GWN Manager about every
# 90 s. If they did not change for this long, the client is idle.
THROUGHPUT_IDLE_AFTER = 240

# Fast sources: read the devices directly (syslog push, SNMP poll) on top of the API.
CONF_FAST_SYSLOG = "fast_syslog"
CONF_SYSLOG_PORT = "syslog_port"
# Also evaluate the JSON events the devices only send at syslog level debug.
CONF_SYSLOG_DEBUG_EVENTS = "syslog_debug_events"
# Keep the events and signal history of the client diagnosis over restarts (off: memory only).
CONF_PERSIST_DIAGNOSIS = "persist_diagnosis"
CONF_FAST_SNMP = "fast_snmp"
CONF_SNMP_COMMUNITY = "snmp_community"
CONF_SNMP_INTERVAL = "snmp_interval"
DEFAULT_SYSLOG_PORT = 5514
DEFAULT_SNMP_COMMUNITY = "public"
# One SNMP poll of all devices takes ~10-15 s (the agents only answer GETNEXT).
DEFAULT_SNMP_INTERVAL = 10
MIN_SNMP_INTERVAL = 5
MAX_SNMP_INTERVAL = 60

# Remove clients (devices + entities) after this many days without a connection; 0 = never.
CONF_REMOVE_AFTER_DAYS = "remove_after_days"
DEFAULT_REMOVE_AFTER_DAYS = 0

# Forward every received syslog datagram unchanged to these "ip:port" targets (comma separated),
# so other tools can analyse the same stream.
CONF_SYSLOG_FORWARD = "syslog_forward"
