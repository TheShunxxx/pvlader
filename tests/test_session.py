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
    tr = SessionTracker()
    run(tr, 3600, 3600, -500)  # 1 h 3,6 kW, Einspeisung -> alles solar
    s = tr.current
    assert s.energy_wh == pytest.approx(3600)
    assert s.solar_wh == pytest.approx(3600)
    assert s.grid_wh == pytest.approx(0)
    assert s.cost_eur == pytest.approx(0)


def test_integration_partial_grid_and_cost():
    tr = SessionTracker(grid_price=0.3)
    run(tr, 3600, 4000, 1000)  # 1 kW Netzbezug -> 3 kW solar
    s = tr.current
    assert s.solar_wh == pytest.approx(3000)
    assert s.grid_wh == pytest.approx(1000)
    assert s.cost_eur == pytest.approx(0.3)
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
    tr = SessionTracker(grid_price=0.3)
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
