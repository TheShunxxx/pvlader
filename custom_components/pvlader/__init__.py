"""pvlader – PV-Überschussladen für die Dadapower-Wallbox (evcc-Ersatz)."""

from __future__ import annotations

from datetime import datetime

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import PvLaderConfigEntry, PvLaderCoordinator
from .dadapower import DadapowerClient, WallboxError
from .frontend import async_register_frontend
from .ha_const import ATTR_CONFIG_ENTRY_ID, ATTR_TARGET_SOC, ATTR_TIME, CONF_UNIT_ID, SERVICE_SET_PLAN
from .websocket import async_register_websocket

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.DATETIME,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SET_PLAN_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_TARGET_SOC): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
        vol.Required(ATTR_TIME): cv.datetime,
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Service, WebSocket-API und Oberfläche (Karte + Seitenreiter) einmalig registrieren."""

    async def _set_plan(call: ServiceCall) -> None:
        coordinator = _coordinator_for(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        when: datetime = call.data[ATTR_TIME]
        # Datums-Selector liefert naive Ortszeit -> zeitzonenbewusst machen
        when = dt_util.as_local(when) if when.tzinfo else when.replace(tzinfo=dt_util.get_default_time_zone())
        await coordinator.async_set_plan(call.data[ATTR_TARGET_SOC], when)

    hass.services.async_register(DOMAIN, SERVICE_SET_PLAN, _set_plan, schema=SET_PLAN_SCHEMA)
    async_register_websocket(hass)
    await async_register_frontend(hass)
    return True


def _coordinator_for(hass: HomeAssistant, entry_id: str | None) -> PvLaderCoordinator:
    entries = [
        e for e in hass.config_entries.async_entries(DOMAIN)
        if e.state is ConfigEntryState.LOADED and (entry_id is None or e.entry_id == entry_id)
    ]
    if len(entries) != 1:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="no_entry" if not entries else "ambiguous_entry",
        )
    return entries[0].runtime_data


async def async_setup_entry(hass: HomeAssistant, entry: PvLaderConfigEntry) -> bool:
    # Start immer nur lesend; Schreibmodus setzt der Coordinator laut gespeichertem Schalter
    client = DadapowerClient(
        entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
        unit_id=entry.data[CONF_UNIT_ID],
        read_only=True,
    )
    try:
        info = await client.read_info()
        coordinator = PvLaderCoordinator(hass, entry, client, info)
        await coordinator.async_load()
    except WallboxError as err:
        await client.close()
        raise ConfigEntryNotReady(f"Wallbox nicht erreichbar: {err}") from err

    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        await client.close()
        raise
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PvLaderConfigEntry) -> bool:
    # Coordinator.async_shutdown (speichern, Verbindung schließen) läuft automatisch per async_on_unload
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: PvLaderConfigEntry) -> None:
    """Optionen geändert -> neu laden."""
    await hass.config_entries.async_reload(entry.entry_id)
