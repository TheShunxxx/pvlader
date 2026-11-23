"""Config- und Options-Flow für pvlader."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
)

from .const import DEFAULT_PORT, DEFAULT_UNIT_ID, DOMAIN, MAX_CURRENT, MIN_CURRENT
from .dadapower import DadapowerClient, WallboxError, WallboxInfo
from .ha_const import (
    CONF_BATTERY_KWH,
    CONF_DISABLE_DELAY,
    CONF_ENABLE_DELAY,
    CONF_FEEDIN_PRICE,
    CONF_GRID_POWER,
    CONF_GRID_PRICE,
    CONF_GUARD,
    CONF_MAX_CURRENT,
    CONF_MIN_CURRENT,
    CONF_PLUGGED,
    CONF_PV_POWER,
    CONF_SOC,
    CONF_UNIT_ID,
    DEFAULT_GRID_POWER,
    DEFAULT_HOST,
    DEFAULT_PLUGGED,
    DEFAULT_PV_POWER,
    DEFAULT_SOC,
    ENTITY_KEYS,
    OPTION_DEFAULTS,
)

_LOGGER = logging.getLogger(__name__)

ENTITY_DEFAULTS = {
    CONF_GRID_POWER: DEFAULT_GRID_POWER,
    CONF_PV_POWER: DEFAULT_PV_POWER,
    CONF_SOC: DEFAULT_SOC,
    CONF_PLUGGED: DEFAULT_PLUGGED,
}


async def async_probe(host: str, port: int, unit_id: int) -> WallboxInfo:
    """Verbindung rein lesend testen (kein Failsafe-Write – evcc kann parallel laufen)."""
    client = DadapowerClient(host, port=port, unit_id=unit_id, read_only=True)
    try:
        await client.connect()
        return await client.read_info()
    finally:
        await client.close()


def _num(min_v: float, max_v: float, step: float, unit: str | None = None) -> NumberSelector:
    cfg = NumberSelectorConfig(min=min_v, max=max_v, step=step, mode=NumberSelectorMode.BOX)
    if unit:
        cfg["unit_of_measurement"] = unit
    return NumberSelector(cfg)


def _entities_schema(values: dict[str, Any]) -> vol.Schema:
    """Pflicht: Netz, SoC. Optional: PV, Stecker (leer lassbar -> suggested_value statt default)."""
    sensor = EntitySelector(EntitySelectorConfig(domain="sensor"))
    return vol.Schema(
        {
            vol.Required(CONF_GRID_POWER, default=values.get(CONF_GRID_POWER) or DEFAULT_GRID_POWER): sensor,
            vol.Optional(CONF_PV_POWER, description={"suggested_value": values.get(CONF_PV_POWER)}): sensor,
            vol.Required(CONF_SOC, default=values.get(CONF_SOC) or DEFAULT_SOC): sensor,
            vol.Optional(CONF_PLUGGED, description={"suggested_value": values.get(CONF_PLUGGED)}): EntitySelector(
                EntitySelectorConfig(domain="binary_sensor")
            ),
        }
    )


class PvLaderConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._info: WallboxInfo | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input[CONF_PORT])
            unit_id = int(user_input[CONF_UNIT_ID])
            try:
                info = await async_probe(host, port, unit_id)
            except WallboxError as err:
                _LOGGER.debug("Verbindungstest fehlgeschlagen: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unerwarteter Fehler beim Verbindungstest")
                errors["base"] = "unknown"
            else:
                if not info.serial:
                    errors["base"] = "no_serial"
                else:
                    await self.async_set_unique_id(info.serial)
                    self._abort_if_unique_id_configured(
                        updates={CONF_HOST: host, CONF_PORT: port, CONF_UNIT_ID: unit_id}
                    )
                    self._info = info
                    self._data = {CONF_HOST: host, CONF_PORT: port, CONF_UNIT_ID: unit_id}
                    return await self.async_step_entities()

        defaults = user_input or {}
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, DEFAULT_HOST)): TextSelector(),
                vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): _num(1, 65535, 1),
                vol.Required(CONF_UNIT_ID, default=defaults.get(CONF_UNIT_ID, DEFAULT_UNIT_ID)): _num(0, 255, 1),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_entities(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            data = {**self._data, **{k: user_input.get(k) for k in ENTITY_KEYS}}
            return self.async_create_entry(title="PV-Lader", data=data)
        suggested = {CONF_PV_POWER: DEFAULT_PV_POWER, CONF_PLUGGED: DEFAULT_PLUGGED}
        assert self._info is not None
        return self.async_show_form(
            step_id="entities",
            data_schema=_entities_schema(suggested),
            description_placeholders={"serial": self._info.serial, "firmware": self._info.firmware},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> PvLaderOptionsFlow:
        return PvLaderOptionsFlow()


class PvLaderOptionsFlow(OptionsFlow):
    """Entitäten, Fahrzeug, Preise, Ströme und Verzögerungen."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_MIN_CURRENT] > user_input[CONF_MAX_CURRENT]:
                errors["base"] = "min_gt_max"
            else:
                data = {k: user_input.get(k) for k in ENTITY_KEYS}
                data.update({k: user_input[k] for k in OPTION_DEFAULTS})
                return self.async_create_entry(data=data)

        current = {**OPTION_DEFAULTS, **self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            current.update(user_input)
        schema = _entities_schema(current).extend(
            {
                vol.Required(CONF_BATTERY_KWH, default=current[CONF_BATTERY_KWH]): _num(10, 200, 0.1, "kWh"),
                vol.Required(CONF_GRID_PRICE, default=current[CONF_GRID_PRICE]): _num(0, 2, 0.001, "€/kWh"),
                vol.Required(CONF_FEEDIN_PRICE, default=current[CONF_FEEDIN_PRICE]): _num(0, 2, 0.001, "€/kWh"),
                vol.Required(CONF_MIN_CURRENT, default=current[CONF_MIN_CURRENT]): _num(
                    MIN_CURRENT, MAX_CURRENT, 1, "A"
                ),
                vol.Required(CONF_MAX_CURRENT, default=current[CONF_MAX_CURRENT]): _num(
                    MIN_CURRENT, MAX_CURRENT, 1, "A"
                ),
                vol.Required(CONF_ENABLE_DELAY, default=current[CONF_ENABLE_DELAY]): _num(0, 3600, 1, "s"),
                vol.Required(CONF_DISABLE_DELAY, default=current[CONF_DISABLE_DELAY]): _num(0, 3600, 1, "s"),
                vol.Required(CONF_GUARD, default=current[CONF_GUARD]): _num(0, 3600, 1, "s"),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
