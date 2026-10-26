# Fast Sources (SNMP + Syslog) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect client connect/disconnect/roaming within seconds and refresh signal/rate every few seconds by reading the APs directly (syslog push + SNMP poll), with the GWN API staying the source for everything else.

**Architecture:** Two optional, independent "fast sources" feed a small in-memory overlay (`FastState`) that the coordinator merges on top of the API snapshot. Syslog is a UDP listener started by the integration that parses only a handful of hostapd/DHCP lines. SNMP is an async poller per device with model autodetection (AP MIB vs router MIB). Parsers are pure functions in their own modules, unit-tested without Home Assistant.

**Tech Stack:** Python 3.13, Home Assistant ≥ 2026.8, `pysnmp` 7.1.x (async hlapi v3arch), asyncio datagram endpoint, pytest.

**Spec:** Findings from the 2026-10-25 session, recorded in the local `LIVING_DOCUMENT.md` (sections "SNMP-Erkundung" and "Syslog"). The values this plan relies on are copied into Global Constraints below.

## Global Constraints

- Both fast sources are **off by default**; options: `fast_snmp` (bool), `snmp_community` (str, default `public`), `snmp_interval` (s, default 10, min 5, max 60), `fast_syslog` (bool), `syslog_port` (int, default 5514, 1–65535), `syslog_debug_events` (bool, default off: also evaluate the debug-level `gseventlog` JSON), `card_flash` (card option, default on).
- Debug-level JSON (only sent when the GWN syslog level is debug, ~4× the traffic): `controller: process_send_events: gseventlog send {json}`; `event_subtype` 0 = disconnected (`client_mac`, `ap_mac`, `ssid`, `frequency band`), 1 = connected (+ `channel`, `rssi` dBm, `tx_rate`, `rx_rate`, `new_sta_mode`), 21 = client info (`hostname`, `os`, `ipv4`). MACs without colons. RSSI comes only at connect time, not continuously – continuous signal stays with SNMP.
- SNMPv3 is out of scope for this plan (v2c only); note it in "Open ideas".
- AP MIB (GWN76xx): client table `1.3.6.1.4.1.42397.1.1.3.3.1`; columns: 2 = MAC (`aa:bb:..`), 3 = IP, 4 = BSSID, 5 = SSID, 6 = SNR (dB), 9 = hostname, 11 = rate (Mbit/s).
- Router MIB (GWN70xx): client table `1.3.6.1.4.1.42397.1.2.1.3.1`; columns: 2 = MAC (`aabbccddeeff`, no colons), 3 = BSSID, 4 = IP, 6 = band (`2.4GHZ`/`5GHZ`), 7 = SSID, 8 = RSSI (dBm), 11 = hostname.
- Autodetect: GETNEXT on `…42397.1.1.3.3` → AP; else on `…42397.1.2.1.3` → router; else no SNMP for that device.
- Only GETNEXT walks are reliable on these agents; GETBULK times out and GET-by-index returns wrong columns. Walk column by column; a full client-table walk of 9 clients takes ~1–2 s per column.
- AP SNR → dBm for the existing `rssi` field: `rssi = snr - 95` (noise floor assumption); expose raw value as `snr` attribute.
- Syslog lines to use (level notice suffices): `<ifname>: AP-STA-CONNECTED <mac>`, `<ifname>: AP-STA-DISCONNECTED <mac>`, `Sta is freed: sta=<mac>, bssid=<bssid>, if=<ifname>`, `sta is authenticated: mac=<mac>, bssid=<bssid>`, router `DHCPACK(<if>) <ip> <mac> [<hostname>]`. Sender identified by source IP → device via `aps[mac]["ip"]`, fallback the `[<mac12>]` tag after the model in the message.
- Expect ~40 lines/s at notice and ~150/s at debug; the listener must drop non-matching lines with one cheap substring check before any regex.
- No credentials, IPs or MACs from the private network in the repo (tests use made-up values).

## Review Focus

- Syslog sender not in `aps` (e.g. a new AP, or the switch) → line ignored, no exception, counted in diagnostics.
- SNMP table shifts mid-walk (client leaves between column walks) → rows with mismatched/empty BSSID are dropped, never applied.
- Port 5514 already in use / no permission → integration still loads, repair issue `syslog_port_unavailable` is raised, API polling continues.
- Debug events enabled but GWN level is notice → no JSON lines arrive; nothing breaks, diagnostics shows `debug_lines: 0` so the user sees why.
- Fast source says "connected" but API later says not connected for longer than `away_delay` → API wins (no stuck "home").
- Option toggled off at runtime → listener closed and pollers cancelled on entry reload; no leaked socket (second reload binds again).

