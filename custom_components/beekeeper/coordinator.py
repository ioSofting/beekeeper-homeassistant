"""Polls the agent, tells it about Home Assistant, and shows remote access sessions as notifications."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.helpers.system_info import async_get_system_info
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import BeekeeperAgent, BeekeeperAgentError
from .const import DOMAIN, EVENT_REMOTE_ACCESS, INFO_INTERVAL, INSTALLATION, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


def _local_time(ms: int | float | None) -> datetime | None:
    if not ms:
        return None
    return dt_util.as_local(dt_util.utc_from_timestamp(ms / 1000))


def notification_id(session_id: str) -> str:
    return f"{DOMAIN}_{session_id}"


class BeekeeperCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """State of the agent; data is the answer of GET /api/status."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, agent: BeekeeperAgent) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=SCAN_INTERVAL)
        self.agent = agent
        self._installation: str | None = None
        self._info_sent: datetime | None = None
        self._known: dict[str, dict[str, Any]] = {}

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            status = await self.agent.status()
        except BeekeeperAgentError as err:
            raise UpdateFailed(f"Agent bee-ha at {self.agent.url} does not answer: {err}") from err
        await self._push_info()
        self._sessions_changed(status.get("sessions") or [])
        return status

    async def _push_info(self) -> None:
        """Version, installation and name of Home Assistant to the agent, at the start and once an hour."""
        now = dt_util.utcnow()
        if self._info_sent and now - self._info_sent < INFO_INTERVAL:
            return
        if self._installation is None:
            try:
                info = await async_get_system_info(self.hass)
                self._installation = INSTALLATION.get(info.get("installation_type", ""), "")
            except Exception:  # noqa: BLE001 - only for display, never a reason to fail
                self._installation = ""
        body = {"version": HA_VERSION}
        if self._installation:
            body["installation"] = self._installation
        if self.hass.config.location_name:
            body["locationName"] = self.hass.config.location_name
        try:
            await self.agent.push_info(body)
            self._info_sent = now
        except BeekeeperAgentError as err:
            _LOGGER.debug("Could not tell the agent about Home Assistant: %s", err)

    def _sessions_changed(self, sessions: list[dict[str, Any]]) -> None:
        """A notification while somebody works on this Home Assistant, and an event at start and end."""
        current = {s["id"]: s for s in sessions if isinstance(s, dict) and s.get("id")}
        german = (self.hass.config.language or "").startswith("de")
        for sid, s in current.items():
            if sid in self._known:
                continue
            until = _local_time(s.get("expiresAt"))
            until_text = until.strftime("%d.%m.%Y %H:%M") if until else "?"
            if german:
                title = "Fernzugriff über Beekeeper"
                message = (
                    f"**{s.get('by', '?')}** arbeitet per Fernzugriff an diesem Home Assistant.\n\n"
                    f"Zweck: {s.get('purpose', '')}\n\nBis: {until_text}\n\n"
                    "Vorzeitig beenden: Gerät *Beekeeper*, Taste *Fernzugriff beenden*."
                )
            else:
                title = "Remote access through Beekeeper"
                message = (
                    f"**{s.get('by', '?')}** works on this Home Assistant through remote access.\n\n"
                    f"Purpose: {s.get('purpose', '')}\n\nUntil: {until_text}\n\n"
                    "End it now: device *Beekeeper*, button *End remote access*."
                )
            persistent_notification.async_create(self.hass, message, title=title, notification_id=notification_id(sid))
            self.hass.bus.async_fire(
                EVENT_REMOTE_ACCESS,
                {"type": "start", "id": sid, "by": s.get("by"), "purpose": s.get("purpose"), "until": until.isoformat() if until else None},
            )
        for sid, s in self._known.items():
            if sid not in current:
                persistent_notification.async_dismiss(self.hass, notification_id(sid))
                self.hass.bus.async_fire(EVENT_REMOTE_ACCESS, {"type": "end", "id": sid, "by": s.get("by"), "purpose": s.get("purpose")})
        self._known = current

    def dismiss_all(self) -> None:
        """Integration unloaded: no notification about sessions it no longer watches."""
        for sid in self._known:
            persistent_notification.async_dismiss(self.hass, notification_id(sid))
        self._known = {}


def session_attributes(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The sessions for state attributes, with local times."""
    out = []
    for s in sessions:
        started = _local_time(s.get("startedAt"))
        until = _local_time(s.get("expiresAt"))
        out.append(
            {
                "by": s.get("by"),
                "purpose": s.get("purpose"),
                "started": started.isoformat() if started else None,
                "until": until.isoformat() if until else None,
            }
        )
    return out
