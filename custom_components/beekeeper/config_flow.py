"""Set up: the add-on announces itself (discovery), a container is entered with its address."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_URL
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import BeekeeperAgent, BeekeeperAgentError
from .const import DEFAULT_URL, DOMAIN

try:  # since 2025.1
    from homeassistant.helpers.service_info.hassio import HassioServiceInfo
except ImportError:  # pragma: no cover
    from homeassistant.components.hassio import HassioServiceInfo


class BeekeeperConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._url: str | None = None

    async def _check(self, url: str) -> dict[str, Any]:
        return await BeekeeperAgent(async_get_clientsession(self.hass), url).status()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].strip().rstrip("/")
            try:
                status = await self._check(url)
            except BeekeeperAgentError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(status["uuid"])
                self._abort_if_unique_id_configured(updates={CONF_URL: url})
                return self.async_create_entry(title="Beekeeper", data={CONF_URL: url})
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_URL, default=(user_input or {}).get(CONF_URL, DEFAULT_URL)): str}),
            errors=errors,
        )

    async def async_step_hassio(self, discovery_info: HassioServiceInfo) -> ConfigFlowResult:
        """The add-on Beekeeper announced its local API through the Supervisor."""
        host = discovery_info.config.get("host")
        port = discovery_info.config.get("port")
        if not host or not port:
            return self.async_abort(reason="cannot_connect")
        url = f"http://{host}:{port}"
        try:
            status = await self._check(url)
        except BeekeeperAgentError:
            return self.async_abort(reason="cannot_connect")
        await self.async_set_unique_id(status["uuid"])
        # the add-on may get another address after a reinstall: keep the entry, take the new address
        self._abort_if_unique_id_configured(updates={CONF_URL: url})
        self._url = url
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="Beekeeper", data={CONF_URL: self._url})
        self._set_confirm_only()
        return self.async_show_form(step_id="hassio_confirm")
