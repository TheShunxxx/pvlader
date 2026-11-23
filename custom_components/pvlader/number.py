"""Zahlen-Einstellungen: SoC-Limit, Plan-Ziel, Min-/Max-Strom."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import PvLaderConfigEntry, PvLaderCoordinator
from .entity import PvLaderSettingEntity


@dataclass(frozen=True, kw_only=True)
class PvLaderNumberDescription(NumberEntityDescription):
    current_bounds: bool = False  # Grenzen aus den Optionen (Installationsgrenzen)


NUMBERS: tuple[PvLaderNumberDescription, ...] = (
    PvLaderNumberDescription(
        key="limit_soc",
        translation_key="limit_soc",
        native_min_value=0,
        native_max_value=100,
        native_step=5,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
    ),
    PvLaderNumberDescription(
        key="plan_target_soc",
        translation_key="plan_target_soc",
        native_min_value=0,
        native_max_value=100,
        native_step=5,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
    ),
    PvLaderNumberDescription(
        key="min_current",
        translation_key="min_current",
        native_step=1,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        current_bounds=True,
    ),
    PvLaderNumberDescription(
        key="max_current",
        translation_key="max_current",
        native_step=1,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        current_bounds=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: PvLaderConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(PvLaderNumber(entry.runtime_data, desc) for desc in NUMBERS)


class PvLaderNumber(PvLaderSettingEntity, NumberEntity):
    entity_description: PvLaderNumberDescription

    def __init__(self, coordinator: PvLaderCoordinator, description: PvLaderNumberDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        if description.current_bounds:
            lo, hi = coordinator.current_bounds()
            self._attr_native_min_value = lo
            self._attr_native_max_value = hi

    @property
    def native_value(self) -> float:
        key = self.entity_description.key
        if key == "min_current":
            return self.coordinator.effective_currents()[0]
        if key == "max_current":
            return self.coordinator.effective_currents()[1]
        return float(getattr(self.coordinator.settings, key))

    async def async_set_native_value(self, value: float) -> None:
        key = self.entity_description.key
        s = self.coordinator.settings
        changes: dict[str, float] = {key: value}
        # Min/Max konsistent halten
        if key == "min_current" and value > s.max_current:
            changes["max_current"] = value
        elif key == "max_current" and value < s.min_current:
            changes["min_current"] = value
        await self.coordinator.async_update_settings(**changes)
