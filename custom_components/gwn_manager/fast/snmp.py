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

from .snmp_parse import COLUMNS, TABLES, MibVariant, SnmpClient, build_clients, normalize_mac

# (host, community, oid prefix) -> {OID suffix after the prefix: value}
WalkFn = Callable[[str, str, str], Awaitable[dict[str, str]]]

# (host, community, oid) -> (OID, value) of the next object, None at the end of the MIB
NextFn = Callable[[str, str, str], Awaitable[tuple[str, str] | None]]

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
        next_: NextFn | None = None,
    ) -> None:
        self.host = host
        self.community = community
        self.variant: MibVariant | None = None
        walker = None if walk and next_ else _PysnmpWalker(engine or build_engine())
        self._walk = walk or walker.walk  # type: ignore[union-attr]
        self._next = next_ or walker.next  # type: ignore[union-attr]
        # Client MAC -> (row before it, its row) in the MAC column, to read just that row.
        self._places: dict[str, tuple[str | None, str]] = {}

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


    async def poll_client(self, mac: str) -> SnmpClient | None:
        """Read one client: a GETNEXT per column instead of walking the whole table.

        The row is found once by walking the MAC column and remembered; each call
        checks that it is still there. None: the client isn't on this device.
        Raises SnmpUnavailable if the device doesn't answer.
        """
        if self.variant is None:
            raise SnmpUnavailable("not detected")
        table = TABLES[self.variant]
        columns_ = COLUMNS[self.variant]
        try:
            for _ in range(2):  # the second round after the rows shifted
                if (place := self._places.get(mac)) is None:
                    place = await self._find(mac, f"{table}.{columns_['mac']}")
                    if place is None:
                        return None
                    self._places[mac] = place
                prev, row = place
                values: dict[str, dict[str, str]] = {}
                for name, number in columns_.items():
                    column = f"{table}.{number}"
                    got = await self._next(self.host, self.community, f"{column}.{prev}" if prev else column)
                    if got is None or got[0] != f"{column}.{row}":
                        break
                    values[name] = {row: got[1]}
                else:
                    if normalize_mac(values["mac"][row]) == mac:
                        return build_clients(self.variant, values).get(mac)
                self._places.pop(mac, None)
        except Exception as err:  # noqa: BLE001 - timeouts and odd agent replies alike
            raise SnmpUnavailable(str(err)) from err
        return None

    async def _find(self, mac: str, column: str) -> tuple[str | None, str] | None:
        previous = None
        for row, value in (await self._walk(self.host, self.community, column)).items():
            if normalize_mac(value) == mac:
                return previous, row
            previous = row
        return None


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

    async def _target(self, host: str) -> UdpTransportTarget:
        if host not in self._targets:
            self._targets[host] = await UdpTransportTarget.create(
                (host, 161), timeout=TIMEOUT, retries=0
            )
        return self._targets[host]

    async def next(self, host: str, community: str, oid: str) -> tuple[str, str] | None:
        """One GETNEXT: the object after oid and its value."""
        error, status, _, binds = await next_cmd(
            self._engine,
            CommunityData(community, mpModel=1),
            await self._target(host),
            ContextData(),
            ObjectType(ObjectIdentity(oid)),
            lookupMib=False,
        )
        if error:
            raise TimeoutError(str(error))
        if status or not binds:
            return None
        name, value = binds[0]
        return str(name), value.prettyPrint()

    async def walk(self, host: str, community: str, oid: str) -> dict[str, str]:
        await self._target(host)
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
