"""Poll the client table of one GWN device over SNMP v2c.

The agents only answer GETNEXT reliably (GETBULK times out, GET by index
returns wrong columns), so every column is walked step by step, one column
after the other: the agents are slow when asked in parallel, and a GETNEXT
with several OIDs returns garbage.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from .snmp_parse import COLUMNS, TABLES, MibVariant, SnmpClient, build_clients

# (host, community, oid prefix) -> {OID suffix after the prefix: value}
WalkFn = Callable[[str, str, str], Awaitable[dict[str, str]]]

# Subtrees that exist on a device even without clients, used to tell the MIBs apart.
DETECT = (
    (MibVariant.AP, "1.3.6.1.4.1.42397.1.1.2"),
    (MibVariant.ROUTER, "1.3.6.1.4.1.42397.1.2.1"),
)
TIMEOUT = 5


class SnmpUnavailable(Exception):
    """The device did not answer."""


class SnmpPoller:
    """Reads one device's Wi-Fi clients."""

    def __init__(self, host: str, community: str, walk: WalkFn | None = None) -> None:
        self.host = host
        self.community = community
        self.variant: MibVariant | None = None
        self._walk = walk or _PysnmpWalker().walk

    async def detect(self) -> MibVariant | None:
        """Find out which client table the device has (None: no usable SNMP)."""
        for variant, oid in DETECT:
            try:
                if await self._walk(self.host, self.community, oid):
                    self.variant = variant
                    return variant
            except (TimeoutError, OSError, SnmpUnavailable):
                return None
        return None

    async def poll(self) -> dict[str, SnmpClient]:
        """Walk the client table; raises SnmpUnavailable if the device doesn't answer."""
        if self.variant is None:
            raise SnmpUnavailable("not detected")
        table = TABLES[self.variant]
        columns: dict[str, dict[str, str]] = {}
        for name, number in COLUMNS[self.variant].items():
            try:
                columns[name] = await self._walk(self.host, self.community, f"{table}.{number}")
            except (TimeoutError, OSError) as err:
                raise SnmpUnavailable(str(err)) from err
        return build_clients(self.variant, columns)


class _PysnmpWalker:
    """GETNEXT walk with pysnmp; one engine per poller."""

    def __init__(self) -> None:
        self._engine: Any = None
        self._targets: dict[str, Any] = {}

    async def walk(self, host: str, community: str, oid: str) -> dict[str, str]:
        # Imported here: pysnmp loads its MIB modules on import (blocking I/O).
        from pysnmp.hlapi.v3arch.asyncio import (  # noqa: PLC0415
            CommunityData,
            ContextData,
            ObjectIdentity,
            ObjectType,
            SnmpEngine,
            UdpTransportTarget,
            next_cmd,
        )

        if self._engine is None:
            self._engine = SnmpEngine()
        if host not in self._targets:
            self._targets[host] = await UdpTransportTarget.create(
                (host, 161), timeout=TIMEOUT, retries=0
            )
        auth = CommunityData(community, mpModel=1)
        prefix = oid + "."
        result: dict[str, str] = {}
        current = oid
        while True:
            error, status, _, binds = await next_cmd(
                self._engine,
                auth,
                self._targets[host],
                ContextData(),
                ObjectType(ObjectIdentity(current)),
                lookupMib=False,
            )
            if error:
                raise TimeoutError(str(error))
            if status or not binds:
                return result
            name, value = binds[0]
            name = str(name)
            if not name.startswith(prefix):
                return result
            result[name[len(prefix) :]] = value.prettyPrint()
            current = name
