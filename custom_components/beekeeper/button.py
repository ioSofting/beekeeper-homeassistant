"""The owner ends the running remote access sessions."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BeekeeperConfigEntry
from .api import BeekeeperAgentError
from .const import DOMAIN
from .entity import BeekeeperEntity


async def async_setup_entry(hass: HomeAssistant, entry: BeekeeperConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([BeekeeperEndSessions(entry.runtime_data)])


class BeekeeperEndSessions(BeekeeperEntity, ButtonEntity):
    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "end_sessions")

    async def async_press(self) -> None:
        try:
            ended = await self.coordinator.agent.end_sessions()
        except BeekeeperAgentError as err:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="agent_unreachable") from err
        await self.coordinator.async_request_refresh()
        if not ended:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_sessions")
