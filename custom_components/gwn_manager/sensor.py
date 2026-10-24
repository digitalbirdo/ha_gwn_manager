"""Sensors for GWN Manager clients and access points."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfDataRate,
    UnitOfInformation,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .coordinator import GWNConfigEntry, GWNCoordinator, parse_timestamp
from .entity import (
    GWNAPEntity,
    GWNClientEntity,
    GWNPortEntity,
    GWNSSIDEntity,
    async_client_area,
    async_setup_ap_entities,
    async_setup_client_entities,
    async_setup_port_entities,
    async_setup_ssid_entities,
)

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class GWNSensorEntityDescription(SensorEntityDescription):
    """Describes a GWN sensor."""

    value_fn: Callable[[dict[str, Any]], StateType | datetime]
    # Whether to create the entity at all, e.g. only for devices with the hardware.
    exists_fn: Callable[[dict[str, Any]], bool] = lambda _: True


def _live(key: str) -> Callable[[dict[str, Any]], Any]:
    """Value that only makes sense while the client is connected."""
    return lambda client: client.get(key) if client["connected"] else None


def _last_seen(client: dict[str, Any]) -> datetime | None:
    time_zone = client.get("timeZone")
    if client["connected"] and (ts := parse_timestamp(client.get("lastactive"), time_zone)):
        return ts
    return parse_timestamp(client.get("lastSeen"), time_zone)


def _number(value: Any, maximum: float | None = None) -> float | None:
    """Extract the number from values like "55℃", "9.0%" or "211 MB"."""
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str) and (match := re.search(r"-?\d+(?:\.\d+)?", value)):
        number = float(match.group())
    else:
        return None
    if maximum is not None and not 0 <= number <= maximum:
        # Some models report nonsense, e.g. a CPU usage of "192224%".
        return None
    return number


def _detail(key: str, maximum: float | None = None) -> Callable[[dict[str, Any]], float | None]:
    return lambda ap: _number(ap.get("details", {}).get(key), maximum)


def _info(key: str, maximum: float | None = None) -> Callable[[dict[str, Any]], float | None]:
    """Value from device/info, e.g. info["result"] = [{"key": "cpuUtilz", "value": "4%"}]."""

    def value(ap: dict[str, Any]) -> float | None:
        for item in ap.get("info", {}).get("result") or []:
            if item.get("key") == key:
                return _number(item.get("value"), maximum)
        return None

    return value


def _poe(key: str) -> Callable[[dict[str, Any]], float | None]:
    """PoE value from device/info, e.g. info["poeInfo"] = [{"key": "PoEUsePower", "value": "26.7"}]."""

    def value(ap: dict[str, Any]) -> float | None:
        for item in ap.get("info", {}).get("poeInfo") or []:
            if item.get("key") == key and item.get("type") == 0:
                return _number(item.get("value"))
        return None

    return value


def _first(*fns: Callable[[dict[str, Any]], float | None]) -> Callable[[dict[str, Any]], float | None]:
    """First value any of the functions returns, for data in different places per model."""

    def value(ap: dict[str, Any]) -> float | None:
        for fn in fns:
            if (result := fn(ap)) is not None:
                return result
        return None

    return value


def _memory(ap: dict[str, Any]) -> float | None:
    # Switches report "0 MB" in the device details, the real value in device/info.
    return _detail("usedMemory")(ap) or _info("usedMemory")(ap)



def _radio(band: str, key: str) -> Callable[[dict[str, Any]], float | None]:
    """Value from the per-band lists, e.g. details["g5"] = [{"key": "channel", "value": "40"}]."""

    def value(ap: dict[str, Any]) -> float | None:
        for item in ap.get("details", {}).get(band) or []:
            if item.get("key") == key:
                return _number(item.get("value"))
        return None

    return value


def _has(
    fn: Callable[[dict[str, Any]], Any], allow_zero: bool = True
) -> Callable[[dict[str, Any]], bool]:
    """Only create the entity if the device reports a value."""
    return lambda ap: (value := fn(ap)) is not None and (allow_zero or value != 0)


def _connected_since(client: dict[str, Any]) -> datetime | None:
    seconds = client.get("assocSecond")
    if not client["connected"] or seconds is None:
        return None
    # Round to the minute so the state does not change on every poll.
    since = dt_util.utcnow() - timedelta(seconds=int(seconds))
    return since.replace(second=0, microsecond=0)


CLIENT_SENSORS: tuple[GWNSensorEntityDescription, ...] = (
    GWNSensorEntityDescription(
        key="rssi",
        translation_key="rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_live("rssi"),
    ),
    GWNSensorEntityDescription(
        key="access_point",
        translation_key="access_point",
        value_fn=_live("apName"),
    ),
    GWNSensorEntityDescription(
        key="ssid",
        translation_key="ssid",
        value_fn=_live("ssid"),
    ),
    GWNSensorEntityDescription(
        key="band",
        translation_key="band",
        value_fn=_live("channelClassStr"),
    ),
    GWNSensorEntityDescription(
        key="last_seen",
        translation_key="last_seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_last_seen,
    ),
    GWNSensorEntityDescription(
        key="connected_since",
        translation_key="connected_since",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_registry_enabled_default=False,
        value_fn=_connected_since,
    ),
    GWNSensorEntityDescription(
        key="channel",
        translation_key="channel",
        value_fn=_live("channel"),
    ),
    GWNSensorEntityDescription(
        key="tx_rate",
        translation_key="tx_rate",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_live("txRate"),
    ),
    GWNSensorEntityDescription(
        key="rx_rate",
        translation_key="rx_rate",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_live("rxRate"),
    ),
    GWNSensorEntityDescription(
        key="tx_bytes",
        translation_key="tx_bytes",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_registry_enabled_default=False,
        value_fn=lambda client: client.get("txBytes"),
    ),
    GWNSensorEntityDescription(
        key="rx_bytes",
        translation_key="rx_bytes",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_registry_enabled_default=False,
        value_fn=lambda client: client.get("rxBytes"),
    ),
    GWNSensorEntityDescription(
        key="download_rate",
        translation_key="download_rate",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.BITS_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda client: client.get("downloadRate"),
    ),
    GWNSensorEntityDescription(
        key="upload_rate",
        translation_key="upload_rate",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.BITS_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda client: client.get("uploadRate"),
    ),
    GWNSensorEntityDescription(
        key="ip_address",
        translation_key="ip_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda client: client.get("ipv4") or None,
    ),
    GWNSensorEntityDescription(
        key="vlan",
        translation_key="vlan",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda client: client.get("vlanId"),
    ),
)

AP_SENSORS: tuple[GWNSensorEntityDescription, ...] = (
    GWNSensorEntityDescription(
        key="clients",
        translation_key="clients",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda ap: ap.get("clients"),
    ),
    GWNSensorEntityDescription(
        key="uptime",
        translation_key="uptime",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.DAYS,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda ap: ap.get("upTime"),
    ),
    GWNSensorEntityDescription(
        key="firmware",
        translation_key="firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda ap: ap.get("versionFirmware"),
    ),
    GWNSensorEntityDescription(
        key="ip_address",
        translation_key="ip_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda ap: ap.get("ipv4") or ap.get("ip") or None,
    ),
    GWNSensorEntityDescription(
        key="temperature",
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_detail("temperature"),
        exists_fn=_has(_detail("temperature")),
    ),
    GWNSensorEntityDescription(
        key="cpu_usage",
        translation_key="cpu_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_first(_detail("cpuUsage", maximum=100), _info("cpuUtilz", maximum=100)),
        exists_fn=_has(_first(_detail("cpuUsage", maximum=100), _info("cpuUtilz", maximum=100))),
    ),
    GWNSensorEntityDescription(
        key="memory_used",
        translation_key="memory_used",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.MEGABYTES,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_memory,
        exists_fn=_has(_memory, allow_zero=False),
    ),
    GWNSensorEntityDescription(
        key="wan_download",
        translation_key="wan_download",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.BITS_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda ap: (ap.get("wan") or {}).get("downloadRate"),
        exists_fn=lambda ap: "wan" in ap,
    ),
    GWNSensorEntityDescription(
        key="wan_upload",
        translation_key="wan_upload",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.BITS_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda ap: (ap.get("wan") or {}).get("uploadRate"),
        exists_fn=lambda ap: "wan" in ap,
    ),
    GWNSensorEntityDescription(
        key="wan_ip",
        translation_key="wan_ip",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda ap: (ap.get("wan") or {}).get("wanIp"),
        exists_fn=lambda ap: "wan" in ap,
    ),
    GWNSensorEntityDescription(
        key="poe_power",
        translation_key="poe_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_poe("PoEUsePower"),
        exists_fn=_has(_poe("PoEUsePower")),
    ),
    GWNSensorEntityDescription(
        key="poe_budget",
        translation_key="poe_budget",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_poe("PoEPowerSupply"),
        exists_fn=_has(_poe("PoEPowerSupply")),
    ),
    *(
        GWNSensorEntityDescription(
            key=f"channel_load_{band}",
            translation_key=f"channel_load_{band}",
            native_unit_of_measurement=PERCENTAGE,
            state_class=SensorStateClass.MEASUREMENT,
            entity_category=EntityCategory.DIAGNOSTIC,
            value_fn=_detail(f"channelload_{band}", maximum=100),
            exists_fn=_has(_detail(f"channelload_{band}", maximum=100)),
        )
        for band in ("2g4", "5g", "6g")
    ),
    *(
        GWNSensorEntityDescription(
            key=f"channel_{band}",
            translation_key=f"channel_{band}",
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_enabled_default=False,
            value_fn=_radio(radio, "channel"),
            exists_fn=_has(_radio(radio, "channel"), allow_zero=False),
        )
        for band, radio in (("2g4", "g24"), ("5g", "g5"), ("6g", "g6"))
    ),
)


PORT_SENSORS: tuple[GWNSensorEntityDescription, ...] = (
    GWNSensorEntityDescription(
        key="link_speed",
        translation_key="port_link_speed",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        entity_registry_enabled_default=False,
        value_fn=lambda port: _number(port.get("operSpeed")) if port.get("operStatus") == "up" else 0,
    ),
    GWNSensorEntityDescription(
        key="download",
        translation_key="port_download",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.BITS_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda port: _number(port.get("inRate")),
    ),
    GWNSensorEntityDescription(
        key="upload",
        translation_key="port_upload",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.BITS_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda port: _number(port.get("outRate")),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GWNConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors for clients and access points."""
    async_setup_client_entities(
        entry,
        async_add_entities,
        lambda coordinator, mac: [
            *(GWNClientSensor(coordinator, mac, description) for description in CLIENT_SENSORS),
            GWNClientAreaSensor(coordinator, mac),
        ],
    )
    async_setup_ap_entities(
        entry,
        async_add_entities,
        lambda coordinator, mac: [
            GWNAPSensor(coordinator, mac, description)
            for description in AP_SENSORS
            if description.exists_fn(coordinator.data.aps[mac])
        ],
    )
    async_setup_port_entities(
        entry,
        async_add_entities,
        lambda coordinator, mac, port_id: [
            GWNPortSensor(coordinator, mac, port_id, description) for description in PORT_SENSORS
        ],
    )
    async_setup_ssid_entities(
        entry,
        async_add_entities,
        lambda coordinator, key: [GWNSSIDClientsSensor(coordinator, key)],
    )


