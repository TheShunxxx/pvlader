"""Szenario-Tests für die Laderegelung mit einfacher Wallbox-/Hausnetz-Simulation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pytest

from pvlader.controller import Command, ControllerInputs, ControllerState, StatusInfo, power_at, step

T0 = datetime(2026, 11, 23, 8, 0)
V = 230.0


@dataclass
class Sim:
    """Geschlossener Regelkreis: Wallbox setzt Commands um, Netzleistung folgt daraus."""

    mode: str = "pv"
    pv: float | Callable[[int], float] = 0.0
    house: float = 0.0
    plug: str = "B"
    soc: float | None = None
    limit_soc: float = 100.0
    meter_down: bool = False
    plan_active: bool = False
    plan_target_soc: float | None = None
    plan_time: datetime | None = None
    battery_kwh: float = 77.0
    efficiency: float = 0.9
    # Wallbox
    enabled: bool = False
    phases: int = 1
    current: float = 6.0
    # Regler
    state: ControllerState = field(default_factory=ControllerState)
    t: int = 0
    history: list[tuple[int, Command, StatusInfo, bool, int, float]] = field(default_factory=list)

    def pv_power(self) -> float:
        return self.pv(self.t) if callable(self.pv) else self.pv

    def charge_power(self) -> float:
        if not self.enabled or self.plug == "A":
            return 0.0
        return self.current * V * self.phases

    def tick(self) -> tuple[Command, StatusInfo]:
        p = self.charge_power()
        grid = self.house + p - self.pv_power()
        inp = ControllerInputs(
            now=T0 + timedelta(seconds=self.t),
            mode=self.mode,
            grid_power_w=None if self.meter_down else grid,
            charge_power_w=p,
            plug="A" if self.plug == "A" else ("C" if p > 0 else "B"),
            connected=self.plug != "A",
            charging=p > 0,
            active_phases=self.phases if p > 0 else 0,
            enabled=self.enabled,
            current_setpoint=float(int(self.current)),
            soc=self.soc,
            limit_soc=self.limit_soc,
            plan_active=self.plan_active,
            plan_target_soc=self.plan_target_soc,
            plan_time=self.plan_time,
            battery_kwh=self.battery_kwh,
        )
        cmd, self.state, info = step(inp, self.state)
        if cmd.phases is not None:
            self.phases = cmd.phases
        if cmd.current is not None:
            assert cmd.current >= 6.0
            self.current = cmd.current
        if cmd.enable is not None:
            self.enabled = cmd.enable
            if not cmd.enable:
                self.current = 6.0  # echte Box: 1001 fällt beim Abschalten auf 6 A
        # SoC fortschreiben
        if self.soc is not None and p > 0:
            self.soc = min(100.0, self.soc + p * 10 / 3600 / 1000 * self.efficiency / self.battery_kwh * 100)
        self.history.append((self.t, cmd, info, self.enabled, self.phases, self.current))
        self.t += 10
        return cmd, info

    def run(self, seconds: int) -> None:
        end = self.t + seconds
        while self.t < end:
            self.tick()

    def run_until(self, t: int) -> None:
        """Läuft bis einschließlich Tick bei Sekunde t."""
        while self.t <= t:
            self.tick()

    def enabled_at(self, t: int) -> bool:
        return next(h[3] for h in self.history if h[0] == t)

    def switch_times(self) -> list[tuple[int, bool]]:
        out, last = [], None
        for t, _, _, en, _, _ in self.history:
            if en != last:
                out.append((t, en))
                last = en
        return out


def inputs(**kw) -> ControllerInputs:
    base = dict(
        now=T0, mode="pv", grid_power_w=0.0, charge_power_w=0.0, plug="B", connected=True,
        charging=False, active_phases=0, enabled=False,
    )
    base.update(kw)
    return ControllerInputs(**base)


# --- Modi ---------------------------------------------------------------------


def test_mode_off_disables_immediately_despite_guard():
    sim = Sim(mode="now")
    sim.tick()
    assert sim.enabled
    sim.mode = "off"
    cmd, info = sim.tick()  # nur 10 s nach dem Einschalten
    assert cmd.enable is False and not sim.enabled
    assert info.reason == "Aus"


def test_mode_now_full_power_three_phases():
    sim = Sim(mode="now", pv=0, house=500)
    cmd, info = sim.tick()
    assert cmd == Command(enable=True, current=16.0, phases=3)
    assert info.effective_mode == "now"
    cmd, _ = sim.tick()
    assert cmd.empty


def test_now_after_off_rewrites_current():
    # Box fällt beim Abschalten auf 6 A -> beim erneuten Sofortladen 16 A neu schreiben
    sim = Sim(mode="now")
    sim.tick()
    sim.mode = "off"
    sim.tick()
    assert sim.current == 6.0
    sim.mode = "now"
    cmd, _ = sim.tick()
    assert cmd.enable is True and cmd.current == 16.0
    assert sim.current == 16.0


def test_current_resync_when_box_reports_other_setpoint():
    st = ControllerState(enabled=True, phases=3, current=16.0, mode="now")
    cmd, st, _ = step(
        inputs(mode="now", charge_power_w=4200, charging=True, active_phases=3, enabled=True,
               configured_phases=3, current_setpoint=6.0),
        st,
    )
    assert cmd == Command(current=16.0)
    # Box meldet 16 A -> nichts schreiben
    cmd, st, _ = step(
        inputs(mode="now", charge_power_w=11000, charging=True, active_phases=3, enabled=True,
               configured_phases=3, current_setpoint=16.0),
        st,
    )
    assert cmd.empty


def test_mode_minpv_enables_with_min_and_follows_surplus():
    sim = Sim(mode="minpv", pv=0)
    cmd, _ = sim.tick()
    assert cmd.enable is True and cmd.current == 6.0 and cmd.phases == 1
    sim.pv = 2300 + 0  # 10 A bei 1 Phase
    sim.run(30)
    assert sim.enabled and sim.phases == 1
    assert sim.current == pytest.approx(10.0)


def test_mode_pv_without_surplus_stays_off():
    sim = Sim(mode="pv", pv=500)
    sim.run(600)
    assert not sim.enabled
    assert sim.history[-1][2].reason == "PV: zu wenig Überschuss"


def test_unknown_mode_raises():
    with pytest.raises(ValueError):
        step(inputs(mode="turbo"), ControllerState())


# --- PV Start/Stopp -------------------------------------------------------------


def test_pv_start_only_after_enable_delay():
    sim = Sim(pv=2000)
    sim.run_until(60)
    assert not sim.enabled_at(50)
    assert sim.enabled_at(60)
    assert "Start in 10 s" in sim.history[5][2].reason
    _, cmd, *_ = sim.history[6]
    assert cmd.enable is True and cmd.phases == 1
    assert cmd.current == pytest.approx(2000 / V, abs=0.01)


def test_pv_start_resets_timer_if_surplus_interrupted():
    sim = Sim(pv=lambda t: 0 if t == 40 else 2000)
    sim.run_until(110)
    assert not sim.enabled_at(100)
    assert sim.enabled_at(110)  # Timer ab 50 s neu


def test_pv_stop_only_after_disable_delay():
    sim = Sim(pv=3000)
    sim.run_until(500)  # gestartet bei 60, Guard bis 360 vorbei
    assert sim.enabled
    sim.pv = 500
    sim.run_until(700)
    # Überschuss fällt ab Tick 510 -> Stopp genau 180 s später
    assert sim.enabled_at(680)
    assert not sim.enabled_at(690)
    # während der Wartezeit Strom auf Minimum
    assert next(h[5] for h in sim.history if h[0] == 520) == 6.0


def test_pv_cloud_passing_does_not_stop():
    sim = Sim(pv=lambda t: 400 if 600 <= t < 720 else 3000)
    sim.run_until(1200)
    assert sim.switch_times() == [(0, False), (60, True)]
    assert sim.current == pytest.approx(3000 / V, abs=0.01)


def test_pv_current_follows_surplus_with_threshold():
    st = ControllerState(enabled=True, phases=1, current=10.0)
    # 10,3 A Überschuss -> keine Änderung (< 0,5 A)
    cmd, st, _ = step(inputs(grid_power_w=-0.3 * V, charge_power_w=10 * V, charging=True, active_phases=1, enabled=True), st)
    assert cmd.current is None and st.current == 10.0
    # 10,6 A -> ändern
    cmd, st, _ = step(inputs(grid_power_w=-0.6 * V, charge_power_w=10 * V, charging=True, active_phases=1, enabled=True), st)
    assert cmd.current == pytest.approx(10.6)
    # Kappung auf max
    cmd, st, _ = step(inputs(grid_power_w=-5000, charge_power_w=10.6 * V, charging=True, active_phases=1, enabled=True), st)
    assert cmd.current == 16.0


def test_small_change_to_bound_is_written():
    st = ControllerState(enabled=True, phases=1, current=6.3)
    cmd, st, _ = step(inputs(grid_power_w=100, charge_power_w=6.3 * V, charging=True, active_phases=1, enabled=True), st)
    assert cmd.current == 6.0


# --- Phasen ---------------------------------------------------------------------


def test_phase_switch_up_and_down():
    sim = Sim(pv=2500)
    sim.run_until(400)
    assert sim.enabled and sim.phases == 1
    sim.pv = 6000
    sim.run_until(470)
    assert next(h[4] for h in sim.history if h[0] == 460) == 1
    assert next(h[4] for h in sim.history if h[0] == 470) == 3  # 60 s nach Anstieg bei 410
    assert sim.current == pytest.approx(6000 / (3 * V), abs=0.01)
    sim.run_until(800)  # Guard ab 470 vorbei
    sim.pv = 2500
    sim.run_until(1000)
    # Abfall ab Tick 810 -> nach 180 s (990) zurück auf 1 Phase, nicht aus
    assert next(h[4] for h in sim.history if h[0] == 980) == 3
    assert next(h[4] for h in sim.history if h[0] == 990) == 1
    assert sim.enabled
    assert sim.current == pytest.approx(2500 / V, abs=0.01)


def test_pv_start_directly_with_three_phases_when_surplus_high():
    sim = Sim(pv=9000)
    sim.run_until(60)
    _, cmd, *_ = sim.history[6]
    assert cmd.enable is True and cmd.phases == 3


def test_pv_start_switches_back_to_one_phase_after_now_mode():
    sim = Sim(mode="now")
    sim.run_until(400)
    assert sim.phases == 3
    sim.mode = "off"
    sim.run_until(800)
    sim.mode = "pv"
    sim.pv = 2000
    sim.run_until(900)
    assert sim.enabled and sim.phases == 1


def test_three_phase_low_surplus_goes_to_one_phase_before_stop():
    sim = Sim(pv=9000)
    sim.run_until(400)
    assert sim.phases == 3
    sim.pv = 0
    sim.run_until(1200)
    # erst 1 Phase (410+180=590), dann Stopp nach erneut 180 s und Guard (590+300=890)
    assert next(h[4] for h in sim.history if h[0] == 590) == 1
    assert sim.enabled_at(880)
    assert not sim.enabled_at(890)


# --- Guard ----------------------------------------------------------------------


def test_guard_prevents_flapping():
    sim = Sim(pv=lambda t: 0 if 70 <= t < 370 else 3000)
    sim.run_until(700)
    # Start 60; Überschuss weg ab 70 -> Stopp-Timer fertig bei 250, Guard bis 360
    assert sim.enabled_at(350)
    reason_250 = next(h[2].reason for h in sim.history if h[0] == 250)
    assert "Schaltsperre noch 110 s" in reason_250
    assert not sim.enabled_at(360)
    # Überschuss wieder ab 370 -> Start-Timer fertig bei 430, Guard bis 660
    assert not sim.enabled_at(650)
    assert sim.enabled_at(660)
    assert sim.switch_times() == [(0, False), (60, True), (360, False), (660, True)]


def test_guard_also_blocks_phase_switch():
    sim = Sim(pv=2500)
    sim.run_until(70)
    sim.pv = 6000
    sim.run_until(400)
    # Phasen-Timer bei 140 fertig, aber Guard bis 360
    assert next(h[4] for h in sim.history if h[0] == 350) == 1
    assert next(h[4] for h in sim.history if h[0] == 360) == 3


def test_now_ignores_guard_after_pv_stop():
    # PV hat gerade abgeschaltet (Guard läuft) -> Sofortladen startet trotzdem sofort
    sim = Sim(pv=lambda t: 0 if t >= 70 else 3000)
    sim.run_until(400)
    assert not sim.enabled and sim.state.last_switch is not None
    sim.mode = "now"
    cmd, info = sim.tick()
    assert cmd == Command(enable=True, current=16.0, phases=3)
    assert "Schaltsperre" not in info.reason


def test_mode_change_lifts_guard_for_pv_start():
    # Aus -> Sofort -> Aus -> PV: der PV-Start wartet nur die Startverzögerung ab
    sim = Sim(mode="now")
    sim.tick()
    sim.mode = "off"
    sim.tick()
    sim.mode = "pv"
    sim.pv = 3000
    sim.run(70)
    assert sim.enabled
    assert all("Schaltsperre" not in h[2].reason for h in sim.history)


def test_plan_start_ignores_guard():
    st = ControllerState(enabled=False, phases=1, mode="pv", last_switch=T0 - timedelta(seconds=10))
    cmd, _, info = step(
        inputs(soc=50.0, plan_active=True, plan_target_soc=80.0, plan_time=T0 + timedelta(hours=1)), st
    )
    assert cmd.enable is True and cmd.phases == 3
    assert info.plan_charging


# --- SoC-Limit ------------------------------------------------------------------


def test_soc_limit_with_hysteresis():
    sim = Sim(mode="now", soc=79.0, limit_soc=80.0, battery_kwh=1000)
    sim.tick()
    assert sim.enabled
    sim.soc = 80.0
    cmd, info = sim.tick()  # sofort, trotz Guard
    assert cmd.enable is False and info.soc_limit_reached
    assert "SoC-Limit 80 %" in info.reason
    sim.soc = 78.5
    sim.run(600)
    assert not sim.enabled  # innerhalb Hysterese
    sim.soc = 77.9
    sim.tick()
    assert sim.enabled and not sim.state.soc_limit_reached


def test_soc_none_is_ignored():
    sim = Sim(mode="now", soc=None, limit_soc=50)
    sim.tick()
    assert sim.enabled


# --- Fahrzeug -------------------------------------------------------------------


def test_plug_a_disables_and_resets():
    sim = Sim(pv=3000)
    sim.run_until(30)
    assert sim.state.enable_timer is not None
    sim.plug = "A"
    sim.tick()
    assert sim.state.enable_timer is None and sim.state.phase_up_timer is None
    sim.plug = "B"
    sim.run_until(60)
    assert not sim.enabled  # Timer wurde zurückgesetzt


def test_unplug_while_charging_and_new_car_now_starts_without_guard():
    sim = Sim(mode="now")
    sim.tick()
    assert sim.enabled
    sim.plug = "A"
    cmd, info = sim.tick()
    assert cmd.enable is False and info.reason == "Kein Fahrzeug"
    assert sim.state.last_switch is None
    sim.plug = "B"
    cmd, _ = sim.tick()
    assert cmd.enable is True


# --- Zählerausfall --------------------------------------------------------------


def test_meter_failure_pv_stops_after_delay():
    sim = Sim(pv=3000)
    sim.run_until(500)
    sim.meter_down = True
    cmd, info = sim.tick()  # t=510
    assert cmd.current == 6.0 and info.surplus_w is None
    assert info.reason.startswith("Netzzähler nicht verfügbar")
    sim.run_until(700)
    assert sim.enabled_at(680)
    assert not sim.enabled_at(690)


def test_meter_failure_pv_does_not_start():
    sim = Sim(pv=5000, meter_down=True)
    sim.run(600)
    assert not sim.enabled


def test_meter_failure_minpv_min_current_one_phase():
    sim = Sim(mode="minpv", pv=9000)
    sim.run_until(400)
    assert sim.phases == 3
    sim.meter_down = True
    sim.run_until(800)
    assert sim.enabled and sim.phases == 1 and sim.current == 6.0


def test_meter_failure_now_unaffected():
    sim = Sim(mode="now", meter_down=True)
    sim.run(60)
    assert sim.enabled and sim.phases == 3 and sim.current == 16.0


# --- Resync ---------------------------------------------------------------------


def test_resync_reenables_if_box_reports_off():
    st = ControllerState(enabled=True, phases=3, current=16.0)
    cmd, st, _ = step(inputs(mode="now", enabled=False), st)
    assert cmd.enable is True
    assert st.last_switch is None  # kein Schaltvorgang im Sinne des Guards


def test_initial_state_adopted_from_measurement():
    inp = inputs(mode="pv", grid_power_w=-3000, charge_power_w=3 * 7 * V, charging=True, active_phases=3, enabled=True)
    cmd, st, _ = step(inp, ControllerState())
    assert st.enabled is True and st.phases == 3
    assert cmd.enable is None and cmd.phases is None


def test_initial_phases_from_register_without_charging():
    # Box steht laut Register 1002 auf 1 Phase, lädt aber nicht – kein unnötiger Phasen-Write
    inp = inputs(mode="pv", grid_power_w=0.0, configured_phases=1)
    cmd, st, _ = step(inp, ControllerState())
    assert st.phases == 1 and cmd.phases is None


# --- Plan -----------------------------------------------------------------------


def test_plan_starts_in_time_and_continues_normally():
    deadline = T0 + timedelta(hours=6)
    sim = Sim(mode="pv", pv=0, soc=50.0, plan_active=True, plan_target_soc=80.0, plan_time=deadline)
    _, info = sim.tick()
    planned = info.planned_start
    energy_kwh = 0.3 * 77 / 0.9
    expected = deadline - timedelta(hours=energy_kwh * 1000 / (16 * V * 3)) - timedelta(minutes=15)
    assert planned == expected
    assert "Planstart" in info.reason
    start_s = int((planned - T0).total_seconds())

    sim.run_until(6 * 3600)
    first_on = next(t for t, *_, en, _, _ in sim.history if en)
    assert start_s <= first_on <= start_s + 10  # nächster Zyklus nach Planstart
    # durchgehend geladen bis Ziel erreicht (Plan verriegelt)
    reached = next(t for t, _, info, *_ in sim.history if t > first_on and not info.plan_charging)
    assert all(h[3] for h in sim.history if first_on <= h[0] < reached)
    assert T0 + timedelta(seconds=reached) < deadline
    assert sim.soc >= 80.0
    # danach normaler PV-Modus: ohne Überschuss Stopp (nach Verzögerung)
    after = next(h for h in sim.history if h[0] == reached)
    assert after[2].effective_mode == "pv"
    assert not sim.enabled


def test_plan_target_overrides_limit_during_plan():
    deadline = T0 + timedelta(minutes=30)  # 4,3 kWh brauchen ~23 min + 15 min Puffer
    sim = Sim(mode="pv", soc=70.0, limit_soc=70.0, plan_active=True, plan_target_soc=75.0, plan_time=deadline)
    _, info = sim.tick()
    assert info.plan_charging and sim.enabled  # obwohl soc >= limit
    sim.run(3600)
    assert sim.soc >= 75.0
    assert not sim.enabled and sim.state.soc_limit_reached  # danach greift wieder limit 70


def test_plan_ignored_in_off_mode_and_when_inactive():
    deadline = T0 + timedelta(minutes=30)
    sim = Sim(mode="off", soc=10.0, plan_active=True, plan_target_soc=80.0, plan_time=deadline)
    sim.run(60)
    assert not sim.enabled
    sim2 = Sim(mode="pv", soc=10.0, plan_active=False, plan_target_soc=80.0, plan_time=deadline)
    sim2.run(60)
    assert not sim2.enabled


def test_plan_deadline_passed_returns_to_normal():
    deadline = T0 + timedelta(minutes=5)
    sim = Sim(mode="pv", soc=10.0, plan_active=True, plan_target_soc=90.0, plan_time=deadline)
    sim.tick()
    assert sim.enabled and sim.state.plan_charging
    sim.run(400)
    assert not sim.state.plan_charging
    assert sim.history[-1][2].effective_mode == "pv"


# --- Zeitabgleich Netzzähler ------------------------------------------------------


def test_power_at_picks_sample_nearest_to_grid_timestamp():
    s = [(T0, 5.0), (T0 + timedelta(seconds=10), 1634.0), (T0 + timedelta(seconds=20), 3277.0)]
    assert power_at(s, T0 + timedelta(seconds=3), 0.0) == 5.0
    assert power_at(s, T0 + timedelta(seconds=13), 0.0) == 1634.0
    assert power_at(s, T0 + timedelta(seconds=5), 0.0) == 5.0  # Gleichstand -> frühere Probe
    assert power_at(s, None, 42.0) == 42.0
    assert power_at([], T0, 42.0) == 42.0


def test_start_ramp_with_stale_grid_does_not_overshoot():
    # Live 2026-11-24 13:19:01: Box zieht schon 1634 W, Zähler (13:18:54) zeigt noch −1578 W
    # von vor dem Anlauf. Ohne Abgleich: Überschuss 3212 W -> 14 A. Mit Abgleich: ~6,9 A.
    st = ControllerState(enabled=True, phases=1, current=6.86, mode="pv")
    cmd, st, info = step(
        inputs(grid_power_w=-1578, charge_power_w=1634, charge_power_at_grid_w=5, charging=True,
               active_phases=1, enabled=True, configured_phases=1),
        st,
    )
    assert info.surplus_w == pytest.approx(1583)
    assert cmd.current is None  # 6,88 A ≈ bisher 6,86 A -> nichts schreiben
