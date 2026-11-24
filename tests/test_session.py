from datetime import datetime, timedelta

import pytest

from pvlader.session import ChargingSession, SessionTracker

T0 = datetime(2026, 11, 23, 12, 0)


def run(tracker, seconds, power, grid, session_wh=None, start=0, connected=True):
    """Ruft update alle 10 s auf; session_wh: Funktion t -> Wh oder None."""
    for t in range(start, start + seconds + 1, 10):
        wh = session_wh(t) if session_wh else None
        tracker.update(T0 + timedelta(seconds=t), connected, power, grid, wh)


def test_session_starts_and_ends_on_plug():
    tr = SessionTracker()
    assert tr.update(T0, False, 0, 0) is None
    assert tr.current is None
    tr.update(T0, True, 0, 0)
    assert tr.current is not None and tr.current.start == T0
    done = tr.update(T0 + timedelta(minutes=5), False, 0, 0)
    assert done is not None and done.end == T0 + timedelta(minutes=5)
    assert tr.current is None and tr.count == 1 and tr.history[0] is done


def test_integration_full_solar():
    tr = SessionTracker(feedin_price=0.08)
    run(tr, 3600, 3600, -500)  # 1 h 3,6 kW, Einspeisung -> alles solar
    s = tr.current
    assert s.energy_wh == pytest.approx(3600)
    assert s.solar_wh == pytest.approx(3600)
    assert s.grid_wh == pytest.approx(0)
    assert s.cost_eur == pytest.approx(3.6 * 0.08)  # wie evcc: Solar zur Einspeisevergütung


def test_integration_partial_grid_and_cost():
    tr = SessionTracker(grid_price=0.3, feedin_price=0.08)
    run(tr, 3600, 4000, 1000)  # 1 kW Netzbezug -> 3 kW solar
    s = tr.current
    assert s.solar_wh == pytest.approx(3000)
    assert s.grid_wh == pytest.approx(1000)
    assert s.cost_eur == pytest.approx(1 * 0.3 + 3 * 0.08)
    assert s.max_power_w == 4000


def test_grid_import_bigger_than_charge_power():
    tr = SessionTracker()
    run(tr, 3600, 2000, 5000)
    assert tr.current.solar_wh == pytest.approx(0)
    assert tr.current.grid_wh == pytest.approx(2000)


def test_grid_unknown_counts_as_grid():
    tr = SessionTracker()
    run(tr, 3600, 2000, None)
    assert tr.current.grid_wh == pytest.approx(2000)


def test_dt_capped_at_60s():
    tr = SessionTracker()
    tr.update(T0, True, 3600, -5000)
    tr.update(T0 + timedelta(seconds=10), True, 3600, -5000)
    tr.update(T0 + timedelta(hours=1), True, 3600, -5000)  # Lücke -> nur 60 s
    assert tr.current.energy_wh == pytest.approx(3600 * 70 / 3600)


def test_register_energy_preferred_and_solar_share_scaled():
    tr = SessionTracker(grid_price=0.3, feedin_price=0.0)
    # Integration ergibt 4 kWh (3 solar / 1 netz), Register meldet 3,8 kWh
    run(tr, 3600, 4000, 1000, session_wh=lambda t: 1000 + 3800 * t / 3600)
    s = tr.current
    assert s.energy_wh == pytest.approx(3800)
    assert s.solar_wh == pytest.approx(3800 * 0.75)
    assert s.grid_wh == pytest.approx(3800 * 0.25)
    assert s.cost_eur == pytest.approx(0.95 * 0.3)


def test_register_reset_after_plug_in():
    tr = SessionTracker()
    tr.update(T0, True, 0, 0, 20000)  # alter Wert der Vorsession
    tr.update(T0 + timedelta(seconds=10), True, 0, 0, 0)  # Box setzt zurück
    tr.update(T0 + timedelta(seconds=20), True, 0, 0, 150)
    assert tr.current.energy_wh == pytest.approx(150)


