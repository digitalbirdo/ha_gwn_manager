"""Run the fast sources (syslog listener, SNMP pollers) for one config entry."""

from __future__ import annotations

import asyncio
from collections import Counter
import contextlib
from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components import network
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from ..const import (
    CONF_FAST_SNMP,
    CONF_FAST_SYSLOG,
    CONF_SNMP_COMMUNITY,
    CONF_SNMP_INTERVAL,
    CONF_SYSLOG_DEBUG_EVENTS,
    CONF_SYSLOG_FORWARD,
    CONF_SYSLOG_PORT,
    DEFAULT_SNMP_COMMUNITY,
    DEFAULT_SNMP_INTERVAL,
    DEFAULT_SYSLOG_PORT,
    DOMAIN,
)
from .snmp import SnmpPoller, SnmpUnavailable, build_engine
from .state import FastState
from .syslog import parse_forward_targets, start_syslog
from .syslog_parse import SyslogEvent

if TYPE_CHECKING:
    from ..coordinator import GWNConfigEntry, GWNCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_own_addresses(hass: HomeAssistant) -> set[str]:
    """IPv4 addresses of this host, to refuse syslog forwarding to itself."""
    adapters = await network.async_get_adapters(hass)
    return {
        ip["address"]
        for adapter in adapters
        for ip in adapter["ipv4"]
        if ip.get("address")
    }
# Changes arriving within this time are published together.
DEBOUNCE = 1.0
# Devices without usable SNMP are asked again after this time.
REDETECT = timedelta(minutes=10)


