"""Konfigurationsschlüssel und Defaults der HA-Anbindung (Kernkonstanten: const.py)."""

from __future__ import annotations

from typing import Final

from .const import (
    BATTERY_KWH,
    DISABLE_DELAY,
    ENABLE_DELAY,
    FEEDIN_PRICE,
    GRID_PRICE,
    GUARD,
    MAX_CURRENT,
    MIN_CURRENT,
)

# Config-Entry (data)
CONF_UNIT_ID: Final = "unit_id"
DEFAULT_HOST: Final = ""

# Entitäten (data, per Options überschreibbar)
CONF_GRID_POWER: Final = "grid_power"
CONF_PV_POWER: Final = "pv_power"
CONF_SOC: Final = "soc"
CONF_PLUGGED: Final = "plugged"
ENTITY_KEYS: Final = (CONF_GRID_POWER, CONF_PV_POWER, CONF_SOC, CONF_PLUGGED)

DEFAULT_GRID_POWER: Final = "sensor.bitshake_smartmeterreader_e320_power_in"
DEFAULT_PV_POWER: Final = "sensor.solar_power_current_power"
DEFAULT_SOC: Final = "sensor.tesla_battery_level"
DEFAULT_PLUGGED: Final = "binary_sensor.tesla_plugged_in"

# Options
CONF_BATTERY_KWH: Final = "battery_kwh"
CONF_GRID_PRICE: Final = "grid_price"
CONF_FEEDIN_PRICE: Final = "feedin_price"
CONF_MIN_CURRENT: Final = "min_current"
CONF_MAX_CURRENT: Final = "max_current"
CONF_ENABLE_DELAY: Final = "enable_delay"
CONF_DISABLE_DELAY: Final = "disable_delay"
CONF_GUARD: Final = "guard"

OPTION_DEFAULTS: Final[dict[str, float]] = {
    CONF_BATTERY_KWH: BATTERY_KWH,
    CONF_GRID_PRICE: GRID_PRICE,
    CONF_FEEDIN_PRICE: FEEDIN_PRICE,
    CONF_MIN_CURRENT: MIN_CURRENT,
    CONF_MAX_CURRENT: MAX_CURRENT,
    CONF_ENABLE_DELAY: ENABLE_DELAY,
    CONF_DISABLE_DELAY: DISABLE_DELAY,
    CONF_GUARD: GUARD,
}

# Persistenz
STORAGE_VERSION: Final = 1
SAVE_DELAY: Final = 30  # s

# Service
SERVICE_SET_PLAN: Final = "set_plan"
ATTR_TARGET_SOC: Final = "target_soc"
ATTR_TIME: Final = "time"
ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"

MANUFACTURER: Final = "Dadapower"
