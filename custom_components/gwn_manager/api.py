"""Async client for the Grandstream GWN Manager / GWN.Cloud open API."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

API_PREFIX = "/oapi"
PAGE_SIZE = 100
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)
# Renew the token a bit before it actually expires.
TOKEN_EXPIRY_MARGIN = 60
MAX_IMAGE_SIZE = 1_000_000


class GWNError(Exception):
    """Base error for the GWN API."""


class GWNConnectionError(GWNError):
    """The GWN Manager could not be reached."""


class GWNAuthError(GWNError):
    """The App ID / secret were rejected."""


class GWNApiError(GWNError):
    """The API returned an error code."""

    def __init__(self, ret_code: Any, msg: str) -> None:
        super().__init__(f"GWN API error {ret_code}: {msg}")
        self.ret_code = ret_code


class GWNClient:
    """Minimal client for the endpoints this integration needs."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        app_id: str,
        secret_key: str,
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._app_id = app_id
        self._secret_key = secret_key
        self._access_token: str | None = None
        self._token_expiry = 0.0
        self._auth_lock = asyncio.Lock()

    async def authenticate(self) -> None:
        """Fetch a new access token."""
        payload = {
            "client_id": self._app_id,
            "client_secret": self._secret_key,
            "grant_type": "client_credentials",
        }
        try:
            async with self._session.post(
                f"{self._base_url}/oauth/token", data=payload, timeout=REQUEST_TIMEOUT
            ) as resp:
                if resp.status in (400, 401, 403):
                    raise GWNAuthError(f"Authentication rejected (HTTP {resp.status})")
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except GWNError:
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            raise GWNConnectionError(f"Cannot reach {self._base_url}: {err}") from err

        token = (data or {}).get("access_token")
        if not token:
            raise GWNAuthError(f"No access token in response: {data}")
        self._access_token = token
        self._token_expiry = time.time() + int(data.get("expires_in", 3600))

    async def _ensure_token(self) -> None:
        async with self._auth_lock:
            if (
                not self._access_token
                or time.time() >= self._token_expiry - TOKEN_EXPIRY_MARGIN
            ):
                await self.authenticate()

    def _signature(self, timestamp: int, body: str | None) -> str:
        """Calculate the request signature as described in the GWN API docs."""
        params = {
            "access_token": self._access_token,
            "appID": self._app_id,
            "secretKey": self._secret_key,
            "timestamp": str(timestamp),
        }
        sign_str = "&" + "&".join(f"{k}={params[k]}" for k in sorted(params)) + "&"
        if body:
            sign_str += hashlib.sha256(body.encode()).hexdigest() + "&"
        return hashlib.sha256(sign_str.encode()).hexdigest()

    async def _post(
        self, endpoint: str, body: dict[str, Any] | None = None, retry: bool = True
    ) -> Any:
        """POST a signed request and return the ``data`` member of the response."""
        await self._ensure_token()
        # The body hash is part of the signature, so the exact bytes we hash
        # must be the bytes we send. Endpoints without parameters reject an
        # empty JSON object ("{}"), they must be called without a body.
        body_str = json.dumps(body, separators=(",", ":")) if body else None
        timestamp = int(time.time() * 1000)
        params = {
            "access_token": self._access_token,
            "appID": self._app_id,
            "timestamp": str(timestamp),
            "signature": self._signature(timestamp, body_str),
        }
        try:
            async with self._session.post(
                f"{self._base_url}{API_PREFIX}{endpoint}",
                params=params,
                data=body_str,
                headers={"Content-Type": "application/json"},
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                if resp.status == 401 and retry:
                    self._access_token = None
                    return await self._post(endpoint, body, retry=False)
                if resp.status in (401, 403):
                    raise GWNAuthError(f"Request rejected (HTTP {resp.status})")
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except GWNError:
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            raise GWNConnectionError(f"Request to {endpoint} failed: {err}") from err

        ret_code = (data or {}).get("retCode")
        if ret_code != 0:
            if retry:
                # Most likely an expired/revoked token: get a new one and retry once.
                _LOGGER.debug("%s returned %s, retrying with new token", endpoint, data)
                self._access_token = None
                return await self._post(endpoint, body, retry=False)
            raise GWNApiError(ret_code, (data or {}).get("msg", ""))
        return data.get("data")

    async def _post_paged(self, endpoint: str, body: dict[str, Any]) -> list[dict[str, Any]]:
        """Fetch all pages of a list endpoint."""
        results: list[dict[str, Any]] = []
        page = 1
        while True:
            data = await self._post(endpoint, {**body, "pageNum": page, "pageSize": PAGE_SIZE})
            data = data or {}
            results.extend(data.get("result") or [])
            total_pages = data.get("totalPage") or data.get("pages") or 1
            if page >= total_pages:
                return results
            page += 1

    async def get_networks(self) -> list[dict[str, Any]]:
        """Return all networks the app has access to."""
        return await self._post_paged(
            "/v1.0.0/network/list", {"type": "asc", "order": "id", "search": ""}
        )

    async def get_access_points(self, network_id: int) -> list[dict[str, Any]]:
        """Return all devices (APs, switches, routers) of a network."""
        return await self._post_paged("/v1.0.0/ap/list", {"networkId": network_id})

    async def get_network_detail(self, network_id: int) -> dict[str, Any]:
        """Return details of a network, including its time zone."""
        return await self._post("/v1.0.0/network/detail", {"id": network_id}) or {}

    async def get_device_details(self, mac: str) -> dict[str, Any]:
        """Return detailed info of a device (CPU, memory, temperature, radios, ...).

        The endpoint accepts a list of MACs but only answers for the first one.
        """
        return await self._post("/v2.0.0/ap/info", {"mac": [mac]}) or {}

    async def get_device_info(self, network_id: int, mac: str) -> dict[str, Any]:
        """Return device info including type, PoE budget and, for switches, CPU/memory."""
        return await self._post("/v1.0.0/device/info", {"networkId": network_id, "mac": mac}) or {}

    async def get_switch_ports(self, network_id: int, mac: str) -> list[dict[str, Any]]:
        """Return the ports of a switch (link, rates in bit/s, PoE state)."""
        return await self._post("/v1.0.0/switch/port/info", {"networkId": network_id, "mac": mac}) or []

    async def get_firmware_versions(self, network_id: int) -> list[dict[str, Any]]:
        """Return current and recommended firmware of each device."""
        data = await self._post("/v1.0.0/upgrade/version", {"networkId": network_id}) or {}
        return data.get("result") or []

    async def upgrade_device(self, mac: str) -> None:
        """Upgrade a device to the recommended firmware."""
        await self._post("/v1.0.0/upgrade/add", {"macs": [mac]})

    async def reboot_device(self, mac: str) -> None:
        """Reboot a device."""
        await self._post("/v1.0.0/ap/reboot", {"mac": [mac]})

    async def get_ssids(self, network_id: int) -> list[dict[str, Any]]:
        """Return the SSIDs of a network."""
        return await self._post_paged("/v1.0.0/ssid/list", {"networkId": network_id})

    async def get_ssid_configuration(self, ssid_id: int) -> dict[str, Any]:
        """Return the configuration of an SSID, including its key."""
        return await self._post("/v1.0.0/ssid/configuration", {"id": ssid_id}) or {}

    async def set_client_blocked(self, network_id: int, mac: str, blocked: bool) -> None:
        """Block or unblock a client."""
        await self._post(
            "/v1.0.0/client/block",
            {"networkId": network_id, "clientId": mac, "block": 0 if blocked else 1},
        )

    async def get_clients(self, network_id: int, connected_only: bool) -> list[dict[str, Any]]:
        """Return clients of a network.

        ``connected_only=True`` returns only currently connected clients but
        with live values (RSSI, link rate, channel, ...), wired ones included.
        Otherwise all known wireless clients are returned, including offline
        ones, without live values. (The history of wired clients is left out:
        it is mostly stale, nameless entries.)
        """
        body: dict[str, Any] = {"networkId": network_id, "untilNow": 0 if connected_only else 1}
        if connected_only:
            # Default filter is "all_ssid" (wireless only).
            body["filter"] = {"ssidId": "all_client"}
        return await self._post_paged("/v1.0.0/client/list", body)

    async def get_model_image(self, model: str) -> bytes | None:
        """Product picture of a device model, as the GWN web UI shows it.

        Served without login by GWN Manager and GWN.Cloud. Unknown models get
        the web UI's HTML page instead of an error, hence the content type check.
        """
        try:
            async with self._session.get(
                f"{self._base_url}/img/topo/main_icon/{model}.png", timeout=REQUEST_TIMEOUT
            ) as resp:
                if resp.status != 200 or resp.content_type != "image/png":
                    return None
                data = await resp.read()
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.debug("Cannot fetch the picture of %s: %s", model, err)
            return None
        return data if len(data) <= MAX_IMAGE_SIZE else None
