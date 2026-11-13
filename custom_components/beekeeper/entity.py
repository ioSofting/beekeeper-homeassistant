"""Base of the entities: one device "Beekeeper" per agent."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BeekeeperCoordinator


class BeekeeperEntity(CoordinatorEntity[BeekeeperCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: BeekeeperCoordinator, key: str) -> None:
        super().__init__(coordinator)
        uuid = coordinator.config_entry.unique_id or coordinator.config_entry.entry_id
        data = coordinator.data or {}
        self._attr_unique_id = f"{uuid}_{key}"
        self._attr_translation_key = key
        server = (data.get("server") or "").rstrip("/")
        asset = data.get("assetId")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, uuid)},
            name="Beekeeper",
            manufacturer="ioSofting",
            model="bee-ha",
            sw_version=data.get("agentVersion"),
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=f"{server}/#/assets/{asset}" if server.startswith("http") and asset else None,
        )
