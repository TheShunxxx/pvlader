"""Fixtures für die HA-Tests: gefakte Wallbox (Modbus-Ebene) und Config-Entry."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pvlader.const import DOMAIN
from custom_components.pvlader.ha_const import (
    CONF_ENABLE_DELAY,
    CONF_GRID_POWER,
    CONF_PLUGGED,
    CONF_PV_POWER,
    CONF_SOC,
    CONF_UNIT_ID,
    OPTION_DEFAULTS,
)

SERIAL = "TESTSERIAL01"
FIRMWARE = "1.2.24"
GRID = "sensor.grid_power"
SOC = "sensor.car_soc"


def ascii_regs(text: str, n: int = 6) -> list[int]:
    raw = text.encode().ljust(n * 2, b"\x00")
    return [int.from_bytes(raw[i : i + 2], "big") for i in range(0, n * 2, 2)]


def u_regs(value: int, n: int) -> list[int]:
    return [(value >> (16 * (n - 1 - i))) & 0xFFFF for i in range(n)]


@dataclass
class Resp:
    registers: list[int]

    def isError(self) -> bool:  # noqa: N802 – pymodbus-API
        return False


class FakeWallbox:
    """Registerabbild + Schreibprotokoll; liefert pro Verbindung einen Fake-Modbus-Client."""

    def __init__(self) -> None:
        self.regs: dict[int, int] = {105: 0}
        self.regs.update(zip(range(106, 112), ascii_regs(SERIAL)))
        self.regs.update(zip(range(112, 118), ascii_regs(FIRMWARE)))
        self.regs.update(zip(range(1025, 1029), u_regs(9_587_151, 4)))
        self.regs.update({1000: 0, 1015: 10, 1016: 11})  # Stecker B, nicht freigegeben
        self.writes: list[tuple[int, int]] = []
        self.fail_connect = False

    def set_power(self, watts: int) -> None:
        self.regs.update(zip((1009, 1010), u_regs(watts, 2)))

    def factory(self, host: str, port: int = 502) -> _FakeModbus:
        return _FakeModbus(self)


class _FakeModbus:
    def __init__(self, box: FakeWallbox) -> None:
        self.box = box
        self.connected = False

    async def connect(self) -> bool:
        if self.box.fail_connect:
            return False
        self.connected = True
        return True

    def close(self) -> None:
        self.connected = False

    async def read_input_registers(self, address: int, *, count: int = 1, device_id: int = 1) -> Resp:
        return Resp([self.box.regs.get(address + i, 0) for i in range(count)])

    async def write_register(self, address: int, value: int, *, device_id: int = 1) -> Resp:
        self.box.regs[address] = value
        self.box.writes.append((address, value))
        return Resp([])


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> Generator[None]:
    yield


@pytest.fixture
def wallbox() -> Generator[FakeWallbox]:
    box = FakeWallbox()
    with patch("custom_components.pvlader.dadapower._default_factory", box.factory):
        yield box


@pytest.fixture
def config_entry(hass) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="PV-Lader",
        unique_id=SERIAL,
        data={
            "host": "192.0.2.10",
            "port": 502,
            CONF_UNIT_ID: 1,
            CONF_GRID_POWER: GRID,
            CONF_PV_POWER: None,
            CONF_SOC: SOC,
            CONF_PLUGGED: None,
        },
        options={**OPTION_DEFAULTS, CONF_ENABLE_DELAY: 0, CONF_GRID_POWER: GRID, CONF_SOC: SOC},
    )
    entry.add_to_hass(hass)
    return entry
