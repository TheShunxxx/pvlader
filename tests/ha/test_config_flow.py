"""Config- und Options-Flow."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pvlader.const import DOMAIN
from custom_components.pvlader.ha_const import CONF_GUARD, CONF_MAX_CURRENT, CONF_MIN_CURRENT, OPTION_DEFAULTS

from .conftest import SERIAL, FakeWallbox

USER_INPUT = {"host": "192.0.2.10", "port": 502, "unit_id": 1}


async def _start(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    return result


async def test_flow_success_read_only(hass: HomeAssistant, wallbox: FakeWallbox) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "entities"

    with patch("custom_components.pvlader.async_setup_entry", return_value=True) as setup:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"grid_power": "sensor.grid_power", "soc": "sensor.car_soc"},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "PV-Lader"
    assert result["result"].unique_id == SERIAL
    assert result["data"] == {
        "host": "192.0.2.10",
        "port": 502,
        "unit_id": 1,
        "grid_power": "sensor.grid_power",
        "pv_power": None,
        "soc": "sensor.car_soc",
        "car_limit": None,
        "plugged": None,
        "odometer": None,
        "range": None,
    }
    assert setup.call_count == 1
    # Verbindungstest darf nichts schreiben (evcc läuft parallel)
    assert wallbox.writes == []


async def test_flow_cannot_connect(hass: HomeAssistant, wallbox: FakeWallbox) -> None:
    wallbox.fail_connect = True
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    # Danach erfolgreich
    wallbox.fail_connect = False
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["step_id"] == "entities"


async def test_flow_duplicate(hass: HomeAssistant, wallbox: FakeWallbox) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data={**USER_INPUT, "host": "192.0.2.99"}).add_to_hass(hass)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "init"

    base = {**OPTION_DEFAULTS, "grid_power": "sensor.grid_power", "soc": "sensor.car_soc"}
    bad = {**base, CONF_MIN_CURRENT: 12, CONF_MAX_CURRENT: 8}
    result = await hass.config_entries.options.async_configure(result["flow_id"], bad)
    assert result["errors"] == {"base": "min_gt_max"}

    result = await hass.config_entries.options.async_configure(result["flow_id"], {**base, CONF_GUARD: 120})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options[CONF_GUARD] == 120
    assert config_entry.options["pv_power"] is None
