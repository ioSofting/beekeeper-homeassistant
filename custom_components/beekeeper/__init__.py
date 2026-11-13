"""Beekeeper: the agent bee-ha (add-on or container) seen from inside Home Assistant.

The agent keeps the connection to the portal and carries remote access; this integration shows its state and the
running remote access sessions, tells the agent version, installation and name of Home Assistant, and lets the owner
end a remote access. It also runs the tools of an AI session of the portal, as far as the owner allows it in the
options (mcp.py, tools.py).
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import BeekeeperAgent
from .coordinator import BeekeeperCoordinator
from .mcp import McpWorker

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.BUTTON]

type BeekeeperConfigEntry = ConfigEntry[BeekeeperCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: BeekeeperConfigEntry) -> bool:
    agent = BeekeeperAgent(async_get_clientsession(hass), entry.data[CONF_URL])
    coordinator = BeekeeperCoordinator(hass, entry, agent)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    worker = McpWorker(hass, entry, agent)
    entry.async_create_background_task(hass, worker.run(), f"{entry.domain} AI access")
    entry.async_on_unload(worker.stop)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BeekeeperConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        entry.runtime_data.dismiss_all()
    return unloaded
