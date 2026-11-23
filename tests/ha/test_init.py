"""Setup/Unload, Sicherheitsschalter, Regelzyklus, Persistenz, Service."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.pvlader.const import DOMAIN, FAILSAFE_TIMEOUT

from .conftest import GRID, SERIAL, SOC, FakeWallbox


def _eid(hass: HomeAssistant, platform: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{SERIAL}_{key}")
    assert entity_id is not None, key
    return entity_id


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, grid: float = -5000, soc: float = 50) -> None:
    hass.states.async_set(GRID, str(grid), {"unit_of_measurement": "W"})
    hass.states.async_set(SOC, str(soc), {"unit_of_measurement": "%"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def _refresh(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def _unload(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_and_unload(hass: HomeAssistant, wallbox: FakeWallbox, config_entry: MockConfigEntry) -> None:
    await _setup(hass, config_entry)

    assert hass.states.get(_eid(hass, "switch", "control_enabled")).state == STATE_OFF
    assert hass.states.get(_eid(hass, "select", "mode")).state == "pv"
    assert hass.states.get(_eid(hass, "sensor", "total_energy")).state == "9587.151"
    assert hass.states.get(_eid(hass, "sensor", "plug_status")).state == "b"
    assert hass.states.get(_eid(hass, "binary_sensor", "connected")).state == STATE_ON
    assert hass.states.get(_eid(hass, "sensor", "surplus")).state == "5000.0"
    # Diagnose-Sensoren standardmäßig deaktiviert
    assert er.async_get(hass).async_get(_eid(hass, "sensor", "voltage_l1")).disabled_by is not None

    await _unload(hass, config_entry)


async def test_setup_retry_when_wallbox_unreachable(
    hass: HomeAssistant, wallbox: FakeWallbox, config_entry: MockConfigEntry
) -> None:
    wallbox.fail_connect = True
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_control_off_never_writes(
    hass: HomeAssistant, wallbox: FakeWallbox, config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    for _ in range(3):
        await _refresh(hass, config_entry)
    # Heartbeat-Intervall verstreichen lassen
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=65))
    await hass.async_block_till_done()

    assert wallbox.writes == []
    state = hass.states.get(_eid(hass, "sensor", "control_reason"))
    assert state.attributes["control_enabled"] is False
    # Schattenbetrieb zeigt, was er tun würde
    assert state.state.startswith("PV")
    assert state.attributes["effective_mode"] == "pv"

    await _unload(hass, config_entry)


async def test_control_on_writes_failsafe_and_commands(
    hass: HomeAssistant, wallbox: FakeWallbox, config_entry: MockConfigEntry, hass_storage
) -> None:
    await _setup(hass, config_entry)
    await _refresh(hass, config_entry)  # Schattenzustand läuft voraus
    assert wallbox.writes == []

    await hass.services.async_call(
        "switch", "turn_on", {ATTR_ENTITY_ID: _eid(hass, "switch", "control_enabled")}, blocking=True
    )
    await hass.async_block_till_done()

    # Failsafe zuerst, dann frisch geregelt (Zustand zurückgesetzt):
    # Überschuss 5000 W >= 3 × 6 A × 230 V -> 3 Phasen, 5000 / 690 = 7,25 A, Freigabe
    assert wallbox.writes[0] == (102, FAILSAFE_TIMEOUT)
    assert wallbox.writes[1:] == [(1002, 3), (1001, 725), (1000, 1)]
    assert hass.states.get(_eid(hass, "switch", "control_enabled")).state == STATE_ON
    assert hass_storage[f"{DOMAIN}.{config_entry.entry_id}"]["data"]["settings"]["control_enabled"] is True

    # Regelzyklus im PV-Modus: Auto lädt, Überschuss sinkt -> Strom wird nachgeführt
    wallbox.regs.update({1016: 12, 1006: 725, 1007: 725, 1008: 725})
    wallbox.set_power(5000)
    hass.states.async_set(GRID, "-1000", {"unit_of_measurement": "W"})  # Überschuss 6000 W
    wallbox.writes.clear()
    await _refresh(hass, config_entry)
    assert wallbox.writes == [(1001, 870)]  # 6000 / 690 = 8,70 A
    assert hass.states.get(_eid(hass, "sensor", "control_reason")).state == "PV-Überschussladen"

    # Ausschalten: nichts mehr schreiben, auch kein Heartbeat
    await hass.services.async_call(
        "switch", "turn_off", {ATTR_ENTITY_ID: _eid(hass, "switch", "control_enabled")}, blocking=True
    )
    await hass.async_block_till_done()
    wallbox.writes.clear()
    hass.states.async_set(GRID, "3000", {"unit_of_measurement": "W"})
    await _refresh(hass, config_entry)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=65))
    await hass.async_block_till_done()
    assert wallbox.writes == []

    await _unload(hass, config_entry)


async def test_heartbeat_when_control_on(
    hass: HomeAssistant, wallbox: FakeWallbox, config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    await hass.services.async_call(
        "switch", "turn_on", {ATTR_ENTITY_ID: _eid(hass, "switch", "control_enabled")}, blocking=True
    )
    wallbox.regs[102] = 0  # Box meldet 0 (wie in der Live-Probe) -> neu schreiben
    wallbox.writes.clear()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=65))
    await hass.async_block_till_done()
    assert (102, FAILSAFE_TIMEOUT) in wallbox.writes

    await _unload(hass, config_entry)


async def test_restored_control_state_writes_failsafe_on_setup(
    hass: HomeAssistant, wallbox: FakeWallbox, config_entry: MockConfigEntry, hass_storage
) -> None:
    hass_storage[f"{DOMAIN}.{config_entry.entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.{config_entry.entry_id}",
        "data": {"settings": {"control_enabled": True, "mode": "off", "limit_soc": 80}, "sessions": None},
    }
    await _setup(hass, config_entry)
    assert wallbox.writes[0] == (102, FAILSAFE_TIMEOUT)
    assert hass.states.get(_eid(hass, "select", "mode")).state == "off"
    assert float(hass.states.get(_eid(hass, "number", "limit_soc")).state) == 80
    # Modus Aus: Box meldet ohnehin "aus" -> kein weiteres Kommando nötig
    assert all(addr == 102 for addr, _ in wallbox.writes)

    await _unload(hass, config_entry)


async def test_session_and_settings(
    hass: HomeAssistant, wallbox: FakeWallbox, config_entry: MockConfigEntry, hass_storage
) -> None:
    await _setup(hass, config_entry)
    coordinator = config_entry.runtime_data
    assert coordinator.tracker.current is not None  # Stecker B -> Session läuft

    # Einstellungen über Entitäten
    await hass.services.async_call(
        "select", "select_option", {ATTR_ENTITY_ID: _eid(hass, "select", "mode"), "option": "now"}, blocking=True
    )
    await hass.services.async_call(
        "number", "set_value", {ATTR_ENTITY_ID: _eid(hass, "number", "max_current"), "value": 10}, blocking=True
    )
    await hass.async_block_till_done()
    assert coordinator.settings.mode == "now"
    assert coordinator.effective_currents() == (6.0, 10.0)
    await _refresh(hass, config_entry)  # zweite Anfrage liegt im Debounce-Fenster
    reason = hass.states.get(_eid(hass, "sensor", "control_reason"))
    assert reason.state == "Sofortladen"
    assert reason.attributes["target_current"] == 10.0
    assert reason.attributes["target_phases"] == 3
    assert reason.attributes["target_enabled"] is True
    assert wallbox.writes == []  # Steuerung aus

    # Ladeplan per Service (naive Ortszeit)
    target = (dt_util.now() + timedelta(hours=10)).replace(tzinfo=None, microsecond=0)
    await hass.services.async_call(DOMAIN, "set_plan", {"target_soc": 90, "time": target}, blocking=True)
    assert coordinator.settings.plan_active is True
    assert coordinator.settings.plan_target_soc == 90
    assert coordinator.settings.plan_time.tzinfo is not None
    assert hass.states.get(_eid(hass, "switch", "plan_active")).state == STATE_ON

    # Abstecken beendet die Session und speichert sofort
    wallbox.regs[1016] = 10
    await _refresh(hass, config_entry)
    assert coordinator.tracker.current is None
    assert coordinator.tracker.count == 1
    stored = hass_storage[f"{DOMAIN}.{config_entry.entry_id}"]["data"]
    assert stored["sessions"]["count"] == 1
    assert stored["settings"]["plan_target_soc"] == 90
    assert hass.states.get(_eid(hass, "sensor", "stats_count")).state == "1"

    await _unload(hass, config_entry)