---

### Task 1: Pure parsers for syslog lines

**Files:**
- Create: `custom_components/gwn_manager/fast/syslog_parse.py`, `custom_components/gwn_manager/fast/__init__.py` (empty)
- Create: `tests/test_syslog_parse.py`, `tests/conftest.py` (adds repo root to `sys.path`), `requirements_test.txt` (`pytest`)

**Interfaces:**
- Produces: `@dataclass(frozen=True) class SyslogEvent: kind: Literal["connected","disconnected","dhcp","info"]; client_mac: str; ifname: str | None = None; bssid: str | None = None; ip: str | None = None; hostname: str | None = None; device_tag: str | None = None; ap_mac: str | None = None; rssi: int | None = None; rate: int | None = None; channel: int | None = None; sta_mode: str | None = None; os: str | None = None; debug: bool = False`
- Produces: `parse_syslog_line(line: str, debug_events: bool = False) -> SyslogEvent | None` — gseventlog lines return `None` unless `debug_events`; then subtype 0/1/21 → disconnected/connected/info with `debug=True` — MACs upper-case with colons; `device_tag` = upper-case 12-hex from `GWNxxxx[<tag>]`.

- [ ] **Step 1: Write failing tests**

```python
def test_connected():
    e = parse_syslog_line("<29>Oct 25 13:14:47 GWN7665[ec74d7000001][1.0.27.20][AP X] daemon.notice: hostapd[1]: [.6]: ath3: AP-STA-CONNECTED 4e:ef:65:00:00:01")
    assert e == SyslogEvent("connected", "4E:EF:65:00:00:01", ifname="ath3", device_tag="EC74D7000001")

def test_disconnected(): ...  # AP-STA-DISCONNECTED -> kind "disconnected"
def test_freed_has_bssid(): ...  # "Sta is freed: sta=..., bssid=ec:74:..:ab, if=ath3" -> disconnected, bssid set
def test_dhcpack_with_hostname(): ...  # "DHCPACK(br-lan) 192.0.2.9 4e:..:01 iPhone" -> dhcp, ip, hostname "iPhone"
def test_dhcpack_without_hostname(): ...  # hostname None
def test_gseventlog_connect_only_when_enabled():
    line = '<15>... GWN7665[ec74d7000001][1.0.27.20][AP X] daemon.debug: controller: process_send_events: gseventlog send { "event_content": { "client_mac": "4eef65000001", "ap_mac": "ec74d7000001", "ssid": "S", "status": 1, "channel": 85, "rssi": -66, "tx_rate": 432, "rx_rate": 432, "new_sta_mode": "11AXA_HE80", "frequency band": 6 }, "event_subtype": 1 }'
    assert parse_syslog_line(line) is None
    e = parse_syslog_line(line, debug_events=True)
    assert (e.kind, e.client_mac, e.rssi, e.rate, e.channel, e.debug) == ("connected", "4E:EF:65:00:00:01", -66, 432, 85, True)

def test_gseventlog_info_and_disconnect(): ...  # subtype 21 -> "info" with hostname/os/ip; subtype 0 -> "disconnected"
def test_gseventlog_truncated_json_returns_none(): ...

def test_noise_returns_none():
    assert parse_syslog_line("... hostapd[1]: receive nbr msg roam cache update for sta 4e:..") is None
    assert parse_syslog_line("") is None
    assert parse_syslog_line("\xff garbage") is None
```

- [ ] **Step 2:** Run `pytest tests/test_syslog_parse.py -v` → FAIL (module missing).
- [ ] **Step 3:** Implement. Fast pre-filter: return `None` unless one of `"AP-STA-"`, `"Sta is freed"`, `"DHCPACK"` (or `"gseventlog send"` when `debug_events`) is in the line; JSON part via `json.loads` of the text after `send `; then one compiled regex per kind.
- [ ] **Step 4:** Run tests → PASS.
- [ ] **Step 5:** Commit `feat: syslog line parser for client events`.

### Task 2: Pure SNMP row assembly

**Files:**
- Create: `custom_components/gwn_manager/fast/snmp_parse.py`
- Test: `tests/test_snmp_parse.py`

**Interfaces:**
- Produces: `class MibVariant(StrEnum): AP = "ap"; ROUTER = "router"`
- Produces: `COLUMNS: dict[MibVariant, dict[str, int]]` with the Global Constraints column numbers under keys `mac, ip, bssid, ssid, signal, hostname, rate` (router: `band` instead of `rate`).
- Produces: `@dataclass class SnmpClient: mac: str; bssid: str | None; ip: str | None; ssid: str | None; rssi: int | None; snr: int | None; rate: int | None; band: str | None; hostname: str | None`
- Produces: `build_clients(variant: MibVariant, columns: dict[str, dict[str, str]]) -> dict[str, SnmpClient]` — input is column-name → {row index → value}; output keyed by normalized MAC.

