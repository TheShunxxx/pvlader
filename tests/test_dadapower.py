from dataclasses import dataclass

import pytest

from pvlader.const import CURRENT_SCALE, FAILSAFE_TIMEOUT
from pvlader.dadapower import DadapowerClient, WallboxError, decode_ascii, decode_uint


@dataclass
class Resp:
    registers: list[int]
    error: bool = False

    def isError(self) -> bool:  # noqa: N802 – pymodbus-API
        return self.error


def ascii_regs(text: str, n: int = 6) -> list[int]:
    raw = text.encode().ljust(n * 2, b"\x00")
    return [int.from_bytes(raw[i : i + 2], "big") for i in range(0, n * 2, 2)]


def u_regs(value: int, n: int) -> list[int]:
    return [(value >> (16 * (n - 1 - i))) & 0xFFFF for i in range(n)]


class FakeModbus:
    """Imitiert AsyncModbusTcpClient (neue API mit device_id=)."""

    instances: list["FakeModbus"] = []

    def __init__(self, host, port=502, log=None, regs=None, **_):
        self.host, self.port = host, port
        self.connected = False
        self.log = log if log is not None else []
        self.regs: dict[int, int] = regs if regs is not None else {}
        self.fail_next_read = False
        self.unit_ids: list[int] = []
        FakeModbus.instances.append(self)

    async def connect(self) -> bool:
        self.connected = True
        self.log.append(("connect",))
        return True

    def close(self) -> None:
        self.connected = False
        self.log.append(("close",))

    async def read_input_registers(self, address, *, count=1, device_id=1):
        self.unit_ids.append(device_id)
        if self.fail_next_read:
            self.fail_next_read = False
            raise ConnectionError("Verbindung weg")
        return Resp([self.regs.get(address + i, 0) for i in range(count)])

    async def write_register(self, address, value, *, device_id=1):
        self.unit_ids.append(device_id)
        self.regs[address] = value
        self.log.append(("write", address, value))
        return Resp([])


class FakeModbusOld(FakeModbus):
    """Alte pymodbus-API mit slave=."""

    async def read_input_registers(self, address, count=1, slave=1):  # type: ignore[override]
        return await super().read_input_registers(address, count=count, device_id=slave)

    async def write_register(self, address, value, slave=1):  # type: ignore[override]
        return await super().write_register(address, value, device_id=slave)


def make_client(fake_cls=FakeModbus, regs=None, unit_id=1):
    log: list[tuple] = []
    regs = regs if regs is not None else {}
    holder: dict = {}

    def factory(host, port=502):
        holder["fake"] = fake_cls(host, port, log=log, regs=regs)
        return holder["fake"]

    async def fake_sleep(sec):
        log.append(("sleep", sec))

    client = DadapowerClient("192.0.2.1", unit_id=unit_id, client_factory=factory, sleep=fake_sleep)
    return client, log, regs, holder


def test_decoders():
    assert decode_uint([0x0001, 0x0002]) == 65538
    assert decode_uint(u_regs(9_587_151, 4)) == 9_587_151
    assert decode_uint([0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF]) == 2**64 - 1
    assert decode_ascii(ascii_regs("TESTSERIAL01")) == "TESTSERIAL01"
    assert decode_ascii(ascii_regs("1.2.24")) == "1.2.24"


async def test_connect_writes_failsafe():
    client, log, regs, _ = make_client()
    await client.connect()
    assert log == [("connect",), ("write", 102, FAILSAFE_TIMEOUT)]


async def test_read_only_never_writes():
    client, log, regs, _ = make_client(regs={1016: 12})
    client.read_only = True
    await client.connect()
    await client.read_state()
    await client.read_info()
    for call in (client.set_enabled(True), client.set_current(10), client.set_phases(3),
                 client.write_failsafe(), client.heartbeat()):
        with pytest.raises(WallboxError):
            await call
    assert [e for e in log if e[0] == "write"] == []
    # Lesemodus verlassen: Failsafe explizit schreiben
    client.read_only = False
    await client.write_failsafe()
    assert log[-1] == ("write", 102, FAILSAFE_TIMEOUT)


async def test_read_state_decoding():
    regs = {
        1000: 1,
        1001: 6,
        1002: 3,
        1003: 23010, 1004: 22950, 1005: 23100,
        1006: 1012, 1007: 1005, 1008: 50,  # dritte Phase < 1 A
        **dict(zip((1009, 1010), u_regs(70_123, 2))),
        1015: 10,
        1016: 12,
        **dict(zip(range(1017, 1021), u_regs(12_345, 4))),
        **dict(zip(range(1025, 1029), u_regs(9_587_151, 4))),
    }
    client, _, _, _ = make_client(regs=regs)
    st = await client.read_state()
    assert st.plug == "C" and st.connected and st.charging
    assert st.enabled is True
    assert st.current_setpoint_raw == 6 and st.phases_raw == 3
    assert st.voltages == (230.10, 229.50, 231.00)
    assert st.currents == (10.12, 10.05, 0.5)
    assert st.phases_active == 2
    assert st.power_w == 70_123
    assert st.session_wh == 12_345
    assert st.total_wh == 9_587_151
    assert st.state_code == 10 and not st.auth_waiting


@pytest.mark.parametrize(
    ("code", "plug", "connected", "charging"),
    [(10, "A", False, False), (11, "B", True, False), (12, "C", True, True), (99, None, False, False)],
)
async def test_plug_states(code, plug, connected, charging):
    client, _, _, _ = make_client(regs={1016: code, 1015: 3})
    st = await client.read_state()
    assert (st.plug, st.connected, st.charging) == (plug, connected, charging)
    assert st.auth_waiting and st.state_code == 3


