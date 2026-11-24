"""WebSocket-API für Karte und Seitenreiter (nur lesend)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, EFFICIENCY, VOLTAGE
from .coordinator import PvLaderCoordinator
from .ha_const import (
    CONF_BATTERY_KWH,
    CONF_FEEDIN_PRICE,
    CONF_GRID_POWER,
    CONF_GRID_PRICE,
    CONF_ODOMETER,
    CONF_PV_POWER,
    CONF_RANGE,
    CONF_SOC,
)

SOURCE_KEYS = (CONF_GRID_POWER, CONF_PV_POWER, CONF_SOC, CONF_ODOMETER, CONF_RANGE)


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_config)
    websocket_api.async_register_command(hass, ws_sessions)


def _loaded(hass: HomeAssistant) -> list[Any]:
    return [e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED]


def _find(hass: HomeAssistant, entry_id: str | None) -> PvLaderCoordinator | None:
    for entry in _loaded(hass):
        if entry_id is None or entry.entry_id == entry_id:
            return entry.runtime_data
    return None


def _entry_info(hass: HomeAssistant, coordinator: PvLaderCoordinator) -> dict[str, Any]:
    entry = coordinator.config_entry
    prefix = f"{entry.unique_id}_"
    entities = {
        ent.unique_id.removeprefix(prefix): ent.entity_id
        for ent in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if ent.unique_id.startswith(prefix) and ent.disabled_by is None
    }
    conf = coordinator.conf
    _, max_current = coordinator.current_bounds()
    return {
        "entry_id": entry.entry_id,
        "title": entry.title,
        "entities": entities,
        "sources": {key: conf.get(key) or None for key in SOURCE_KEYS},
        "grid_price": float(conf[CONF_GRID_PRICE]),
        "feedin_price": float(conf[CONF_FEEDIN_PRICE]),
        "battery_kwh": float(conf[CONF_BATTERY_KWH]),
        "efficiency": EFFICIENCY,
        "max_power_w": max_current * VOLTAGE * 3,
    }


@websocket_api.websocket_command({vol.Required("type"): "pvlader/config"})
@callback
def ws_config(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Alle geladenen Wallboxen mit Entity-IDs und Preisen."""
    entries = [_entry_info(hass, e.runtime_data) for e in _loaded(hass)]
    connection.send_result(msg["id"], {"entries": entries})


@websocket_api.websocket_command(
    {vol.Required("type"): "pvlader/sessions", vol.Optional("entry_id"): str}
)
@callback
def ws_sessions(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Ladehistorie (neueste zuerst) plus laufende Session."""
    coordinator = _find(hass, msg.get("entry_id"))
    if coordinator is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "Keine PV-Lader-Integration geladen")
        return
    t = coordinator.tracker
    connection.send_result(
        msg["id"],
        {
            "entry_id": coordinator.config_entry.entry_id,
            "current": t.current.to_dict(compact=True) if t.current else None,
            "sessions": t.history_to_list(),
            "grid_price": t.grid_price,
            "feedin_price": coordinator.feedin_price,
        },
    )