- [ ] **Step 1: Write failing tests**

```python
def test_ap_rows_snr_to_rssi():
    cols = {"mac": {"1": "aa:bb:cc:00:00:01"}, "bssid": {"1": "ec:74:d7:00:00:ab"}, "signal": {"1": "34"}, "rate": {"1": "600"}, ...}
    c = build_clients(MibVariant.AP, cols)["AA:BB:CC:00:00:01"]
    assert (c.snr, c.rssi, c.rate) == (34, -61, 600)

def test_router_mac_without_colons_and_dbm():
    # mac "aabbcc000001", signal "-22", band "2.4GHZ" -> key "AA:BB:CC:00:00:01", rssi -22, snr None, band "2.4G"
def test_row_with_empty_bssid_dropped(): ...      # table shifted mid-walk
def test_row_missing_from_mac_column_dropped(): ...
def test_non_numeric_signal_gives_none(): ...
```

- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement; band for AP rows derived later from BSSID (Task 4), so `band=None` here.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat: SNMP client table assembly for AP and router MIBs`.

### Task 3: SNMP poller with autodetect

**Files:**
- Create: `custom_components/gwn_manager/fast/snmp.py`
- Modify: `custom_components/gwn_manager/manifest.json` (add `pysnmp` pinned to the version HA core's `snmp` integration pins at implementation time)
- Test: `tests/test_snmp_poller.py` (walk function injected as a fake)

**Interfaces:**
- Consumes: `MibVariant`, `COLUMNS`, `build_clients`, `SnmpClient` (Task 2).
- Produces: `class SnmpPoller: __init__(self, host: str, community: str, walk: WalkFn | None = None)`; `async detect(self) -> MibVariant | None`; `async poll(self) -> dict[str, SnmpClient]`; `variant: MibVariant | None`.
- `WalkFn = Callable[[str, str, str], Awaitable[dict[str, str]]]` = `(host, community, column_oid) -> {row index: value}`; default implementation uses pysnmp `next_cmd` in a loop (GETNEXT only, timeout 5 s, retries 0, `lookupMib=False`), one `SnmpEngine` per poller.

- [ ] **Step 1:** Tests: `detect` returns AP when the fake answers under `…1.1.3.3`, ROUTER when only `…1.2.1.3`, `None` when neither; `poll` walks only the columns of its variant; a walk raising `TimeoutError` makes `poll` raise `SnmpUnavailable`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement; walk columns sequentially (agent is single-threaded, parallel walks slow it down).
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat: SNMP poller with AP/router autodetect`.

### Task 4: FastState overlay and coordinator merge

**Files:**
- Create: `custom_components/gwn_manager/fast/state.py`
- Modify: `custom_components/gwn_manager/coordinator.py` (`_fetch`, after `_apply_ap_clients`, before `_detect_events`)
- Test: `tests/test_fast_state.py`

**Interfaces:**
- Consumes: `SyslogEvent` (Task 1), `SnmpClient` (Task 2).
- Produces: `class FastState: apply_syslog(self, ap_mac: str, event: SyslogEvent, now: datetime) -> bool` (True if state changed); `apply_snmp(self, ap_mac: str, clients: dict[str, SnmpClient], now: datetime) -> bool`; `overlay(self, now: datetime, max_age: timedelta) -> dict[str, dict[str, Any]]` returning per client MAC the keys the coordinator already uses: `connected`, `apId`, `apName` (filled by coordinator), `ssid`, `channelClassStr`, `rssi`, plus new `snr`, `rate`, `bssid`, `source` (`"syslog"`/`"snmp"`).
- Band from BSSID: build `bssid → band` from SNMP AP rows (`ath*` interface order is not stable, so BSSID wins); fall back to the API's `channelClassStr`.
- Coordinator: `self.fast: FastState | None`; in `_fetch`, for each overlay entry younger than `max_age` (= 3 × `snmp_interval`, or 30 s for syslog-only), `data.clients[mac].update(entry)` and `self._last_connected[mac] = now` when connected. A syslog "disconnected" sets `connected=False` immediately; the existing `away_delay` logic in `device_tracker` stays as is.

