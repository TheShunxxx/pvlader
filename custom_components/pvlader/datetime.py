"""Zielzeit des Ladeplans."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.datetime import DateTimeEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import PvLaderConfigEntry, PvLaderCoordinator
from .entity import PvLaderSettingEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: PvLaderConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([PvLaderPlanTime(entry.runtime_data)])


class PvLaderPlanTime(PvLaderSettingEntity, DateTimeEntity):
    def __init__(self, coordinator: PvLaderCoordinator) -> None:
        super().__init__(coordinator, "plan_time")

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.settings.plan_time

    async def async_set_value(self, value: datetime) -> None:
        # HA liefert zeitzonenbewusst (UTC); intern in Ortszeit ablegen
        await self.coordinator.async_update_settings(plan_time=dt_util.as_local(value))
