"""Sensor platform for Grandstream GWN Manager."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfInformation,
    UnitOfDataRate,
    UnitOfTime,
    UnitOfTemperature,
)
from datetime import datetime
import pytz
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the GWN Manager sensors for clients and APs as dedicated devices."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    
    # Create sensors for each AP
    aps = coordinator.data.get("aps", [])
    for ap in aps:
        entities.append(GWNAPStatusSensor(coordinator, ap))
        entities.append(GWNAPUpTimeSensor(coordinator, ap))
        entities.append(GWNAPClientsCountSensor(coordinator, ap))
        entities.append(GWNAPUsageSensor(coordinator, ap))
        entities.append(GWNAPUploadSensor(coordinator, ap))
        entities.append(GWNAPDownloadSensor(coordinator, ap))
        entities.append(GWNAPFirmwareSensor(coordinator, ap))
        entities.append(GWNAPIPv4Sensor(coordinator, ap))
        entities.append(GWNAPIPv6Sensor(coordinator, ap))
    
    # Create sensors for each client
    clients = coordinator.data.get("clients", [])
    for client in clients:
        # Create sensors for each client
        entities.append(GWNClientRSSISensor(coordinator, client))
        entities.append(GWNClientAPSensor(coordinator, client))
        entities.append(GWNClientStatusSensor(coordinator, client))
        entities.append(GWNClientSSIDSensor(coordinator, client))
        entities.append(GWNClientTxBytesSensor(coordinator, client))
        entities.append(GWNClientRxBytesSensor(coordinator, client))
        entities.append(GWNClientTxRateSensor(coordinator, client))
        entities.append(GWNClientRxRateSensor(coordinator, client))
        entities.append(GWNClientLastSeenSensor(coordinator, client))
        entities.append(GWNClientIPv4Sensor(coordinator, client))
        entities.append(GWNClientIPv6Sensor(coordinator, client))
        entities.append(GWNClientChannelClassSensor(coordinator, client))
        entities.append(GWNClientVlanIdSensor(coordinator, client))

    async_add_entities(entities)


class GWNAPBaseSensor(CoordinatorEntity, SensorEntity):
    """Base class for GWN AP sensors."""

    def __init__(self, coordinator, ap_data):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._ap_mac = ap_data["mac"]
        self._ap_name = ap_data["name"]
        
    @property
    def device_info(self) -> DeviceInfo:
        """Return device information about this entity."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._ap_mac)},
            name=self._ap_name,
            manufacturer="Grandstream",
            model=self._ap_data.get("apType", "GWN AP"),
        )

    @property
    def _ap_data(self):
        """Get the latest data for this AP from the coordinator."""
        # Find the AP in the current data
        aps = self.coordinator.data.get("aps", [])
        for ap in aps:
            if ap["mac"] == self._ap_mac:
                return ap
        return None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return super().available and self._ap_data is not None


class GWNAPStatusSensor(GWNAPBaseSensor):
    """Sensor for AP Status."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, ap_data):
        super().__init__(coordinator, ap_data)
        self._attr_unique_id = f"{self._ap_mac}_status"
        self._attr_name = "Status"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        data = self._ap_data
        if data:
            return "Online" if data.get("status", 0) else "Offline"
        return None


class GWNAPUpTimeSensor(GWNAPBaseSensor):
    """Sensor for AP Uptime."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_has_entity_name = True

    def __init__(self, coordinator, ap_data):
        super().__init__(coordinator, ap_data)
        self._attr_unique_id = f"{self._ap_mac}_uptime"
        self._attr_name = "Uptime"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        data = self._ap_data
        if data:
            return data.get("upTime", 0)
        return None