- [ ] **Step 1:** Tests: connected-then-disconnected via syslog → `connected` False; SNMP update after syslog connect keeps AP and adds rssi; roam (connected on AP B while on AP A) → apId B, no disconnect in between; entries older than `max_age` are not returned; SNMP poll of AP A that no longer lists the client does **not** disconnect it if syslog saw it connect on AP B more recently.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement `FastState`; then the coordinator merge.
- [ ] **Step 4:** Run tests → PASS; start the test instance (`scripts/dev-instance.sh run`) and confirm it loads with fast sources off (no behavior change).
- [ ] **Step 5:** Commit `feat: merge fast-source overlay into coordinator`.

### Task 5: Syslog listener and SNMP scheduling in the integration lifecycle

**Files:**
- Create: `custom_components/gwn_manager/fast/syslog.py`
- Modify: `custom_components/gwn_manager/__init__.py` (start/stop on setup/unload), `coordinator.py` (resolve sender IP → AP MAC; trigger `async_set_updated_data` on change, debounced 1 s)
- Test: `tests/test_syslog_listener.py` (real UDP on an ephemeral port)

**Interfaces:**
- Consumes: `parse_syslog_line` (Task 1), `SnmpPoller` (Task 3), `FastState` (Task 4).
- Produces: `async start_syslog(port: int, on_event: Callable[[str, SyslogEvent], None]) -> asyncio.DatagramTransport` (callback gets sender IP); raises `OSError` when the port can't be bound.
- SNMP: one `async_track_time_interval` per entry at `snmp_interval`; each tick polls all detected devices concurrently (`asyncio.gather`, per-device failures logged once and counted). Detection runs after each full API update for devices not yet detected.

- [ ] **Step 1:** Test: send three datagrams (one connect, one noise, one non-UTF-8) to the bound port → callback called exactly once with the sender IP; closing the transport frees the port (bind again succeeds).
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement; on `OSError` create repair issue `syslog_port_unavailable` (translation keys in `en.json`/`de.json`) and continue without syslog.
- [ ] **Step 4:** Run tests → PASS. In the test instance with both options on: toggle Wi-Fi on a phone and confirm the device tracker changes within ~2 s and the logbook shows the event.
- [ ] **Step 5:** Commit `feat: syslog listener and SNMP polling lifecycle`.

### Task 6: Options, diagnostics, docs

**Files:**
- Modify: `const.py` (option keys/defaults from Global Constraints), `config_flow.py` (options form), `translations/en.json`, `translations/de.json`, `diagnostics.py`, `README.md`, `AGENTS.md`, `manifest.json` (`iot_class` → `local_push`, version bump)

**Interfaces:**
- Consumes: everything above.
- Diagnostics adds `fast_sources`: per device `{"snmp": "ap"|"router"|null, "snmp_errors": int, "syslog_lines": int, "syslog_ignored": int, "last_syslog": iso}`; client MACs redacted as today.

- [ ] **Step 1:** Add options and translations; README section "Fast updates": how to enable SNMP v2c and syslog (target = HA host, port 5514, level notice) in GWN Manager, and that the API cannot switch them on.
- [ ] **Step 2:** Run `python -m json.tool` on both translation files and the hassfest script from `AGENTS.md` → no errors.
- [ ] **Step 3:** Test instance: options form shows the new fields in both languages; diagnostics download contains `fast_sources`.
- [ ] **Step 4:** Commit `feat: options and diagnostics for fast sources`.

### Task 7: Card flash on fast-source changes

**Files:**
- Modify: `custom_components/gwn_manager/websocket.py` (topology payload gets per client `changed_at` (ISO) and `change` = `connected`|`roamed`|`signal`|`disconnected`), `coordinator.py` (record them in `_detect_events` and on fast-source updates; `signal` only when the RSSI colour step changes, same steps as the card), `frontend/gwn-network-card.js`, `frontend/gwn-topology-card.js`, both cards' version constants

**Interfaces:**
- Consumes: coordinator events (existing) and `FastState` changes (Task 4).
- Card option `card_flash` (default `true`).
- Look: a very light flash – the client's bubble/row gets a soft highlight (accent colour, opacity ≈ 0.25) that fades out over 1.5 s via CSS `@keyframes`; disconnected clients stay greyed for 5 s before they disappear (keep them in a local `removed` map, since the card re-renders fully). Respect `prefers-reduced-motion` (no animation, only a 1.5 s static outline).
- Only flash when `changed_at` is newer than the last value the card saw for that client (no flash on first load or re-subscribe).

- [ ] **Step 1:** `node --check` both cards; dev instance with syslog on: toggle Wi-Fi on a phone → its bubble flashes once on disconnect (grey, then gone after 5 s) and once on connect; no flash for other clients; reload the page → no flash.
- [ ] **Step 2:** Same with `card_flash: false` → no flash; with reduced motion emulated in Playwright → outline only.
- [ ] **Step 3:** Commit `feat: cards flash clients that just changed`.
