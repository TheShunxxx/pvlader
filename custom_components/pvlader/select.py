"""Lademodus-Auswahl."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import MODES
from .coordinator import PvLaderConfigEntry, PvLaderCoordinator
from .entity import PvLaderSettingEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: PvLaderConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([PvLaderModeSelect(entry.runtime_data)])


class PvLaderModeSelect(PvLaderSettingEntity, SelectEntity):
    _attr_options = list(MODES)

    def __init__(self, coordinator: PvLaderCoordinator) -> None:
        super().__init__(coordinator, "mode")

    @property
    def current_option(self) -> str:
        return self.coordinator.settings.mode

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_update_settings(mode=option)