class GWNAPClientsCountSensor(GWNAPBaseSensor):
    """Sensor for AP connected clients count."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_has_entity_name = True

    def __init__(self, coordinator, ap_data):
        super().__init__(coordinator, ap_data)
        self._attr_unique_id = f"{self._ap_mac}_clients_count"
        self._attr_name = "Connected Clients"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        data = self._ap_data
        if data:
            return data.get("clients", 0)
        return None


class GWNAPUsageSensor(GWNAPBaseSensor):
    """Sensor for AP total usage."""

    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfInformation.BYTES
    _attr_has_entity_name = True

    def __init__(self, coordinator, ap_data):
        super().__init__(coordinator, ap_data)
        self._attr_unique_id = f"{self._ap_mac}_usage"
        self._attr_name = "Total Usage"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        data = self._ap_data
        if data:
            return data.get("usage", 0)
        return None


class GWNAPUploadSensor(GWNAPBaseSensor):
    """Sensor for AP upload."""

    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfInformation.BYTES
    _attr_has_entity_name = True

    def __init__(self, coordinator, ap_data):
        super().__init__(coordinator, ap_data)
        self._attr_unique_id = f"{self._ap_mac}_upload"
        self._attr_name = "Upload"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        data = self._ap_data
        if data:
            return data.get("upload", 0)
        return None


class GWNAPDownloadSensor(GWNAPBaseSensor):
    """Sensor for AP download."""

    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfInformation.BYTES
    _attr_has_entity_name = True

    def __init__(self, coordinator, ap_data):
        super().__init__(coordinator, ap_data)
        self._attr_unique_id = f"{self._ap_mac}_download"
        self._attr_name = "Download"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        data = self._ap_data
        if data:
            return data.get("download", 0)
        return None


class GWNAPFirmwareSensor(GWNAPBaseSensor):
    """Sensor for AP firmware version."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, ap_data):
        super().__init__(coordinator, ap_data)
        self._attr_unique_id = f"{self._ap_mac}_firmware"
        self._attr_name = "Firmware Version"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        data = self._ap_data
        if data:
            return data.get("versionFirmware", "Unknown")
        return None


class GWNAPIPv4Sensor(GWNAPBaseSensor):
    """Sensor for AP IPv4 address."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, ap_data):
        super().__init__(coordinator, ap_data)
        self._attr_unique_id = f"{self._ap_mac}_ipv4"
        self._attr_name = "IPv4 Address"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        data = self._ap_data
        if data:
            return data.get("ipv4", "")
        return None


class GWNAPIPv6Sensor(GWNAPBaseSensor):
    """Sensor for AP IPv6 address."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, ap_data):
        super().__init__(coordinator, ap_data)
        self._attr_unique_id = f"{self._ap_mac}_ipv6"
        self._attr_name = "IPv6 Address"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        data = self._ap_data
        if data:
            return data.get("ipv6", "")
        return None


class GWNClientBaseSensor(CoordinatorEntity, SensorEntity):
    """Base class for GWN Client sensors."""

    def __init__(self, coordinator, client_data):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._client_mac = client_data["mac"]
        self._client_name = client_data["name"]
        self._ap_name = client_data.get("ap_name", "Unknown AP")
        self._ap_mac = client_data.get("ap_mac", "")
        
    @property
    def device_info(self) -> DeviceInfo:
        """Return device information about this entity."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._client_mac)},
            name=self._client_name,
            manufacturer="Grandstream",
            model="Network Client",
            suggested_area=self._ap_name,
            via_device=(DOMAIN, self._ap_mac) if self._ap_mac else None,
        )

    @property
    def _client_data(self):
        """Get the latest data for this client from the coordinator."""
        # Find the client in the current data
        clients = self.coordinator.data.get("clients", [])
        for client in clients:
            if client["mac"] == self._client_mac:
                return client
        return None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return super().available and self._client_data is not None


class GWNClientRSSISensor(GWNClientBaseSensor):
    """Sensor for Client RSSI."""

    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_has_entity_name = True

    def __init__(self, coordinator, client_data):
        super().__init__(coordinator, client_data)
        self._attr_unique_id = f"{self._client_mac}_rssi"
        self._attr_name = "RSSI"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        data = self._client_data
        if data:
            return data.get("rssi")
        return None


class GWNClientAPSensor(GWNClientBaseSensor):
    """Sensor for Client Connected AP."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, client_data):
        super().__init__(coordinator, client_data)
        self._attr_unique_id = f"{self._client_mac}_ap"
        self._attr_name = "Connected AP"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        data = self._client_data
        if data:
            return data.get("ap_name", "Unknown")
        return None


class GWNClientStatusSensor(GWNClientBaseSensor):
    """Sensor for Client Online Status."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, client_data):
        super().__init__(coordinator, client_data)
        self._attr_unique_id = f"{self._client_mac}_status"
        self._attr_name = "Status"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        data = self._client_data
        if data:
            return "Online" if data.get("online", 0) else "Offline"
        return None


class GWNClientSSIDSensor(GWNClientBaseSensor):
    """Sensor for Client SSID."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, client_data):
        super().__init__(coordinator, client_data)
        self._attr_unique_id = f"{self._client_mac}_ssid"
        self._attr_name = "SSID"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        data = self._client_data
        if data:
            return data.get("ssid", "Unknown")
        return None


