from datetime import datetime, timedelta

import pytest

from pvlader.planner import charge_duration, plan_start, required_energy_kwh

NOW = datetime(2026, 11, 23, 18, 0)
P_MAX = 16 * 230 * 3  # 11040 W


def test_required_energy():
    assert required_energy_kwh(50, 80, 77, 0.9) == pytest.approx(0.3 * 77 / 0.9)
    assert required_energy_kwh(80, 80, 77, 0.9) == 0
    assert required_energy_kwh(90, 80, 77, 0.9) == 0


def test_charge_duration():
    assert charge_duration(11.04, 11040) == timedelta(hours=1)
    with pytest.raises(ValueError):
        charge_duration(1, 0)


def test_plan_start_basic():
    target = NOW + timedelta(hours=10)
    start = plan_start(NOW, 50, 80, target, 77, 0.9, P_MAX, timedelta(minutes=15))
    energy = 0.3 * 77 / 0.9
    expected = target - timedelta(hours=energy * 1000 / P_MAX) - timedelta(minutes=15)
    assert start == expected
    assert NOW < start < target


def test_plan_start_too_late_returns_now():
    target = NOW + timedelta(minutes=30)
    assert plan_start(NOW, 10, 90, target, 77, 0.9, P_MAX, timedelta(minutes=15)) == NOW


def test_plan_start_nothing_to_do_or_past():
    assert plan_start(NOW, 80, 80, NOW + timedelta(hours=1), 77, 0.9, P_MAX, timedelta()) is None
    assert plan_start(NOW, 10, 80, NOW - timedelta(minutes=1), 77, 0.9, P_MAX, timedelta()) is None
