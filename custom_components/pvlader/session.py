"""Ladevorgänge erfassen: Energie, Solaranteil, Kosten, Historie (ohne HA-Abhängigkeit)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from .const import FEEDIN_PRICE, GRID_PRICE, MAX_INTEGRATION_DT, POWER_ON_THRESHOLD, SESSION_HISTORY_SIZE

# 1 = nur Netzanteil × Strompreis; 2 = wie evcc: zusätzlich Solaranteil × Einspeisevergütung
COST_VERSION = 2

# Interne Zähler – nur für die laufende Session nötig, nicht in der Historie
_INTERNAL = ("reg_wh", "reg_seen", "last_session_wh", "int_wh", "int_solar_wh", "last_update")


@dataclass
class ChargingSession:
    start: datetime
    end: datetime | None = None
    energy_wh: float = 0.0
    solar_wh: float = 0.0
    grid_wh: float = 0.0
    cost_eur: float = 0.0
    max_power_w: float = 0.0
    charge_s: float = 0.0  # Zeit mit Ladeleistung > POWER_ON_THRESHOLD
    soc_start: float | None = None
    soc_end: float | None = None
    odometer_km: float | None = None
    range_start_km: float | None = None
    range_end_km: float | None = None
    # interne Zähler (für Fortsetzung nach Neustart mit persistiert)
    reg_wh: float = 0.0  # aufsummierte Register-Deltas
    reg_seen: bool = False
    last_session_wh: float | None = None
    int_wh: float = 0.0  # integrierte Leistung gesamt
    int_solar_wh: float = 0.0
    last_update: datetime | None = None

    @property
    def _int_share(self) -> float:
        return self.int_solar_wh / self.int_wh if self.int_wh > 0 else 0.0

    @property
    def solar_share(self) -> float:
        # Historie hat keine internen Zähler mehr -> aus den Endwerten
        return self.solar_wh / self.energy_wh if self.energy_wh > 0 else self._int_share

    def observe(self, soc: float | None, odometer: float | None, range_km: float | None) -> None:
        """Fahrzeugwerte mitschreiben: erster bekannter Wert = Start, letzter = Ende."""
        if soc is not None:
            if self.soc_start is None:
                self.soc_start = soc
            self.soc_end = soc
        if odometer is not None:
            self.odometer_km = odometer
        if range_km is not None:
            if self.range_start_km is None:
                self.range_start_km = range_km
            self.range_end_km = range_km

    def to_dict(self, *, compact: bool = False) -> dict[str, Any]:
        data = asdict(self)
        for key in ("start", "end", "last_update"):
            if data[key] is not None:
                data[key] = data[key].isoformat()
        if compact:
            for key in _INTERNAL:
                data.pop(key, None)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChargingSession:
        data = dict(data)
        for key in ("start", "end", "last_update"):
            if data.get(key) is not None:
                data[key] = datetime.fromisoformat(data[key])
        known = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class SessionTracker:
    """Beginnt eine Session beim Anstecken, beendet sie beim Abstecken."""

    grid_price: float = GRID_PRICE
    feedin_price: float = FEEDIN_PRICE
    history_size: int = SESSION_HISTORY_SIZE
    current: ChargingSession | None = None
    history: list[ChargingSession] = field(default_factory=list)  # neueste zuerst
    total_kwh: float = 0.0
    total_solar_kwh: float = 0.0
    total_grid_kwh: float = 0.0
    total_cost: float = 0.0
    count: int = 0

    def update(
        self,
        now: datetime,
        connected: bool,
        charge_power_w: float,
        grid_power_w: float | None,
        session_wh: float | None = None,
        *,
        soc: float | None = None,
        odometer: float | None = None,
        range_km: float | None = None,
    ) -> ChargingSession | None:
        """Einmal pro Zyklus aufrufen. Gibt beim Abstecken die fertige Session zurück."""
        if not connected:
            return self._finish(now) if self.current is not None else None

        sess = self.current
        if sess is None:
            sess = self.current = ChargingSession(start=now, last_update=now, last_session_wh=session_wh)
            sess.reg_seen = session_wh is not None
            sess.observe(soc, odometer, range_km)
            self._recalc(sess)
            return None

        sess.observe(soc, odometer, range_km)
        power = max(0.0, charge_power_w)
        sess.max_power_w = max(sess.max_power_w, power)

        # Leistung integrieren (Δt gekappt)
        if sess.last_update is not None:
            dt = min(max(0.0, (now - sess.last_update).total_seconds()), MAX_INTEGRATION_DT)
            if grid_power_w is None:
                solar_p = 0.0  # unbekannt -> konservativ als Netzbezug werten
            else:
                solar_p = max(0.0, min(power, power - max(0.0, grid_power_w)))
            sess.int_wh += power * dt / 3600.0
            sess.int_solar_wh += solar_p * dt / 3600.0
            if power > POWER_ON_THRESHOLD:
                sess.charge_s += dt
        sess.last_update = now

        # Registerzähler: positive Deltas aufsummieren, Rücksprung = Zähler-Reset
        if session_wh is not None:
            if sess.last_session_wh is not None:
                if session_wh >= sess.last_session_wh:
                    sess.reg_wh += session_wh - sess.last_session_wh
                else:
                    sess.reg_wh += session_wh
            sess.reg_seen = True
            sess.last_session_wh = session_wh

        self._recalc(sess)
        return None

    def _recalc(self, sess: ChargingSession) -> None:
        sess.energy_wh = sess.reg_wh if sess.reg_seen else sess.int_wh
        sess.solar_wh = sess.energy_wh * sess._int_share
        sess.grid_wh = sess.energy_wh - sess.solar_wh
        sess.cost_eur = self.cost(sess.grid_wh, sess.solar_wh)

    def cost(self, grid_wh: float, solar_wh: float) -> float:
        """Wie evcc: Netzstrom zum Strompreis, Solarstrom zur entgangenen Einspeisevergütung."""
        return grid_wh / 1000.0 * self.grid_price + solar_wh / 1000.0 * self.feedin_price

    def _finish(self, now: datetime) -> ChargingSession:
        sess = self.current
        assert sess is not None
        self._recalc(sess)
        sess.end = now
        self.current = None
        self.history.insert(0, sess)
        del self.history[self.history_size :]
        self.total_kwh += sess.energy_wh / 1000.0
        self.total_solar_kwh += sess.solar_wh / 1000.0
        self.total_grid_kwh += sess.grid_wh / 1000.0
        self.total_cost += sess.cost_eur
        self.count += 1
        return sess

    def history_to_list(self) -> list[dict[str, Any]]:
        return [s.to_dict(compact=True) for s in self.history]

    def to_dict(self, *, include_history: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "current": self.current.to_dict() if self.current else None,
            "total_kwh": self.total_kwh,
            "total_solar_kwh": self.total_solar_kwh,
            "total_grid_kwh": self.total_grid_kwh,
            "total_cost": self.total_cost,
            "count": self.count,
            "cost_version": COST_VERSION,
        }
        if include_history:
            data["history"] = self.history_to_list()
        return data

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any] | None,
        grid_price: float = GRID_PRICE,
        history_size: int = SESSION_HISTORY_SIZE,
        feedin_price: float = FEEDIN_PRICE,
    ) -> SessionTracker:
        tracker = cls(grid_price=grid_price, feedin_price=feedin_price, history_size=history_size)
        if not data:
            return tracker
        if data.get("current"):
            tracker.current = ChargingSession.from_dict(data["current"])
        tracker.history = [ChargingSession.from_dict(s) for s in data.get("history", [])][:history_size]
        tracker.total_kwh = float(data.get("total_kwh", 0.0))
        tracker.total_solar_kwh = float(data.get("total_solar_kwh", 0.0))
        tracker.total_grid_kwh = float(data.get("total_grid_kwh", 0.0))
        tracker.total_cost = float(data.get("total_cost", 0.0))
        tracker.count = int(data.get("count", 0))
        # Alte Daten (nur Netzkosten) auf evcc-Rechnung umstellen. Historie und Summen liegen in
        # verschiedenen Dateien -> je eigener Versionsstand, damit nichts doppelt umgerechnet wird.
        version = int(data.get("cost_version", 1))
        if version < COST_VERSION:
            tracker.total_cost += tracker.total_solar_kwh * tracker.feedin_price
            if tracker.current is not None:
                tracker._recalc(tracker.current)
        if int(data.get("history_cost_version", version)) < COST_VERSION:
            for sess in tracker.history:
                sess.cost_eur = tracker.cost(sess.grid_wh, sess.solar_wh)
        return tracker
