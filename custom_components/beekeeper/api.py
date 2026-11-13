"""Client of the local API of the agent bee-ha (add-on or container next to Home Assistant)."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

TIMEOUT = aiohttp.ClientTimeout(total=10)
# an answer of a tool may be large
REPLY_TIMEOUT = aiohttp.ClientTimeout(total=60)


class BeekeeperAgentError(Exception):
    """The agent does not answer, or not as expected."""


class BeekeeperAgent:
    """GET /api/status, POST /api/ha-info, POST /api/sessions/end, POST /api/mcp/next and /api/mcp/reply."""

    def __init__(self, session: aiohttp.ClientSession, url: str) -> None:
        self._session = session
        self.url = url.rstrip("/")

    async def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        timeout: aiohttp.ClientTimeout = TIMEOUT,
    ) -> Any:
        try:
            async with self._session.request(
                method, f"{self.url}/api{path}", json=body, timeout=timeout
            ) as res:
                if res.status >= 400:
                    raise BeekeeperAgentError(f"HTTP {res.status}")
                if res.status == 204:
                    return None
                return await res.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            raise BeekeeperAgentError(str(err) or type(err).__name__) from err

    async def status(self) -> dict[str, Any]:
        """State of the agent and the running remote access sessions."""
        data = await self._request("GET", "/status")
        if not isinstance(data, dict) or "uuid" not in data or not isinstance(data.get("sessions"), list):
            raise BeekeeperAgentError("not a beekeeper agent")
        return data

    async def push_info(self, info: dict[str, str]) -> None:
        """Version, installation and name of Home Assistant, as only Home Assistant itself knows them."""
        await self._request("POST", "/ha-info", info)

    async def end_sessions(self, session_id: str | None = None) -> int:
        """Ends one or all remote access sessions; the server ends them too. Returns how many."""
        data = await self._request("POST", "/sessions/end", {"id": session_id} if session_id else {})
        return int(data.get("ended", 0)) if isinstance(data, dict) else 0

    async def mcp_next(self, rights: dict[str, bool], wait: int) -> dict[str, Any] | None:
        """The next request of an AI session, or None after wait seconds; tells the agent what the owner allows."""
        data = await self._request(
            "POST", "/mcp/next", {"wait": wait, "ai": rights}, aiohttp.ClientTimeout(total=wait + 15)
        )
        return data if isinstance(data, dict) else None

    async def mcp_reply(self, ref: str, body: dict[str, Any]) -> None:
        """The answer to a request: {"reply": {...}} or {"error": "..."}."""
        try:
            await self._request("POST", "/mcp/reply", {"ref": ref, **body}, REPLY_TIMEOUT)
        except BeekeeperAgentError as err:
            # 404: the agent gave up waiting
            if str(err) != "HTTP 404":
                raise
