"""Zielzeit-Planung: wann muss spätestens mit voller Leistung gestartet werden."""

from __future__ import annotations

from datetime import datetime, timedelta


def required_energy_kwh(soc: float, target_soc: float, battery_kwh: float, efficiency: float) -> float:
    """Aus dem Netz/der Wallbox benötigte Energie (inkl. Ladeverlusten)."""
    if target_soc <= soc:
        return 0.0
    if efficiency <= 0:
        raise ValueError("efficiency muss > 0 sein")
    return (target_soc - soc) / 100.0 * battery_kwh / efficiency


def charge_duration(energy_kwh: float, max_power_w: float) -> timedelta:
    if max_power_w <= 0:
        raise ValueError("max_power_w muss > 0 sein")
    return timedelta(hours=energy_kwh * 1000.0 / max_power_w)


def plan_start(
    now: datetime,
    soc: float,
    target_soc: float,
    target_time: datetime,
    battery_kwh: float,
    efficiency: float,
    max_power_w: float,
    buffer: timedelta,
) -> datetime | None:
    """Spätester Startzeitpunkt (frühestens `now`); None wenn nichts zu tun oder Zielzeit vorbei."""
    if now >= target_time:
        return None
    energy = required_energy_kwh(soc, target_soc, battery_kwh, efficiency)
    if energy <= 0:
        return None
    start = target_time - charge_duration(energy, max_power_w) - buffer
    return max(start, now)
