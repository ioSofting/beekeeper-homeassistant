"""Running remote access sessions, and the state of the agent."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BeekeeperConfigEntry
from .coordinator import session_attributes
from .entity import BeekeeperEntity

STATES = ["connected", "disconnected", "not_enrolled", "problem"]


async def async_setup_entry(hass: HomeAssistant, entry: BeekeeperConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([BeekeeperSessions(entry.runtime_data), BeekeeperState(entry.runtime_data)])


class BeekeeperSessions(BeekeeperEntity, SensorEntity):
    """Number of running remote access sessions; who and why in the attributes."""

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "sessions")

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data.get("sessions") or [])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"sessions": session_attributes(self.coordinator.data.get("sessions") or [])}


class BeekeeperState(BeekeeperEntity, SensorEntity):
    """connected, disconnected, not_enrolled or problem (the reason as attribute)."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = STATES

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "state")

    @property
    def native_value(self) -> str:
        data = self.coordinator.data
        if data.get("problem"):
            return "problem"
        if not data.get("enrolled"):
            return "not_enrolled"
        return "connected" if data.get("connected") else "disconnected"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"problem": self.coordinator.data.get("problem")}
