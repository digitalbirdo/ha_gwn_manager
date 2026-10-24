"""Wi-Fi QR codes for GWN Manager SSIDs."""
from __future__ import annotations

import io

import segno

from homeassistant.components.image import ImageEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import GWNConfigEntry, GWNCoordinator
from .entity import GWNSSIDEntity, async_setup_ssid_entities

PARALLEL_UPDATES = 0

# ssidSecurityType: 0 open, 1 personal (WPA/WPA2/WPA3 PSK); the others
# (enterprise, PPSK, Hotspot 2.0) have no single shareable password.
SECURITY_OPEN = 0
SECURITY_PERSONAL = 1


def _escape(value: str) -> str:
    """Escape special characters for the WIFI: QR format."""
    for char in ("\\", ";", ",", ":", '"'):
        value = value.replace(char, f"\\{char}")
    return value


def wifi_qr_payload(ssid: dict) -> str | None:
    """Return the WIFI: payload for an SSID, or None if it can't be shared."""
    config = ssid.get("configuration") or {}
    name = ssid.get("ssidName")
    security = config.get("ssidSecurityType")
    if not name or security not in (SECURITY_OPEN, SECURITY_PERSONAL):
        return None
    hidden = "H:true;" if config.get("ssidSsidHidden") else ""
    if security == SECURITY_OPEN:
        return f"WIFI:T:nopass;S:{_escape(name)};{hidden};"
    if not (key := config.get("ssidWpaKey")):
        return None
    return f"WIFI:T:WPA;S:{_escape(name)};P:{_escape(key)};{hidden};"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GWNConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a QR code for every shareable SSID."""
    async_setup_ssid_entities(
        entry,
        async_add_entities,
        lambda coordinator, key: (
            [GWNWifiQRCode(hass, coordinator, key)]
            if wifi_qr_payload(coordinator.data.ssids[key])
            else []
        ),
    )


class GWNWifiQRCode(GWNSSIDEntity, ImageEntity):
    """QR code to join the Wi-Fi. Contains the password, so disabled by default."""

    _attr_content_type = "image/png"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "wifi_qr_code"

    def __init__(self, hass: HomeAssistant, coordinator: GWNCoordinator, key: str) -> None:
        GWNSSIDEntity.__init__(self, coordinator, key, "qr_code")
        ImageEntity.__init__(self, hass)
        self._payload: str | None = None
        self._image: bytes | None = None

    @callback
    def _handle_coordinator_update(self) -> None:
        payload = wifi_qr_payload(self.ssid)
        if payload != self._payload:
            # SSID name or password changed: new image.
            self._payload = payload
            self._image = None
            self._attr_image_last_updated = dt_util.utcnow()
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._payload = wifi_qr_payload(self.ssid)
        self._attr_image_last_updated = dt_util.utcnow()

    async def async_image(self) -> bytes | None:
        if self._payload is None:
            return None
        if self._image is None:
            buffer = io.BytesIO()
            segno.make_qr(self._payload).save(buffer, kind="png", scale=8, border=2)
            self._image = buffer.getvalue()
        return self._image
