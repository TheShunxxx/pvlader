"""Binärsensoren: Fahrzeug verbunden, lädt, Planladen aktiv."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import POWER_ON_THRESHOLD
from .coordinator import PvLaderConfigEntry, PvLaderCoordinator
from .entity import PvLaderEntity


@dataclass(frozen=True, kw_only=True)
class PvLaderBinaryDescription(BinarySensorEntityDescription):
    is_on_fn: Callable[[PvLaderCoordinator], bool]


BINARY_SENSORS: tuple[PvLaderBinaryDescription, ...] = (
    PvLaderBinaryDescription(
        key="connected",
        translation_key="connected",
        device_class=BinarySensorDeviceClass.PLUG,
        is_on_fn=lambda c: c.data.connected,
    ),
    PvLaderBinaryDescription(
        key="charging",
        translation_key="charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        is_on_fn=lambda c: c.data.wallbox.charging or c.data.wallbox.power_w > POWER_ON_THRESHOLD,
    ),
    PvLaderBinaryDescription(
        key="plan_charging",
        translation_key="plan_charging",
        device_class=BinarySensorDeviceClass.RUNNING,
        is_on_fn=lambda c: c.data.info.plan_charging,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: PvLaderConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(PvLaderBinarySensor(entry.runtime_data, desc) for desc in BINARY_SENSORS)


class PvLaderBinarySensor(PvLaderEntity, BinarySensorEntity):
    entity_description: PvLaderBinaryDescription

    def __init__(self, coordinator: PvLaderCoordinator, description: PvLaderBinaryDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        return self.entity_description.is_on_fn(self.coordinator)