class GWNClientTxBytesSensor(GWNClientBaseSensor):
    """Sensor for Client TX Bytes."""

    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfInformation.BYTES
    _attr_has_entity_name = True

    def __init__(self, coordinator, client_data):
        super().__init__(coordinator, client_data)
        self._attr_unique_id = f"{self._client_mac}_tx_bytes"
        self._attr_name = "TX Bytes"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        data = self._client_data
        if data:
            return data.get("tx_bytes", 0)
        return None


class GWNClientRxBytesSensor(GWNClientBaseSensor):
    """Sensor for Client RX Bytes."""

    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfInformation.BYTES
    _attr_has_entity_name = True

    def __init__(self, coordinator, client_data):
        super().__init__(coordinator, client_data)
        self._attr_unique_id = f"{self._client_mac}_rx_bytes"
        self._attr_name = "RX Bytes"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        data = self._client_data
        if data:
            return data.get("rx_bytes", 0)
        return None


class GWNClientTxRateSensor(GWNClientBaseSensor):
    """Sensor for Client TX Rate."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfDataRate.KILOBITS_PER_SECOND
    _attr_has_entity_name = True

    def __init__(self, coordinator, client_data):
        super().__init__(coordinator, client_data)
        self._attr_unique_id = f"{self._client_mac}_tx_rate"
        self._attr_name = "TX Rate"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        data = self._client_data
        if data:
            return data.get("tx_rate", 0)
        return None


class GWNClientRxRateSensor(GWNClientBaseSensor):
    """Sensor for Client RX Rate."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfDataRate.KILOBITS_PER_SECOND
    _attr_has_entity_name = True

    def __init__(self, coordinator, client_data):
        super().__init__(coordinator, client_data)
        self._attr_unique_id = f"{self._client_mac}_rx_rate"
        self._attr_name = "RX Rate"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        data = self._client_data
        if data:
            return data.get("rx_rate", 0)
        return None


class GWNClientLastSeenSensor(GWNClientBaseSensor):
    """Sensor for time since client was last seen."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_has_entity_name = True

    def __init__(self, coordinator, client_data):
        super().__init__(coordinator, client_data)
        self._attr_unique_id = f"{self._client_mac}_last_seen_seconds"
        self._attr_name = "Last Seen"

    @property
    def native_value(self):
        """Return seconds since last active."""
        data = self._client_data
        if data:
            lastactive = data.get("lastactive")
            if lastactive:
                try:
                    # Parse ISO 8601 timestamp
                    last_time = datetime.fromisoformat(lastactive.replace("Z", "+00:00"))
                    now = datetime.now(pytz.UTC)
                    delta = (now - last_time).total_seconds()
                    return max(0, int(delta))
                except (ValueError, TypeError):
                    return None
        return None


class GWNClientIPv4Sensor(GWNClientBaseSensor):
    """Sensor for Client IPv4 address."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, client_data):
        super().__init__(coordinator, client_data)
        self._attr_unique_id = f"{self._client_mac}_ipv4"
        self._attr_name = "IPv4 Address"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        data = self._client_data
        if data:
            return data.get("ipv4", "")
        return None


class GWNClientIPv6Sensor(GWNClientBaseSensor):
    """Sensor for Client IPv6 address."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, client_data):
        super().__init__(coordinator, client_data)
        self._attr_unique_id = f"{self._client_mac}_ipv6"
        self._attr_name = "IPv6 Address"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        data = self._client_data
        if data:
            return data.get("ipv6", "")
        return None


class GWNClientChannelClassSensor(GWNClientBaseSensor):
    """Sensor for Client channel class (2G/5G/6G)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, client_data):
        super().__init__(coordinator, client_data)
        self._attr_unique_id = f"{self._client_mac}_channel_class"
        self._attr_name = "Channel Class"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        data = self._client_data
        if data:
            return data.get("channelClassStr", "Unknown")
        return None


class GWNClientVlanIdSensor(GWNClientBaseSensor):
    """Sensor for Client VLAN ID."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_has_entity_name = True

    def __init__(self, coordinator, client_data):
        super().__init__(coordinator, client_data)
        self._attr_unique_id = f"{self._client_mac}_vlan_id"
        self._attr_name = "VLAN ID"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        data = self._client_data
        if data:
            vid = data.get("vid")
            return vid if vid is not None else "Not assigned"
        return None

    _attr_has_entity_name = True

    def __init__(self, coordinator, client_data):
        super().__init__(coordinator, client_data)
        self._attr_unique_id = f"{self._client_mac}_ap"
        self._attr_name = "Connected AP"
        self._attr_icon = "mdi:access-point"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        data = self._client_data
        if data:
            return data.get("ap")
        return None
