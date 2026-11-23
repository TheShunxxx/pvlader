"""Sensoren: Messwerte, Regelstatus, Session und Statistik."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .coordinator import PvLaderConfigEntry, PvLaderCoordinator
from .entity import PvLaderEntity

PLUG_OPTIONS = ["a", "b", "c"]  # Translation-Keys müssen klein geschrieben sein

type ValueFn = Callable[[PvLaderCoordinator], StateType | datetime]


@dataclass(frozen=True, kw_only=True)
class PvLaderSensorDescription(SensorEntityDescription):
    value_fn: ValueFn
    attrs_fn: Callable[[PvLaderCoordinator], dict[str, Any]] | None = None
    last_reset_fn: Callable[[PvLaderCoordinator], datetime | None] | None = None


def _session(attr: str, scale: float = 1.0) -> ValueFn:
    def fn(c: PvLaderCoordinator) -> float | None:
        sess = c.display_session
        return None if sess is None else getattr(sess, attr) * scale

    return fn


def _session_attrs(c: PvLaderCoordinator) -> dict[str, Any]:
    sess = c.display_session
    if sess is None:
        return {}
    return {
        "start": sess.start.isoformat(),
        "end": sess.end.isoformat() if sess.end else None,
        "solar_kwh": round(sess.solar_wh / 1000, 3),
        "grid_kwh": round(sess.grid_wh / 1000, 3),
        "max_power_w": round(sess.max_power_w),
        # Opportunitätskosten: entgangene Einspeisevergütung für den Solaranteil
        "feedin_lost_eur": round(sess.solar_wh / 1000 * c.feedin_price, 2),
    }


def _reason_attrs(c: PvLaderCoordinator) -> dict[str, Any]:
    d = c.data
    cmd = d.command
    planned = {k: v for k, v in (("phases", cmd.phases), ("current", cmd.current), ("enable", cmd.enable)) if v is not None}
    return {
        # Kommando dieses Zyklus (bei Steuerung aus: nur angezeigt, nicht geschrieben)
        "planned_command": planned or None,
        # Sollzustand des Reglers
        "target_enabled": d.target.enabled,
        "target_phases": d.target.phases,
        "target_current": d.target.current,
        "control_enabled": d.control_enabled,
        "effective_mode": d.info.effective_mode,
        "guard_remaining_s": round(d.info.guard_remaining_s),
        "soc_limit_reached": d.info.soc_limit_reached,
        "grid_power_w": d.grid_power_w,
        "pv_power_w": d.pv_power_w,
        "soc": d.soc,
        "vehicle_plugged": d.vehicle_plugged,
        "wallbox_state_code": d.wallbox.state_code,
        "auth_waiting": d.wallbox.auth_waiting,
    }


def _stats_solar(c: PvLaderCoordinator) -> float | None:
    t = c.tracker
    return round(t.total_solar_kwh / t.total_kwh * 100, 1) if t.total_kwh > 0 else None


def _phase(kind: str, idx: int, unit: str, dc: SensorDeviceClass) -> PvLaderSensorDescription:
    return PvLaderSensorDescription(
        key=f"{kind}_l{idx + 1}",
        translation_key=f"{kind}_l{idx + 1}",
        native_unit_of_measurement=unit,
        device_class=dc,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=1 if kind == "voltage" else 2,
        value_fn=lambda c, i=idx, k=kind: (c.data.wallbox.voltages if k == "voltage" else c.data.wallbox.currents)[i],
    )


SENSORS: tuple[PvLaderSensorDescription, ...] = (
    PvLaderSensorDescription(
        key="charge_power",
        translation_key="charge_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda c: c.data.wallbox.power_w,
    ),
    PvLaderSensorDescription(
        key="charge_current",
        translation_key="charge_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda c: max(c.data.wallbox.currents),
    ),
    PvLaderSensorDescription(
        key="phases_active",
        translation_key="phases_active",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: c.data.wallbox.phases_active,
    ),
    PvLaderSensorDescription(
        key="plug_status",
        translation_key="plug_status",
        device_class=SensorDeviceClass.ENUM,
        options=PLUG_OPTIONS,
        value_fn=lambda c: c.data.wallbox.plug.lower() if c.data.wallbox.plug else None,
    ),
    PvLaderSensorDescription(
        key="control_reason",
        translation_key="control_reason",
        value_fn=lambda c: c.data.info.reason[:255],
        attrs_fn=_reason_attrs,
    ),
    PvLaderSensorDescription(
        key="surplus",
        translation_key="surplus",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda c: c.data.info.surplus_w,
    ),
    # Session-Energie: state_class TOTAL mit last_reset = Session-Start (setzt je Session neu an)
    PvLaderSensorDescription(
        key="session_energy",
        translation_key="session_energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=_session("energy_wh", 0.001),
        last_reset_fn=lambda c: c.display_session.start if c.display_session else None,
        attrs_fn=_session_attrs,
    ),
    PvLaderSensorDescription(
        key="session_solar_percent",
        translation_key="session_solar_percent",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=_session("solar_share", 100.0),
    ),
    PvLaderSensorDescription(
        key="session_cost",
        translation_key="session_cost",
        native_unit_of_measurement="EUR",
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
        value_fn=_session("cost_eur"),
        attrs_fn=_session_attrs,
    ),
    PvLaderSensorDescription(
        key="remaining_time",
        translation_key="remaining_time",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        suggested_display_precision=0,
        value_fn=lambda c: c.data.remaining_min,
    ),
    PvLaderSensorDescription(
        key="planned_start",
        translation_key="planned_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda c: c.data.info.planned_start,
    ),
    PvLaderSensorDescription(
        key="total_energy",
        translation_key="total_energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        value_fn=lambda c: c.data.wallbox.total_wh / 1000,
    ),
    PvLaderSensorDescription(
        key="stats_total_kwh",
        translation_key="stats_total_kwh",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        value_fn=lambda c: c.tracker.total_kwh,
    ),
    PvLaderSensorDescription(
        key="stats_solar_percent",
        translation_key="stats_solar_percent",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=_stats_solar,
    ),
    PvLaderSensorDescription(
        key="stats_total_cost",
        translation_key="stats_total_cost",
        native_unit_of_measurement="EUR",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=lambda c: c.tracker.total_cost,
    ),
    PvLaderSensorDescription(
        key="stats_count",
        translation_key="stats_count",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda c: c.tracker.count,
    ),
    *(_phase("voltage", i, UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE) for i in range(3)),
    *(_phase("current", i, UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT) for i in range(3)),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: PvLaderConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(PvLaderSensor(entry.runtime_data, desc) for desc in SENSORS)


class PvLaderSensor(PvLaderEntity, SensorEntity):
    entity_description: PvLaderSensorDescription

    def __init__(self, coordinator: PvLaderCoordinator, description: PvLaderSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType | datetime:
        return self.entity_description.value_fn(self.coordinator)

    @property
    def last_reset(self) -> datetime | None:
        fn = self.entity_description.last_reset_fn
        return fn(self.coordinator) if fn else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self.coordinator) if fn else None
