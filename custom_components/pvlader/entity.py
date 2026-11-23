"""Gemeinsame Basisklasse der pvlader-Entitäten."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PvLaderCoordinator
from .ha_const import MANUFACTURER


class PvLaderEntity(CoordinatorEntity[PvLaderCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: PvLaderCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        info = coordinator.wallbox_info
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, info.serial)},
            name=entry.title,
            manufacturer=MANUFACTURER,
            model="Wallbox",
            model_id=str(info.model),  # Rohwert Register 105
            sw_version=info.firmware,
            serial_number=info.serial,
        )


class PvLaderSettingEntity(PvLaderEntity):
    """Einstellungs-Entitäten: Wert kommt aus dem Store, nicht aus der Wallbox."""

    @property
    def available(self) -> bool:
        return True
