"""Laderegelung – rein funktional, zeitbasiert, ohne IO.

`step()` bekommt Messwerte + bisherigen Zustand und liefert ein Kommando,
den neuen Zustand und Statusinformationen für die UI.

Anwendung eines Commands durch den Aufrufer: phases -> current -> enable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from .const import (
    BATTERY_KWH,
    CURRENT_THRESHOLD,
    DISABLE_DELAY,
    EFFICIENCY,
    ENABLE_DELAY,
    GUARD,
    MAX_CURRENT,
    MIN_CURRENT,
    MODE_MINPV,
    MODE_NOW,
    MODE_OFF,
    MODE_PV,
    PLAN_BUFFER,
    POWER_ON_THRESHOLD,
    SOC_HYSTERESIS,
    VOLTAGE,
)
from .planner import plan_start


@dataclass(frozen=True)
class ControllerSettings:
    enable_delay: float = ENABLE_DELAY  # s
    disable_delay: float = DISABLE_DELAY  # s
    guard: float = GUARD  # s
    voltage: float = VOLTAGE
    soc_hysteresis: float = SOC_HYSTERESIS
    efficiency: float = EFFICIENCY
    plan_buffer: timedelta = PLAN_BUFFER
    current_threshold: float = CURRENT_THRESHOLD  # A
    power_on_threshold: float = POWER_ON_THRESHOLD  # W


@dataclass
class ControllerInputs:
    now: datetime
    mode: str
    grid_power_w: float | None  # + Bezug / − Einspeisung; None = Zähler nicht verfügbar
    charge_power_w: float
    plug: str | None  # 'A' | 'B' | 'C' | None
    connected: bool
    charging: bool
    active_phases: int  # gemessen (0 wenn kein Strom fließt)
    enabled: bool  # Register 1000 – nur für Initialisierung/Resync
    soc: float | None = None
    configured_phases: int | None = None  # Register 1002 per FC04 (live bestätigt 2026-11-23)
    # Register 1001 per FC04 in ganzen A. Die Box setzt es beim Abschalten selbst auf 6 A zurück
    # (live beobachtet 2026-11-23) -> Grundlage für den Strom-Resync.
    current_setpoint: float | None = None
    # Ladeleistung zum Messzeitpunkt des Netzzählers (siehe power_at). Der Zähler liefert nur
    # alle ~10 s; ohne Abgleich zählt eine gerade geänderte Ladeleistung doppelt oder gar nicht.
    charge_power_at_grid_w: float | None = None
    limit_soc: float = 100.0
    min_current: float = MIN_CURRENT
    max_current: float = MAX_CURRENT
    plan_active: bool = False
    plan_target_soc: float | None = None
    plan_time: datetime | None = None
    battery_kwh: float = BATTERY_KWH
    settings: ControllerSettings = field(default_factory=ControllerSettings)


@dataclass
class ControllerState:
    enable_timer: datetime | None = None  # seit wann Startbedingung (pv) erfüllt
    disable_timer: datetime | None = None  # seit wann Überschuss < Mindestleistung
    phase_up_timer: datetime | None = None
    phase_down_timer: datetime | None = None
    last_switch: datetime | None = None  # letzter Enable- oder Phasenwechsel (Guard)
    phases: int | None = None  # Soll-Phasen (None = unbekannt)
    current: float | None = None  # zuletzt kommandierter Strom
    enabled: bool | None = None  # zuletzt kommandierter Freigabezustand (None = unbekannt)
    soc_limit_reached: bool = False
    plan_charging: bool = False  # Planladen läuft (verriegelt bis Ziel/Zielzeit)
    mode: str | None = None  # zuletzt gesehener Modus – Moduswechsel hebt die Schaltsperre auf


@dataclass
class Command:
    enable: bool | None = None
    current: float | None = None
    phases: int | None = None

    @property
    def empty(self) -> bool:
        return self.enable is None and self.current is None and self.phases is None


@dataclass
class StatusInfo:
    reason: str
    surplus_w: float | None
    planned_start: datetime | None
    effective_mode: str
    plan_charging: bool
    soc_limit_reached: bool
    guard_remaining_s: float


@dataclass
class _Decision:
    enable: bool
    reason: str
    phases: int | None = None  # None = beibehalten
    power_w: float | None = None  # Strom aus Leistung berechnen
    current: float | None = None  # fester Strom
    # Ausdrücklicher Wunsch (Aus, Sofort/Plan, SoC-Limit, kein Fahrzeug): schaltet ohne Guard.
    # Der Guard bremst nur die automatische PV-Regelung (Start/Stopp/Phasen).
    exempt: bool = False


def power_at(samples: Sequence[tuple[datetime, float]], when: datetime | None, fallback: float) -> float:
    """Ladeleistung aus der Probe, die dem Zeitpunkt `when` am nächsten liegt (bei Gleichstand die frühere)."""
    if when is None or not samples:
        return fallback
    return min(samples, key=lambda s: (abs((s[0] - when).total_seconds()), s[0]))[1]


def _secs(now: datetime, since: datetime | None) -> float:
    return 0.0 if since is None else (now - since).total_seconds()


def _has_vehicle(inp: ControllerInputs) -> bool:
    if inp.plug == "A":
        return False
    if inp.plug in ("B", "C"):
        return True
    return inp.connected


def _reset_timers(st: ControllerState) -> None:
    st.enable_timer = st.disable_timer = st.phase_up_timer = st.phase_down_timer = None


def _update_plan(inp: ControllerInputs, st: ControllerState) -> datetime | None:
    """Aktualisiert st.plan_charging, liefert geplanten Start."""
    s = inp.settings
    usable = (
        inp.plan_active
        and inp.mode in (MODE_PV, MODE_MINPV)
        and inp.plan_time is not None
        and inp.plan_target_soc is not None
        and inp.now < inp.plan_time
    )
    if not usable:
        st.plan_charging = False
        return None
    assert inp.plan_time is not None and inp.plan_target_soc is not None
    if inp.soc is None:
        return None  # Zustand beibehalten (z. B. Fahrzeug schläft kurz)
    if inp.soc >= inp.plan_target_soc:
        st.plan_charging = False
        return None
    start = plan_start(
        inp.now,
        inp.soc,
        inp.plan_target_soc,
        inp.plan_time,
        inp.battery_kwh,
        s.efficiency,
        inp.max_current * s.voltage * 3,
        s.plan_buffer,
    )
    if start is not None and inp.now >= start:
        st.plan_charging = True
    return start


def _phase_target(inp: ControllerInputs, st: ControllerState, surplus: float, phases: int) -> int | None:
    """Automatische Phasenwahl mit Verzögerung; None = keine Änderung."""
    s = inp.settings
    if surplus >= inp.min_current * s.voltage * 3:
        st.phase_down_timer = None
        st.phase_up_timer = st.phase_up_timer or inp.now
        if phases != 3 and _secs(inp.now, st.phase_up_timer) >= s.enable_delay:
            return 3
    else:
        st.phase_up_timer = None
        st.phase_down_timer = st.phase_down_timer or inp.now
        if phases == 3 and _secs(inp.now, st.phase_down_timer) >= s.disable_delay:
            return 1
    return None


def _decide_minpv(inp: ControllerInputs, st: ControllerState, surplus: float) -> _Decision:
    phases = st.phases or 1
    target = _phase_target(inp, st, surplus, phases)
    if st.phases is None:
        target = target or 1
    st.enable_timer = st.disable_timer = None
    return _Decision(enable=True, phases=target, power_w=surplus, reason="Min+PV")


def _decide_pv(inp: ControllerInputs, st: ControllerState, surplus: float) -> _Decision:
    s = inp.settings
    phases = st.phases or 1
    target = _phase_target(inp, st, surplus, phases)

    if st.enabled:
        st.enable_timer = None
        if st.phases is None:
            target = target or 1
        if surplus < inp.min_current * s.voltage * phases:
            st.disable_timer = st.disable_timer or inp.now
            left = s.disable_delay - _secs(inp.now, st.disable_timer)
            if left > 0:
                return _Decision(True, f"PV: zu wenig Überschuss, Stopp in {left:.0f} s", target, power_w=surplus)
            if phases == 3:
                return _Decision(True, "PV: zu wenig Überschuss, Umschaltung auf 1 Phase", 1, power_w=surplus)
            return _Decision(False, "PV: zu wenig Überschuss, Stopp")
        st.disable_timer = None
        return _Decision(True, "PV-Überschussladen", target, power_w=surplus)

    st.disable_timer = None
    if surplus >= inp.min_current * s.voltage:
        st.enable_timer = st.enable_timer or inp.now
        left = s.enable_delay - _secs(inp.now, st.enable_timer)
        if left > 0:
            return _Decision(False, f"PV: Start in {left:.0f} s")
        up_ok = st.phase_up_timer is not None and _secs(inp.now, st.phase_up_timer) >= s.enable_delay
        return _Decision(True, "PV: Start", 3 if up_ok else 1, power_w=surplus)
    st.enable_timer = None
    return _Decision(False, "PV: zu wenig Überschuss")


def step(inp: ControllerInputs, state: ControllerState) -> tuple[Command, ControllerState, StatusInfo]:
    s = inp.settings
    st = replace(state)
    now = inp.now
    charge_ref = inp.charge_power_w if inp.charge_power_at_grid_w is None else inp.charge_power_at_grid_w
    surplus = None if inp.grid_power_w is None else charge_ref - inp.grid_power_w
    measured_on = inp.charging or inp.charge_power_w > s.power_on_threshold

    # Unbekannten Zustand (z. B. nach Neustart) aus Messwerten übernehmen
    if st.enabled is None:
        st.enabled = measured_on or inp.enabled
    if st.phases is None and inp.configured_phases in (1, 3):
        st.phases = inp.configured_phases
    if st.phases is None and measured_on and inp.active_phases > 0:
        st.phases = 1 if inp.active_phases == 1 else 3

    # Moduswechsel durch den Nutzer: frühere Schaltvorgänge sperren nichts mehr
    if st.mode is not None and st.mode != inp.mode:
        st.last_switch = None
    st.mode = inp.mode

    planned_start: datetime | None = None
    effective_mode = inp.mode
    vehicle = _has_vehicle(inp)

    if inp.mode == MODE_OFF:
        _reset_timers(st)
        st.plan_charging = False
        dec = _Decision(False, "Aus", exempt=True)
    elif not vehicle:
        _reset_timers(st)
        st.plan_charging = False
        st.last_switch = None  # neues Fahrzeug -> kein Guard aus alter Session
        dec = _Decision(False, "Kein Fahrzeug", exempt=True)
    else:
        planned_start = _update_plan(inp, st)
        if st.plan_charging:
            effective_mode = MODE_NOW
        limit = inp.plan_target_soc if st.plan_charging and inp.plan_target_soc is not None else inp.limit_soc
        if inp.soc is not None:
            if inp.soc >= limit:
                st.soc_limit_reached = True
            elif inp.soc < limit - s.soc_hysteresis:
                st.soc_limit_reached = False

        if inp.soc is not None and st.soc_limit_reached:
            _reset_timers(st)
            dec = _Decision(False, f"SoC-Limit {limit:.0f} % erreicht", exempt=True)
        elif effective_mode == MODE_NOW:
            _reset_timers(st)
            reason = "Sofortladen"
            if st.plan_charging and inp.plan_time is not None:
                reason = f"Planladen bis {inp.plan_time:%H:%M} auf {inp.plan_target_soc:.0f} %"
            dec = _Decision(True, reason, 3, current=inp.max_current, exempt=True)
        elif effective_mode in (MODE_PV, MODE_MINPV):
            # Zählerausfall: wie Überschuss 0 behandeln (Minimum, verzögerter Stopp, Guard gilt)
            eff = surplus if surplus is not None else 0.0
            dec = _decide_minpv(inp, st, eff) if effective_mode == MODE_MINPV else _decide_pv(inp, st, eff)
            if surplus is None:
                dec.reason = f"Netzzähler nicht verfügbar – {dec.reason}"
            if planned_start is not None:
                dec.reason += f" (Planstart {planned_start:%H:%M})"
        else:
            raise ValueError(f"Unbekannter Modus {inp.mode!r}")

    # --- Command bilden (Guard) --------------------------------------------
    cmd = Command()
    guard_left = 0.0 if st.last_switch is None else max(0.0, s.guard - _secs(now, st.last_switch))
    guard_ok = guard_left <= 0
    blocked = False
    switched = False

    if dec.enable and dec.phases is not None and dec.phases != st.phases:
        if guard_ok or dec.exempt:
            cmd.phases = st.phases = dec.phases
            st.phase_up_timer = st.phase_down_timer = st.disable_timer = None
            switched = True
        else:
            blocked = True

    if dec.enable != st.enabled:
        if guard_ok or dec.exempt:
            cmd.enable = st.enabled = dec.enable
            st.enable_timer = st.disable_timer = None
            switched = True
        else:
            blocked = True
    elif dec.enable and not measured_on and not inp.enabled:
        cmd.enable = True  # Resync: Box meldet aus, obwohl freigegeben sein soll
    elif not dec.enable and (measured_on or inp.enabled):
        cmd.enable = False  # Resync: Box lädt/meldet an, obwohl aus sein soll

    if switched and vehicle:
        st.last_switch = now
        guard_left = s.guard

    if cmd.enable is False:
        st.current = None  # Box fällt beim Abschalten selbst auf 6 A zurück

    # Strom nur bei (künftig) freigegebener Box
    if st.enabled and (dec.current is not None or dec.power_w is not None):
        phases = st.phases or 1
        target = dec.current
        if target is None:
            assert dec.power_w is not None
            target = dec.power_w / (s.voltage * phases)
        target = round(min(inp.max_current, max(inp.min_current, target)), 2)
        diff = abs(target - st.current) if st.current is not None else None
        at_bound = target in (inp.min_current, inp.max_current)
        # Box meldet einen anderen Sollstrom als zuletzt geschrieben (z. B. nach eigenem Reset)
        drifted = (
            inp.enabled
            and inp.current_setpoint is not None
            and st.current is not None
            and abs(inp.current_setpoint - st.current) >= 1
        )
        if (
            diff is None
            or drifted
            or cmd.enable is True
            or cmd.phases is not None
            or diff >= s.current_threshold
            or (diff >= 0.01 and at_bound)
        ):
            cmd.current = st.current = target

    reason = dec.reason
    if blocked:
        reason += f" (Schaltsperre noch {guard_left:.0f} s)"

    info = StatusInfo(
        reason=reason,
        surplus_w=surplus,
        planned_start=planned_start,
        effective_mode=effective_mode,
        plan_charging=st.plan_charging,
        soc_limit_reached=st.soc_limit_reached,
        guard_remaining_s=guard_left,
    )
    return cmd, st, info
