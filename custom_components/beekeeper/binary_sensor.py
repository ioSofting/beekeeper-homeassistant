"""Connection of the agent to the portal."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BeekeeperConfigEntry
from .entity import BeekeeperEntity


async def async_setup_entry(hass: HomeAssistant, entry: BeekeeperConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([BeekeeperConnected(entry.runtime_data)])


class BeekeeperConnected(BeekeeperEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "connected")

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.get("connected"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        return {
            "server": data.get("server"),
            "device_id": data.get("deviceId"),
            "remote_access_allowed": data.get("remoteAccess"),
            "problem": data.get("problem"),
        }
