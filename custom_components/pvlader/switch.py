"""Schalter: Steuerung aktiv (Sicherheitsschalter), Ladeplan aktiv."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import PvLaderConfigEntry, PvLaderCoordinator
from .entity import PvLaderSettingEntity

KEYS = ("control_enabled", "plan_active")


async def async_setup_entry(
    hass: HomeAssistant, entry: PvLaderConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(PvLaderSwitch(entry.runtime_data, key) for key in KEYS)


class PvLaderSwitch(PvLaderSettingEntity, SwitchEntity):
    def __init__(self, coordinator: PvLaderCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._key = key

    @property
    def is_on(self) -> bool:
        return bool(getattr(self.coordinator.settings, self._key))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_update_settings(**{self._key: True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_update_settings(**{self._key: False})
