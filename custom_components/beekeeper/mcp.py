"""AI access: fetches the requests of AI sessions from the agent (long poll) and runs the tools in Home Assistant.

The owner decides in the options of the integration what an AI may do (read, write, users; all off by default). The
agent learns it with every poll, so the portal shows it; this integration checks it again on every request. While
an AI session runs, a notification says who, why and until when.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .api import BeekeeperAgent, BeekeeperAgentError
from .const import CONF_AI_READ, CONF_AI_USERS, CONF_AI_WRITE, DOMAIN, EVENT_AI_ACCESS
from .tools import call_tool, tools_for

_LOGGER = logging.getLogger(__name__)

# seconds a poll waits at the agent for a request
POLL_WAIT = 20
RETRY_MIN = 5
RETRY_MAX = 60

# notification while an AI works on this Home Assistant: {by}, {purpose}, {until}
AI_NOTIFICATION = {
    "en": ("AI access through Beekeeper", "An AI of **{by}** works on this Home Assistant.\n\nPurpose: {purpose}\n\nUntil: {until}\n\nWhat it may do is set in the options of the integration Beekeeper; switch off *AI access* there to stop it at once."),
    "de": ("KI-Zugang über Beekeeper", "Eine KI von **{by}** arbeitet an diesem Home Assistant.\n\nZweck: {purpose}\n\nBis: {until}\n\nWas sie darf, steht in den Optionen der Integration Beekeeper; dort *KI-Zugang* ausschalten beendet es sofort."),
    "ru": ("Доступ ИИ через Beekeeper", "ИИ от **{by}** работает с этим Home Assistant.\n\nЦель: {purpose}\n\nДо: {until}\n\nЧто ему разрешено, задаётся в параметрах интеграции Beekeeper; выключите там *Доступ ИИ*, чтобы сразу остановить."),
    "uk": ("Доступ ШІ через Beekeeper", "ШІ від **{by}** працює з цим Home Assistant.\n\nМета: {purpose}\n\nДо: {until}\n\nЩо йому дозволено, задається в параметрах інтеграції Beekeeper; вимкніть там *Доступ ШІ*, щоб одразу зупинити."),
    "es": ("Acceso de IA a través de Beekeeper", "Una IA de **{by}** trabaja en este Home Assistant.\n\nMotivo: {purpose}\n\nHasta: {until}\n\nLo que puede hacer se define en las opciones de la integración Beekeeper; desactive allí *Acceso de IA* para detenerlo al instante."),
    "fr": ("Accès IA via Beekeeper", "Une IA de **{by}** travaille sur ce Home Assistant.\n\nObjet : {purpose}\n\nJusqu'à : {until}\n\nCe qu'elle peut faire se règle dans les options de l'intégration Beekeeper ; désactivez-y *Accès IA* pour l'arrêter aussitôt."),
    "it": ("Accesso IA tramite Beekeeper", "Un'IA di **{by}** lavora su questo Home Assistant.\n\nScopo: {purpose}\n\nFino a: {until}\n\nCiò che può fare si imposta nelle opzioni dell'integrazione Beekeeper; disattivare lì *Accesso IA* per fermarla subito."),
    "nl": ("AI-toegang via Beekeeper", "Een AI van **{by}** werkt aan deze Home Assistant.\n\nDoel: {purpose}\n\nTot: {until}\n\nWat ze mag, staat in de opties van de integratie Beekeeper; zet daar *AI-toegang* uit om het meteen te stoppen."),
    "pl": ("Dostęp AI przez Beekeeper", "AI od **{by}** pracuje na tym Home Assistant.\n\nCel: {purpose}\n\nDo: {until}\n\nTo, co może robić, ustawia się w opcjach integracji Beekeeper; wyłącz tam *Dostęp AI*, aby natychmiast zatrzymać."),
    "pt": ("Acesso de IA através do Beekeeper", "Uma IA de **{by}** está a trabalhar neste Home Assistant.\n\nMotivo: {purpose}\n\nAté: {until}\n\nO que pode fazer define-se nas opções da integração Beekeeper; desligue lá *Acesso de IA* para parar de imediato."),
    "zh-Hans": ("通过 Beekeeper 的 AI 访问", "**{by}** 的 AI 正在操作此 Home Assistant。\n\n目的：{purpose}\n\n截止：{until}\n\n其权限在 Beekeeper 集成的选项中设置；在那里关闭 *AI 访问* 可立即停止。"),
}


def ai_texts(language: str | None) -> dict[str, str]:
    lang = language or "en"
    if lang.startswith("zh"):
        lang = "zh-Hans"
    title, message = AI_NOTIFICATION.get(lang) or AI_NOTIFICATION.get(lang.split("-")[0]) or AI_NOTIFICATION["en"]
    return {"title": title, "message": message}


def ai_notification_id(session_id: str) -> str:
    return f"{DOMAIN}_ai_{session_id}"


def rights_of(entry: ConfigEntry) -> dict[str, bool]:
    """What the owner allows; writing and users only together with reading."""
    read = bool(entry.options.get(CONF_AI_READ, False))
    return {
        "read": read,
        "write": read and bool(entry.options.get(CONF_AI_WRITE, False)),
        "users": read and bool(entry.options.get(CONF_AI_USERS, False)),
    }


class McpWorker:
    """Long poll at the agent; each request runs in its own task."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, agent: BeekeeperAgent) -> None:
        self.hass = hass
        self.entry = entry
        self.agent = agent
        # session id -> request of 'open' (by, purpose, expiresAt)
        self.sessions: dict[str, dict[str, Any]] = {}
        self._tasks: set[asyncio.Task] = set()

    async def run(self) -> None:
        retry = RETRY_MIN
        while True:
            try:
                job = await self.agent.mcp_next(rights_of(self.entry), POLL_WAIT)
                retry = RETRY_MIN
            except BeekeeperAgentError as err:
                # an agent before 0.3.0 has no AI access (HTTP 404), or it restarts
                _LOGGER.debug("No requests from the agent: %s; again in %s s", err, retry)
                await asyncio.sleep(retry)
                retry = min(retry * 2, RETRY_MAX)
                continue
            self._expire()
            if job:
                task = self.hass.async_create_background_task(self._handle(job), f"{DOMAIN} AI request")
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

    def stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        for sid in list(self.sessions):
            self._closed(sid)

    async def _handle(self, job: dict[str, Any]) -> None:
        ref = job.get("ref")
        req = job.get("request")
        if not isinstance(ref, str) or not isinstance(req, dict):
            return
        action = req.get("action")
        if action == "close":
            self._closed(str(req.get("session", "")))
            return
        rights = rights_of(self.entry)
        try:
            if not rights["read"]:
                raise PermissionError("Der Besitzer hat den KI-Zugang in Home Assistant nicht erlaubt")
            if float(req.get("expiresAt", 0)) / 1000 < time.time():
                raise PermissionError("Der KI-Zugang ist abgelaufen")
            self._seen(req)
            if action in ("open", "tools"):
                reply: dict[str, Any] = {"tools": tools_for(rights)}
            elif action == "call":
                tool = str(req.get("tool", ""))
                args = req.get("args") if isinstance(req.get("args"), dict) else {}
                # never the arguments: create_user carries a password
                _LOGGER.info("AI of %s: %s", req.get("by"), tool)
                reply = {"result": await call_tool(self.hass, tool, args, rights)}
            else:
                raise ValueError(f"unknown action {action}")
            await self.agent.mcp_reply(ref, {"reply": reply})
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 - the portal gets the error
            try:
                await self.agent.mcp_reply(ref, {"error": str(err) or type(err).__name__})
            except BeekeeperAgentError:
                _LOGGER.debug("Could not answer request %s: %s", ref, err)

    def _seen(self, req: dict[str, Any]) -> None:
        """A request of a session: a notification at its start (also after a restart of Home Assistant)."""
        sid = str(req.get("session", ""))
        if not sid or sid in self.sessions:
            return
        self.sessions[sid] = req
        until = dt_util.as_local(dt_util.utc_from_timestamp(float(req.get("expiresAt", 0)) / 1000))
        texts = ai_texts(self.hass.config.language)
        message = texts["message"].format(
            by=req.get("by", "?"), purpose=req.get("purpose", ""), until=until.strftime("%d.%m.%Y %H:%M")
        )
        persistent_notification.async_create(self.hass, message, title=texts["title"], notification_id=ai_notification_id(sid))
        self.hass.bus.async_fire(
            EVENT_AI_ACCESS,
            {"type": "start", "id": sid, "by": req.get("by"), "purpose": req.get("purpose"), "until": until.isoformat()},
        )

    def _closed(self, sid: str) -> None:
        req = self.sessions.pop(sid, None)
        if req is None:
            return
        persistent_notification.async_dismiss(self.hass, ai_notification_id(sid))
        self.hass.bus.async_fire(EVENT_AI_ACCESS, {"type": "end", "id": sid, "by": req.get("by"), "purpose": req.get("purpose")})

    def _expire(self) -> None:
        now = time.time()
        for sid, req in list(self.sessions.items()):
            if float(req.get("expiresAt", 0)) / 1000 < now:
                self._closed(sid)
