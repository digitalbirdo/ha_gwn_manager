"""Serve the product pictures of the GWN devices to the cards."""
from __future__ import annotations

import re
import time

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN

MODEL_PATTERN = re.compile(r"^GWN[0-9A-Z-]{2,20}$")
# Retry models without a picture now and then, e.g. after a GWN Manager update.
MISSING_RETRY = 3600


def model_image_url(model: str | None) -> str | None:
    """URL of the picture of a device model, if the model name looks valid."""
    if model and MODEL_PATTERN.match(model):
        return f"/{DOMAIN}/model/{model}.png"
    return None


class GWNModelImageView(HomeAssistantView):
    """Proxy for the product pictures of the GWN Manager.

    The browser can't load them directly: the GWN Manager mostly has a
    self-signed certificate and isn't reachable from outside the LAN. The
    pictures are public on the GWN Manager too, so no authentication here
    (an <img> can't send the token).
    """

    url = f"/{DOMAIN}/model/{{model}}.png"
    name = f"{DOMAIN}:model_image"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the cache."""
        self._hass = hass
        self._images: dict[str, bytes] = {}
        self._missing: dict[str, float] = {}

    async def get(self, request: web.Request, model: str) -> web.Response:
        """Return the picture of a model."""
        if not MODEL_PATTERN.match(model):
            raise web.HTTPNotFound
        if (image := self._images.get(model)) is None:
            if time.monotonic() < self._missing.get(model, 0):
                raise web.HTTPNotFound
            image = await self._fetch(model)
            if image is None:
                self._missing[model] = time.monotonic() + MISSING_RETRY
                raise web.HTTPNotFound
            self._images[model] = image
        return web.Response(
            body=image,
            content_type="image/png",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    async def _fetch(self, model: str) -> bytes | None:
        """Ask the loaded GWN Manager entries for the picture."""
        for entry in self._hass.config_entries.async_loaded_entries(DOMAIN):
            if image := await entry.runtime_data.client.get_model_image(model):
                return image
        return None