class FastSources:
    """Feeds syslog events and SNMP polls into the coordinator's FastState."""

    def __init__(self, hass: HomeAssistant, entry: GWNConfigEntry, coordinator: GWNCoordinator) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        options = entry.options
        self.syslog_enabled = options.get(CONF_FAST_SYSLOG, False)
        self.snmp_enabled = options.get(CONF_FAST_SNMP, False)
        self.port = options.get(CONF_SYSLOG_PORT, DEFAULT_SYSLOG_PORT)
        self.debug_events = options.get(CONF_SYSLOG_DEBUG_EVENTS, False)
        self.forward: list[tuple[str, int]] = []
        self.community = options.get(CONF_SNMP_COMMUNITY, DEFAULT_SNMP_COMMUNITY)
        self.interval = options.get(CONF_SNMP_INTERVAL, DEFAULT_SNMP_INTERVAL)
        self._transport: asyncio.DatagramTransport | None = None
        self._task: asyncio.Task | None = None
        self._pending: CALLBACK_TYPE | None = None
        self._engine: Any = None
        self._pollers: dict[str, SnmpPoller] = {}  # device MAC -> poller
        self._undetected: dict[str, Any] = {}  # device MAC -> time of the failed detection
        self.stats: Counter[str] = Counter()
        self.syslog_by_device: Counter[str] = Counter()
        self.snmp_errors: Counter[str] = Counter()
        self.last_syslog: str | None = None
        # Last syslog event that only arrives at level debug (the diagnosis needs them).
        self.debug_seen: datetime | None = None
        # Last SNMP round with at least one answering device, and its result.
        self.last_snmp: datetime | None = None
        self.snmp_ok = 0

    @property
    def enabled(self) -> bool:
        return self.syslog_enabled or self.snmp_enabled

    async def async_start(self) -> None:
        if not self.enabled:
            return
        self.coordinator.fast = FastState()
        # Keep fast values a few poll rounds (a round takes ~10-15 s plus the interval).
        self.coordinator.fast_max_age = timedelta(
            seconds=max(30, 3 * (self.interval + 15)) if self.snmp_enabled else 30
        )
        try:
            self.forward = parse_forward_targets(
                self.entry.options.get(CONF_SYSLOG_FORWARD),
                self.port,
                await async_own_addresses(self.hass),
            )
        except ValueError as err:
            _LOGGER.warning("Ignoring syslog forwarding targets: %s", err)
        issue_id = f"syslog_port_unavailable_{self.entry.entry_id}"
        if self.syslog_enabled:
            try:
                self._transport = await start_syslog(
                    self.port,
                    self._on_syslog,
                    debug_events=self.debug_events,
                    forward=self.forward,
                )
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            except OSError as err:
                _LOGGER.warning("Syslog port %s unavailable: %s", self.port, err)
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    issue_id,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key="syslog_port_unavailable",
                    translation_placeholders={"port": str(self.port), "error": str(err)},
                )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
        if self.snmp_enabled:
            self._engine = await self.hass.async_add_executor_job(build_engine)
            self._task = self.entry.async_create_background_task(
                self.hass, self._snmp_loop(), f"{DOMAIN} SNMP"
            )

    async def async_stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._engine is not None:
            # Closes the engine's UDP socket; a new one is built on the next start.
            self._engine.close_dispatcher()
            self._engine = None
        if self._pending is not None:
            self._pending()
            self._pending = None
        self.coordinator.fast = None

    def _device_for(self, ip: str, tag: str | None) -> str | None:
        """Device MAC for a syslog sender: by IP, else by the MAC in the message."""
        aps = self.coordinator.data.aps if self.coordinator.data else {}
        for mac, ap in aps.items():
            if ip in (ap.get("ipv4"), ap.get("ip")):
                return mac
        if tag:
            mac = ":".join(tag[i : i + 2] for i in range(0, 12, 2))
            if mac in aps:
                return mac
        return None

    @callback
    def _on_syslog(self, ip: str, event: SyslogEvent) -> None:
        self.stats["syslog_events"] += 1
        if event.debug:
            self.stats["syslog_debug_events"] += 1
        device = self._device_for(ip, event.device_tag)
        if device is None:
            self.stats["syslog_ignored"] += 1
            return
        self.syslog_by_device[device] += 1
        self.last_syslog = dt_util.utcnow().isoformat()
        # The debug JSON events (gseventlog) don't pair up with the hostapd lines: leave them out.
        if event.kind == "diag" or (event.kind in ("connected", "disconnected") and not event.debug):
            self.coordinator.diagnosis.add(
                event.client_mac,
                {
                    "type": event.diag or event.kind,
                    "ap": device,
                    "ifname": event.ifname,
                    "bssid": event.bssid,
                    "from_bssid": event.from_bssid,
                    "reason_code": event.reason_code,
                    "status_code": event.status_code,
                    "result": event.result,
                    "snr": event.snr,
                    "detail": event.detail,
                    "rssi": event.rssi,
                    "channel": event.channel,
                },
                dt_util.utcnow(),
            )
            if event.debug:
                self.debug_seen = dt_util.utcnow()
        if self.coordinator.fast and self.coordinator.fast.apply_syslog(device, event, dt_util.utcnow()):
            self._schedule_update()

    @callback
    def _schedule_update(self) -> None:
        if self._pending is not None:
            return

        @callback
        def _publish(_now: Any) -> None:
            self._pending = None
            self.coordinator.async_fast_update()

        self._pending = async_call_later(self.hass, DEBOUNCE, _publish)

    async def _snmp_loop(self) -> None:
        while True:
            try:
                await self._snmp_round()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Keep polling: one odd round must not stop SNMP until a reload.
                _LOGGER.exception("SNMP round failed")
            await asyncio.sleep(self.interval)

    async def _snmp_round(self) -> None:
        await self._detect_devices()
        started = dt_util.utcnow()
        pollers = list(self._pollers.items())
        results = await asyncio.gather(*(p.poll() for _, p in pollers), return_exceptions=True)
        changed = False
        ok = 0
        for (device, _), result in zip(pollers, results, strict=True):
            if isinstance(result, SnmpUnavailable):
                if not self.snmp_errors[device]:
                    _LOGGER.info("SNMP of %s unavailable: %s", device, result)
                self.snmp_errors[device] += 1
                continue
            if isinstance(result, BaseException):
                _LOGGER.error("SNMP poll of %s failed: %r", device, result)
                continue
            ok += 1
            if self.coordinator.fast and self.coordinator.fast.apply_snmp(
                device, result, dt_util.utcnow(), started
            ):
                changed = True
        self.snmp_ok = ok
        if ok:
            self.last_snmp = dt_util.utcnow()
        if changed:
            self._schedule_update()

    async def _detect_devices(self) -> None:
        aps = self.coordinator.data.aps if self.coordinator.data else {}
        now = dt_util.utcnow()
        candidates: dict[str, SnmpPoller] = {}
        for mac, ap in aps.items():
            ip = ap.get("ipv4") or ap.get("ip")
            if not ip or mac in self._pollers:
                continue
            if (failed := self._undetected.get(mac)) and now - failed < REDETECT:
                continue
            candidates[mac] = SnmpPoller(ip, self.community, engine=self._engine)
        results = await asyncio.gather(*(p.detect() for p in candidates.values()))
        for (mac, poller), variant in zip(candidates.items(), results, strict=True):
            if variant:
                self._pollers[mac] = poller
                self._undetected.pop(mac, None)
            else:
                self._undetected[mac] = now

    def status(self) -> dict[str, Any]:
        """What the cards show: is each source on, and when did it last deliver."""
        last_line = self._transport.get_protocol().last_line if self._transport else None
        return {
            "syslog": {
                "enabled": self.syslog_enabled,
                "listening": self._transport is not None,
                "at": dt_util.utc_from_timestamp(last_line).isoformat() if last_line else None,
                "event_at": self.last_syslog,
            },
            "snmp": {
                "enabled": self.snmp_enabled,
                "at": self.last_snmp.isoformat() if self.last_snmp else None,
                "ok": self.snmp_ok,
                "total": len(self._pollers),
                "interval": self.interval,
            },
        }

    def diagnostics(self) -> dict[str, Any]:
        return {
            "syslog": self.syslog_enabled,
            "syslog_listening": self._transport is not None,
            "syslog_debug_events": self.debug_events,
            "syslog_forward_targets": len(self.forward),
            "syslog_forwarded": self._transport.get_protocol().stats["forwarded"] if self._transport else 0,
            "syslog_forward_errors": self._transport.get_protocol().stats["forward_errors"] if self._transport else 0,
            "syslog_forward_skipped_loop": self._transport.get_protocol().stats["forward_skipped_loop"] if self._transport else 0,
            "snmp": self.snmp_enabled,
            "stats": dict(self.stats),
            "last_syslog": self.last_syslog,
            "devices": {
                mac: {
                    "snmp": poller.variant.value if poller.variant else None,
                    "snmp_errors": self.snmp_errors[mac],
                    "syslog_events": self.syslog_by_device[mac],
                }
                for mac, poller in self._pollers.items()
            }
            | {
                mac: {"snmp": None, "snmp_errors": 0, "syslog_events": self.syslog_by_device[mac]}
                for mac in self._undetected
            },
        }