def test_aggregates_and_history_limit():
    tr = SessionTracker(grid_price=0.3, history_size=3)
    for i in range(5):
        base = i * 7200
        run(tr, 3600, 1000, 1000, start=base)  # 1 kWh, alles Netz
        tr.update(T0 + timedelta(seconds=base + 3700), False, 0, 0)
    assert tr.count == 5
    assert len(tr.history) == 3
    assert tr.history[0].start == T0 + timedelta(seconds=4 * 7200)  # neueste zuerst
    assert tr.total_kwh == pytest.approx(5)
    assert tr.total_grid_kwh == pytest.approx(5)
    assert tr.total_solar_kwh == pytest.approx(0)
    assert tr.total_cost == pytest.approx(1.5)


def test_persistence_roundtrip_including_running_session():
    tr = SessionTracker()
    run(tr, 600, 3000, 0, session_wh=lambda t: t)
    tr.update(T0 + timedelta(seconds=700), False, 0, 0)
    run(tr, 300, 2000, 500, start=1000, session_wh=lambda t: t)
    data = tr.to_dict()
    tr2 = SessionTracker.from_dict(data)
    assert tr2.to_dict() == data
    assert isinstance(tr2.current, ChargingSession)
    assert tr2.history[0].start == tr.history[0].start
    # Fortsetzung nach "Neustart"
    tr2.update(T0 + timedelta(seconds=1310), True, 2000, 500, 1310)
    assert tr2.current.energy_wh > tr.current.energy_wh


def test_from_dict_empty():
    tr = SessionTracker.from_dict(None)
    assert tr.count == 0 and tr.current is None


def test_charge_duration_and_vehicle_values() -> None:
    t0 = T0
    tr = SessionTracker()
    tr.update(t0, True, 0.0, 0.0, soc=40.0, odometer=None, range_km=200.0)
    tr.update(t0 + timedelta(seconds=10), True, 0.0, 0.0, soc=None)  # keine Ladeleistung
    tr.update(t0 + timedelta(seconds=20), True, 7000.0, 0.0, soc=41.0, odometer=39478.5, range_km=204.0)
    tr.update(t0 + timedelta(seconds=30), True, 7000.0, 0.0, soc=None, range_km=None)
    sess = tr.current
    assert sess.charge_s == 20.0  # nur Zyklen mit Leistung > Schwelle
    assert (sess.soc_start, sess.soc_end) == (40.0, 41.0)
    assert (sess.range_start_km, sess.range_end_km) == (200.0, 204.0)
    assert sess.odometer_km == 39478.5
    done = tr.update(t0 + timedelta(seconds=40), False, 0.0, 0.0)
    compact = tr.history_to_list()[0]
    assert "int_wh" not in compact and compact["charge_s"] == 20.0
    assert done.solar_share == 1.0


def test_cost_migration_from_grid_only():
    """Alte Speicherstände (Kosten nur Netz) werden einmalig auf die evcc-Rechnung umgestellt."""
    old = {
        "history": [{"start": "2026-11-20T10:00:00", "end": "2026-11-20T12:00:00", "energy_wh": 10000.0,
                     "solar_wh": 6000.0, "grid_wh": 4000.0, "cost_eur": 1.2}],
        "total_kwh": 10.0, "total_solar_kwh": 6.0, "total_grid_kwh": 4.0, "total_cost": 1.2, "count": 1,
    }
    tr = SessionTracker.from_dict(old, grid_price=0.3, feedin_price=0.08)
    assert tr.history[0].cost_eur == pytest.approx(4 * 0.3 + 6 * 0.08)
    assert tr.total_cost == pytest.approx(1.2 + 6 * 0.08)
    # Neu gespeichert -> kein zweites Mal umrechnen
    again = SessionTracker.from_dict(tr.to_dict(), grid_price=0.3, feedin_price=0.08)
    assert again.total_cost == pytest.approx(tr.total_cost)
    assert again.history[0].cost_eur == pytest.approx(tr.history[0].cost_eur)
