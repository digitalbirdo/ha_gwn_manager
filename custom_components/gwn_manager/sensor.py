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
    async_setup_ap_entities,
    async_setup_client_entities,
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
        entity_registry_enabled_default=False,
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
        value_fn=_detail("cpuUsage", maximum=100),
        exists_fn=_has(_detail("cpuUsage", maximum=100)),
    ),
    GWNSensorEntityDescription(
        key="memory_used",
        translation_key="memory_used",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.MEGABYTES,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_detail("usedMemory"),
        # Switches report "0 MB".
        exists_fn=_has(_detail("usedMemory"), allow_zero=False),
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
            GWNClientSensor(coordinator, mac, description) for description in CLIENT_SENSORS
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
