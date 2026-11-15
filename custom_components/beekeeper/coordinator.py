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


# notification while somebody works on this Home Assistant: {by}, {purpose}, {until}
NOTIFICATION = {
    "en": ("Remote access through Beekeeper", "**{by}** works on this Home Assistant through remote access.\n\nPurpose: {purpose}\n\nUntil: {until}\n\nEnd it now: device *Beekeeper*, button *End remote access*."),
    "de": ("Fernzugriff über Beekeeper", "**{by}** arbeitet per Fernzugriff an diesem Home Assistant.\n\nZweck: {purpose}\n\nBis: {until}\n\nVorzeitig beenden: Gerät *Beekeeper*, Taste *Fernzugriff beenden*."),
    "ru": ("Удалённый доступ через Beekeeper", "**{by}** работает с этим Home Assistant через удалённый доступ.\n\nЦель: {purpose}\n\nДо: {until}\n\nЗавершить сейчас: устройство *Beekeeper*, кнопка *Завершить удалённый доступ*."),
    "uk": ("Віддалений доступ через Beekeeper", "**{by}** працює з цим Home Assistant через віддалений доступ.\n\nМета: {purpose}\n\nДо: {until}\n\nЗавершити зараз: пристрій *Beekeeper*, кнопка *Завершити віддалений доступ*."),
    "es": ("Acceso remoto a través de Beekeeper", "**{by}** trabaja en este Home Assistant mediante acceso remoto.\n\nMotivo: {purpose}\n\nHasta: {until}\n\nFinalizar ahora: dispositivo *Beekeeper*, botón *Finalizar acceso remoto*."),
    "fr": ("Accès à distance via Beekeeper", "**{by}** travaille sur ce Home Assistant par accès à distance.\n\nObjet : {purpose}\n\nJusqu'à : {until}\n\nTerminer maintenant : appareil *Beekeeper*, bouton *Terminer l'accès à distance*."),
    "it": ("Accesso remoto tramite Beekeeper", "**{by}** lavora su questo Home Assistant tramite accesso remoto.\n\nScopo: {purpose}\n\nFino a: {until}\n\nTerminare ora: dispositivo *Beekeeper*, pulsante *Termina l'accesso remoto*."),
    "nl": ("Externe toegang via Beekeeper", "**{by}** werkt via externe toegang aan deze Home Assistant.\n\nDoel: {purpose}\n\nTot: {until}\n\nNu beëindigen: apparaat *Beekeeper*, knop *Externe toegang beëindigen*."),
    "pl": ("Dostęp zdalny przez Beekeeper", "**{by}** pracuje na tym Home Assistant przez dostęp zdalny.\n\nCel: {purpose}\n\nDo: {until}\n\nZakończ teraz: urządzenie *Beekeeper*, przycisk *Zakończ dostęp zdalny*."),
    "pt": ("Acesso remoto através do Beekeeper", "**{by}** está a trabalhar neste Home Assistant por acesso remoto.\n\nMotivo: {purpose}\n\nAté: {until}\n\nTerminar agora: dispositivo *Beekeeper*, botão *Terminar acesso remoto*."),
    "zh-Hans": ("通过 Beekeeper 的远程访问", "**{by}** 正在通过远程访问操作此 Home Assistant。\n\n目的：{purpose}\n\n截止：{until}\n\n立即结束：设备 *Beekeeper*，按钮 *结束远程访问*。"),
}


# the console instead of remote access: title and first sentence, {by}
CONSOLE_NOTIFICATION = {
    "en": ("Console through Beekeeper", "**{by}** works in a console (shell) of the add-on Beekeeper: the configuration of Home Assistant and the CLI ha."),
    "de": ("Konsole über Beekeeper", "**{by}** arbeitet in einer Konsole (Shell) des Add-ons Beekeeper: Konfiguration von Home Assistant und CLI ha."),
    "ru": ("Консоль через Beekeeper", "**{by}** работает в консоли (shell) дополнения Beekeeper: конфигурация Home Assistant и CLI ha."),
    "uk": ("Консоль через Beekeeper", "**{by}** працює в консолі (shell) доповнення Beekeeper: конфігурація Home Assistant і CLI ha."),
    "es": ("Consola a través de Beekeeper", "**{by}** trabaja en una consola (shell) del complemento Beekeeper: la configuración de Home Assistant y la CLI ha."),
    "fr": ("Console via Beekeeper", "**{by}** travaille dans une console (shell) du module complémentaire Beekeeper : la configuration de Home Assistant et la CLI ha."),
    "it": ("Console tramite Beekeeper", "**{by}** lavora in una console (shell) dell'add-on Beekeeper: la configurazione di Home Assistant e la CLI ha."),
    "nl": ("Console via Beekeeper", "**{by}** werkt in een console (shell) van de add-on Beekeeper: de configuratie van Home Assistant en de CLI ha."),
    "pl": ("Konsola przez Beekeeper", "**{by}** pracuje w konsoli (shell) dodatku Beekeeper: konfiguracja Home Assistant i CLI ha."),
    "pt": ("Consola através do Beekeeper", "**{by}** está a trabalhar numa consola (shell) do add-on Beekeeper: a configuração do Home Assistant e a CLI ha."),
    "zh-Hans": ("通过 Beekeeper 的控制台", "**{by}** 正在 Beekeeper 加载项的控制台（shell）中工作：Home Assistant 的配置和 CLI ha。"),
}


def _lang(language: str | None, table: dict[str, tuple[str, str]]) -> tuple[str, str]:
    lang = language or "en"
    if lang.startswith("zh"):
        lang = "zh-Hans"
    return table.get(lang) or table.get(lang.split("-")[0]) or table["en"]


def notification_texts(language: str | None, kind: str = "ha") -> dict[str, str]:
    """Title and message in the language of Home Assistant (de, de-CH, pt-BR, zh-Hans ...), else English."""
    title, message = _lang(language, NOTIFICATION)
    if kind == "console":
        # the same purpose, end and button; another title and first sentence
        title, first = _lang(language, CONSOLE_NOTIFICATION)
        message = first + message[message.index("\n\n") :]
    return {"title": title, "message": message}


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
        for sid, s in current.items():
            if sid in self._known:
                continue
            kind = s.get("kind") or "ha"
            texts = notification_texts(self.hass.config.language, kind)
            until = _local_time(s.get("expiresAt"))
            until_text = until.strftime("%d.%m.%Y %H:%M") if until else "?"
            title = texts["title"]
            message = texts["message"].format(by=s.get("by", "?"), purpose=s.get("purpose", ""), until=until_text)
            persistent_notification.async_create(self.hass, message, title=title, notification_id=notification_id(sid))
            self.hass.bus.async_fire(
                EVENT_REMOTE_ACCESS,
                {
                    "type": "start",
                    "id": sid,
                    "kind": kind,
                    "by": s.get("by"),
                    "purpose": s.get("purpose"),
                    "until": until.isoformat() if until else None,
                },
            )
        for sid, s in self._known.items():
            if sid not in current:
                persistent_notification.async_dismiss(self.hass, notification_id(sid))
                self.hass.bus.async_fire(
                    EVENT_REMOTE_ACCESS,
                    {"type": "end", "id": sid, "kind": s.get("kind") or "ha", "by": s.get("by"), "purpose": s.get("purpose")},
                )
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
                "kind": s.get("kind") or "ha",
                "by": s.get("by"),
                "purpose": s.get("purpose"),
                "started": started.isoformat() if started else None,
                "until": until.isoformat() if until else None,
            }
        )
    return out