class GWNClientSensor(GWNClientEntity, SensorEntity):
    """A sensor of a network client."""

    entity_description: GWNSensorEntityDescription

    def __init__(
        self,
        coordinator: GWNCoordinator,
        mac: str,
        description: GWNSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, mac, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType | datetime:
        return self.entity_description.value_fn(self.client)


class GWNClientAreaSensor(GWNClientEntity, SensorEntity):
    """Area of the access point the client is connected to (room-level presence).

    Assign areas to the access point devices to use it.
    """

    _attr_translation_key = "area"

    def __init__(self, coordinator: GWNCoordinator, mac: str) -> None:
        super().__init__(coordinator, mac, "area")

    @property
    def native_value(self) -> str | None:
        return async_client_area(self.hass, self.coordinator.config_entry.entry_id, self.client)


class GWNAPSensor(GWNAPEntity, SensorEntity):
    """A sensor of an access point."""

    entity_description: GWNSensorEntityDescription

    def __init__(
        self,
        coordinator: GWNCoordinator,
        mac: str,
        description: GWNSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, mac, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType | datetime:
        return self.entity_description.value_fn(self.ap)


class GWNPortSensor(GWNPortEntity, SensorEntity):
    """A sensor of a switch port."""

    entity_description: GWNSensorEntityDescription

    def __init__(
        self,
        coordinator: GWNCoordinator,
        mac: str,
        port_id: str,
        description: GWNSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, mac, port_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType | datetime:
        return self.entity_description.value_fn(self.port)


class GWNSSIDClientsSensor(GWNSSIDEntity, SensorEntity):
    """Number of clients connected to an SSID."""

    _attr_translation_key = "ssid_clients"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: GWNCoordinator, key: str) -> None:
        super().__init__(coordinator, key, "clients")

    @property
    def native_value(self) -> int:
        ssid = self.ssid
        return sum(
            1
            for client in self.coordinator.data.clients.values()
            if client["connected"]
            and client.get("ssid") == ssid.get("ssidName")
            and client.get("networkId") == ssid.get("networkId")
        )