async def test_set_current_scaling_and_minimum():
    client, log, _, _ = make_client()
    await client.set_current(10.555)
    assert log[-1] == ("write", 1001, round(10.555 * CURRENT_SCALE))
    await client.set_current(6)
    assert log[-1] == ("write", 1001, 600)
    with pytest.raises(ValueError):
        await client.set_current(5.99)
    assert log[-1] == ("write", 1001, 600)  # nichts geschrieben


async def test_set_enabled():
    client, log, _, _ = make_client()
    await client.set_enabled(True)
    await client.set_enabled(False)
    assert log[-2:] == [("write", 1000, 1), ("write", 1000, 0)]


async def test_phase_switch_sequence_when_enabled():
    client, log, _, _ = make_client(regs={1000: 1})
    await client.connect()
    log.clear()
    await client.set_phases(3)
    assert log == [
        ("write", 1000, 0),
        ("sleep", 5.0),
        ("write", 1002, 3),
        ("sleep", 2.0),
        ("write", 1000, 1),
    ]


async def test_phase_switch_when_charging_but_register_reads_0():
    client, log, _, _ = make_client(regs={1000: 0, 1016: 12})
    await client.connect()
    log.clear()
    await client.set_phases(1)
    assert [e for e in log if e[0] == "write"] == [("write", 1000, 0), ("write", 1002, 1), ("write", 1000, 1)]


async def test_phase_switch_when_disabled_only_writes_phases():
    client, log, _, _ = make_client(regs={1000: 0, 1016: 11})
    await client.connect()
    log.clear()
    await client.set_phases(1)
    assert log == [("write", 1002, 1)]
    with pytest.raises(ValueError):
        await client.set_phases(2)


async def test_heartbeat_reads_and_rewrites_if_needed():
    client, log, regs, _ = make_client()
    await client.connect()
    log.clear()
    assert await client.heartbeat() == FAILSAFE_TIMEOUT
    assert log == []
    regs[102] = 0  # z. B. nach Neustart der Box
    assert await client.heartbeat() == 0
    assert log == [("write", 102, FAILSAFE_TIMEOUT)]


async def test_read_info():
    regs = {105: 0x1205, **dict(zip(range(106, 112), ascii_regs("TESTSERIAL01"))),
            **dict(zip(range(112, 118), ascii_regs("1.2.24")))}
    client, _, _, _ = make_client(regs=regs)
    info = await client.read_info()
    assert info.model == 5
    assert info.serial == "TESTSERIAL01"
    assert info.firmware == "1.2.24"


async def test_reconnect_after_error():
    client, log, _, holder = make_client()
    await client.connect()
    first = holder["fake"]
    first.fail_next_read = True
    with pytest.raises(WallboxError):
        await client.read_state()
    assert not first.connected  # verworfen
    await client.read_state()  # verbindet neu, schreibt Failsafe erneut
    assert holder["fake"] is not first
    assert log.count(("write", 102, FAILSAFE_TIMEOUT)) == 2


async def test_error_response_raises():
    client, _, _, holder = make_client()
    await client.connect()

    async def bad(*a, **k):
        return Resp([], error=True)

    holder["fake"].read_input_registers = bad
    with pytest.raises(WallboxError):
        await client.heartbeat()


@pytest.mark.parametrize("fake_cls", [FakeModbus, FakeModbusOld])
async def test_unit_id_kwarg_variants(fake_cls):
    client, log, _, holder = make_client(fake_cls=fake_cls, unit_id=7)
    await client.set_enabled(True)
    await client.read_state()
    assert set(holder["fake"].unit_ids) == {7}


async def test_unit_id_fallback_on_type_error():
    class OnlySlaveKw(FakeModbus):
        # Signatur per **kwargs verschleiert -> Erkennung rät device_id, TypeError -> slave
        async def read_input_registers(self, address, **kw):  # type: ignore[override]
            if "slave" not in kw:
                raise TypeError("unexpected keyword device_id")
            return await super().read_input_registers(address, count=kw.get("count", 1), device_id=kw["slave"])

        async def write_register(self, address, value, **kw):  # type: ignore[override]
            if "slave" not in kw:
                raise TypeError("unexpected keyword device_id")
            return await super().write_register(address, value, device_id=kw["slave"])

    client, _, _, holder = make_client(fake_cls=OnlySlaveKw, unit_id=3)
    assert await client.heartbeat() == FAILSAFE_TIMEOUT
    assert set(holder["fake"].unit_ids) == {3}


async def test_connect_failure_raises():
    class NoConnect(FakeModbus):
        async def connect(self) -> bool:
            return False

    client, _, _, _ = make_client(fake_cls=NoConnect)
    with pytest.raises(WallboxError):
        await client.connect()


def test_real_pymodbus_client_signature_detection():
    """Prüft gegen die installierte pymodbus-Version, dass ein gültiger Parametername gewählt wird."""
    import inspect

    from pymodbus.client import AsyncModbusTcpClient

    client = DadapowerClient("192.0.2.1")
    kw = client._unit_kwarg(AsyncModbusTcpClient.read_input_registers)
    assert kw in inspect.signature(AsyncModbusTcpClient.read_input_registers).parameters


async def test_real_pymodbus_connect_failure_maps_to_wallbox_error():
    """Echter AsyncModbusTcpClient gegen geschlossenen lokalen Port -> WallboxError, kein Hänger."""
    client = DadapowerClient("127.0.0.1", port=1)
    with pytest.raises(WallboxError):
        await client.connect()
    await client.close()
