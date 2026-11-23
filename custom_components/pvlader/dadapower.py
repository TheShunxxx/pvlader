"""Modbus-TCP-Client für die Dadapower-Wallbox (ohne Home-Assistant-Abhängigkeit).

Lesen immer per FC04 (Input-Register), Schreiben per FC06, Big Endian.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .const import (
    CURRENT_SCALE,
    DEFAULT_PORT,
    DEFAULT_UNIT_ID,
    FAILSAFE_TIMEOUT,
    MIN_CURRENT,
    PHASE_SWITCH_PAUSE,
    PHASE_SWITCH_SETTLE,
)

_LOGGER = logging.getLogger(__name__)

# Register
REG_FAILSAFE = 102
REG_MODEL = 105
REG_SERIAL = 106  # 6 Register ASCII
REG_FIRMWARE = 112  # 6 Register ASCII
REG_ENABLE = 1000
REG_CURRENT = 1001
REG_PHASES = 1002
REG_VOLTAGES = 1003  # 1003-1005, ÷100 V
REG_CURRENTS = 1006  # 1006-1008, ÷100 A
REG_POWER = 1009  # uint32 W
REG_STATE = 1015
REG_PLUG = 1016
REG_SESSION_WH = 1017  # uint64 Wh
REG_TOTAL_WH = 1025  # uint64 Wh

STATE_AUTH_WAITING = 3
PLUG_CODES = {10: "A", 11: "B", 12: "C"}
PHASE_ACTIVE_CURRENT = 1.0  # A, ab hier zählt eine Phase als aktiv


class WallboxError(Exception):
    """Kommunikations- oder Protokollfehler der Wallbox."""


@dataclass
class WallboxState:
    """Momentaufnahme der Wallbox."""

    plug: str | None  # 'A' | 'B' | 'C' | None (unbekannter Code)
    connected: bool
    charging: bool
    enabled: bool
    current_setpoint_raw: int | None  # Rohwert 1001 per FC04, bewusst nicht interpretiert
    phases_raw: int | None  # Rohwert 1002 per FC04
    phases_active: int  # aus Strömen > 1 A gezählt
    power_w: float
    currents: tuple[float, float, float]
    voltages: tuple[float, float, float]
    session_wh: int
    total_wh: int
    state_code: int  # Rohwert 1015
    plug_code: int  # Rohwert 1016
    auth_waiting: bool


@dataclass
class WallboxInfo:
    model: int
    serial: str
    firmware: str


def decode_uint(regs: Sequence[int]) -> int:
    """Big-Endian-Registerfolge (uint32/uint64 …) zu int."""
    value = 0
    for reg in regs:
        value = (value << 16) | (reg & 0xFFFF)
    return value


def decode_ascii(regs: Sequence[int]) -> str:
    """Register als ASCII (High-Byte zuerst), Nullbytes/Leerzeichen entfernt."""
    raw = b"".join((r & 0xFFFF).to_bytes(2, "big") for r in regs)
    return raw.replace(b"\x00", b"").decode("ascii", errors="replace").strip()


ClientFactory = Callable[..., Any]
SleepFn = Callable[[float], Awaitable[Any]]


def _default_factory(host: str, port: int) -> Any:
    from pymodbus.client import AsyncModbusTcpClient

    return AsyncModbusTcpClient(host, port=port)


@dataclass
class DadapowerClient:
    """Asynchroner Client; alle Zugriffe laufen seriell über ein asyncio.Lock."""

    host: str
    port: int = DEFAULT_PORT
    unit_id: int = DEFAULT_UNIT_ID
    client_factory: ClientFactory | None = None  # für Tests injizierbar
    sleep: SleepFn = asyncio.sleep  # für Tests injizierbar
    # Nur lesen: kein Failsafe-Write beim Verbinden, jeder Schreibzugriff wird abgelehnt
    read_only: bool = False
    _client: Any = field(default=None, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _unit_kw: str | None = field(default=None, init=False, repr=False)

    # --- Verbindung -------------------------------------------------------

    async def connect(self) -> None:
        """Verbindet und schreibt den Failsafe-Timeout (außer im Lesemodus)."""
        async with self._lock:
            await self._ensure_connected()

    async def write_failsafe(self) -> None:
        """Failsafe-Timeout explizit schreiben (z. B. nach Verlassen des Lesemodus)."""
        async with self._lock:
            await self._ensure_connected()
            await self._write(REG_FAILSAFE, FAILSAFE_TIMEOUT)

    async def close(self) -> None:
        async with self._lock:
            await self._drop()

    async def _ensure_connected(self) -> Any:
        if self._client is not None and getattr(self._client, "connected", False):
            return self._client
        await self._drop()
        factory = self.client_factory
        try:
            client = (
                factory(self.host, port=self.port)
                if factory is not None
                else _default_factory(self.host, self.port)
            )
            ok = await client.connect()
        except Exception as err:  # noqa: BLE001 – alles in WallboxError übersetzen
            raise WallboxError(f"Verbindung zu {self.host}:{self.port} fehlgeschlagen: {err}") from err
        if not ok and not getattr(client, "connected", False):
            await self._close_client(client)
            raise WallboxError(f"Verbindung zu {self.host}:{self.port} fehlgeschlagen")
        self._client = client
        self._unit_kw = None
        if self.read_only:
            _LOGGER.debug("Verbunden mit %s:%s (nur lesen)", self.host, self.port)
            return client
        await self._write(REG_FAILSAFE, FAILSAFE_TIMEOUT)
        _LOGGER.debug("Verbunden mit %s:%s, Failsafe %s s", self.host, self.port, FAILSAFE_TIMEOUT)
        return client

    async def _drop(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await self._close_client(client)

    @staticmethod
    async def _close_client(client: Any) -> None:
        try:
            result = client.close()
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001
            pass

    # --- Low-Level --------------------------------------------------------

    def _unit_kwarg(self, method: Callable[..., Any]) -> str:
        """pymodbus <3.10 nutzt `slave=`, neuere `device_id=`."""
        if self._unit_kw is None:
            try:
                params = inspect.signature(method).parameters
            except (TypeError, ValueError):
                params = {}
            self._unit_kw = "slave" if "slave" in params and "device_id" not in params else "device_id"
        return self._unit_kw

    async def _call(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        if self._client is None:
            raise WallboxError("Nicht verbunden")
        method = getattr(self._client, method_name)
        kw = self._unit_kwarg(method)
        try:
            try:
                result = await method(*args, **kwargs, **{kw: self.unit_id})
            except TypeError:
                # Signaturerkennung lag daneben -> anderen Namen probieren
                self._unit_kw = "slave" if kw == "device_id" else "device_id"
                result = await method(*args, **kwargs, **{self._unit_kw: self.unit_id})
        except WallboxError:
            raise
        except Exception as err:  # noqa: BLE001 – Verbindung verwerfen, nächster Aufruf verbindet neu
            await self._drop()
            raise WallboxError(f"Modbus-Fehler bei {method_name}{args}: {err}") from err
        if result is None or (hasattr(result, "isError") and result.isError()):
            raise WallboxError(f"Fehlerantwort bei {method_name}{args}: {result}")
        return result

    async def _read(self, address: int, count: int = 1) -> list[int]:
        result = await self._call("read_input_registers", address, count=count)
        regs = list(getattr(result, "registers", []) or [])
        if len(regs) < count:
            raise WallboxError(f"Zu wenige Register ab {address}: {len(regs)}/{count}")
        return regs[:count]

    async def _write(self, address: int, value: int) -> None:
        if self.read_only:
            raise WallboxError(f"Schreiben auf Register {address} im Lesemodus gesperrt")
        await self._call("write_register", address, int(value))

    # --- API --------------------------------------------------------------

    async def read_state(self) -> WallboxState:
        async with self._lock:
            await self._ensure_connected()
            ctrl = await self._read(REG_ENABLE, 3)  # 1000-1002
            meas = await self._read(REG_VOLTAGES, 8)  # 1003-1010
            stat = await self._read(REG_STATE, 6)  # 1015-1020
            total = await self._read(REG_TOTAL_WH, 4)  # 1025-1028

        voltages = tuple(r / 100 for r in meas[0:3])
        currents = tuple(r / 100 for r in meas[3:6])
        plug_code = stat[1]
        plug = PLUG_CODES.get(plug_code)
        return WallboxState(
            plug=plug,
            connected=plug in ("B", "C"),
            charging=plug == "C",
            enabled=ctrl[0] == 1,
            current_setpoint_raw=ctrl[1],
            phases_raw=ctrl[2],
            phases_active=sum(1 for c in currents if c > PHASE_ACTIVE_CURRENT),
            power_w=float(decode_uint(meas[6:8])),
            currents=currents,  # type: ignore[arg-type]
            voltages=voltages,  # type: ignore[arg-type]
            session_wh=decode_uint(stat[2:6]),
            total_wh=decode_uint(total),
            state_code=stat[0],
            plug_code=plug_code,
            auth_waiting=stat[0] == STATE_AUTH_WAITING,
        )

    async def read_info(self) -> WallboxInfo:
        async with self._lock:
            await self._ensure_connected()
            model = await self._read(REG_MODEL, 1)
            serial = await self._read(REG_SERIAL, 6)
            firmware = await self._read(REG_FIRMWARE, 6)
        return WallboxInfo(model=model[0] & 0xFF, serial=decode_ascii(serial), firmware=decode_ascii(firmware))

    async def set_enabled(self, enabled: bool) -> None:
        async with self._lock:
            await self._ensure_connected()
            await self._write(REG_ENABLE, 1 if enabled else 0)

    async def set_current(self, current: float) -> None:
        """Ladestrom in A (0,01 A Auflösung); < 6 A wird abgelehnt."""
        if current < MIN_CURRENT:
            raise ValueError(f"Ladestrom {current} A unter Minimum {MIN_CURRENT} A")
        raw = round(current * CURRENT_SCALE)
        async with self._lock:
            await self._ensure_connected()
            await self._write(REG_CURRENT, raw)

    async def set_phases(self, phases: int) -> None:
        """Phasen umschalten; bei aktivem Enable mit Aus-/Einschaltsequenz."""
        if phases not in (1, 3):
            raise ValueError(f"Ungültige Phasenzahl {phases}")
        async with self._lock:
            await self._ensure_connected()
            # Register 1000 allein ist evtl. nicht verlässlich -> Laden/Leistung mitprüfen,
            # damit nie unter Last umgeschaltet wird.
            enabled = (await self._read(REG_ENABLE, 1))[0] == 1
            if not enabled:
                plug = (await self._read(REG_PLUG, 1))[0]
                power = decode_uint(await self._read(REG_POWER, 2))
                enabled = plug == 12 or power > 0
            if not enabled:
                await self._write(REG_PHASES, phases)
                return
            await self._write(REG_ENABLE, 0)
            await self.sleep(PHASE_SWITCH_PAUSE)
            await self._write(REG_PHASES, phases)
            await self.sleep(PHASE_SWITCH_SETTLE)
            await self._write(REG_ENABLE, 1)

    async def heartbeat(self) -> int:
        """Liest Register 102 (hält den Failsafe aktiv); schreibt ihn neu, falls abweichend."""
        async with self._lock:
            await self._ensure_connected()
            value = (await self._read(REG_FAILSAFE, 1))[0]
            if value != FAILSAFE_TIMEOUT:
                await self._write(REG_FAILSAFE, FAILSAFE_TIMEOUT)
            return value
