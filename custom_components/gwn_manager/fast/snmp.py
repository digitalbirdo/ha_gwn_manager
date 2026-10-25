"""Poll the client table of one GWN device over SNMP v2c.

The agents only answer GETNEXT reliably (GETBULK times out, GET by index
returns wrong columns), so every column is walked step by step, one column
after the other: the agents are slow when asked in parallel, and a GETNEXT
with several OIDs returns garbage.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from pysnmp.hlapi.v3arch.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    next_cmd,
)
from pysnmp.smi import view

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

    def __init__(
        self,
        host: str,
        community: str,
        walk: WalkFn | None = None,
        engine: SnmpEngine | None = None,
    ) -> None:
        self.host = host
        self.community = community
        self.variant: MibVariant | None = None
        self._walk = walk or _PysnmpWalker(engine or build_engine()).walk

    async def detect(self) -> MibVariant | None:
        """Find out which client table the device has (None: no usable SNMP)."""
        for variant, oid in DETECT:
            try:
                if await self._walk(self.host, self.community, oid):
                    self.variant = variant
                    return variant
            except Exception:  # noqa: BLE001 - any odd agent reply means: no usable SNMP
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
            except Exception as err:  # noqa: BLE001 - timeouts and odd agent replies alike
                raise SnmpUnavailable(str(err)) from err
        return build_clients(self.variant, columns)


def build_engine() -> SnmpEngine:
    """Create an SNMP engine with its MIBs loaded (blocking: run in an executor)."""
    engine = SnmpEngine()
    mib_view_controller = view.MibViewController(
        engine.message_dispatcher.mib_instrum_controller.get_mib_builder()
    )
    engine.cache["mibViewController"] = mib_view_controller
    mib_view_controller.mibBuilder.load_modules()
    return engine


class _PysnmpWalker:
    """GETNEXT walk with pysnmp."""

    def __init__(self, engine: SnmpEngine) -> None:
        self._engine = engine
        self._targets: dict[str, UdpTransportTarget] = {}

    async def walk(self, host: str, community: str, oid: str) -> dict[str, str]:
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
