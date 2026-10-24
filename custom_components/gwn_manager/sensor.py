"""Sensors for GWN Manager clients and access points."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfDataRate,
    UnitOfInformation,
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


def _live(key: str) -> Callable[[dict[str, Any]], Any]:
    """Value that only makes sense while the client is connected."""
    return lambda client: client.get(key) if client["connected"] else None


def _last_seen(client: dict[str, Any]) -> datetime | None:
    if client["connected"] and (ts := parse_timestamp(client.get("lastactive"))):
        return ts
    return parse_timestamp(client.get("lastSeen"))


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
            GWNAPSensor(coordinator, mac, description) for description in AP_SENSORS
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
