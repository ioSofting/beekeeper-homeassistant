"""Tools of the AI access of Beekeeper, run inside Home Assistant.

An AI of the integrator reaches them only during an AI session that a person opened in the portal, for a purpose and
a time; every call is in the audit log of the portal. What they may do decides the owner in the options of this
integration: read (overview, entities, states, history, log, integrations, automations), write (call services,
reload integrations, restart) and users (list, create, enable and disable users). All of it is off by default.
create_user is called only by the portal: it makes the password and keeps it in its access data.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from typing import Any

import aiohttp
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import Context, HomeAssistant, State
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

# longest text of a result; the agent and the portal take no more (MAX_MCP_RESULT of the protocol)
MAX_TEXT = 512 * 1024
SERVICE_TIMEOUT = 60
LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
# start of an entry in home-assistant.log: "2026-11-13 19:00:00.123 ERROR (MainThread) [homeassistant.core] ..."
LOG_ENTRY = re.compile(r"^\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d")
ANSI = re.compile(r"\x1b\[[0-9;]*m")
USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,49}$")
CONFIG_ENTRY_PROBLEMS = {"setup_error", "migration_error", "setup_retry", "failed_unload"}
# the icon of Beekeeper, as picture of a user (e.g. the login of the support)
ICON = os.path.join(os.path.dirname(__file__), "brand", "icon@2x.png")


class ToolError(Exception):
    """The tool cannot do it; the text goes to the AI."""


@dataclass(frozen=True)
class Tool:
    name: str
    title: str
    description: str
    properties: dict[str, Any]
    required: tuple[str, ...]
    # read, write or users: the switch of the owner it needs
    right: str
    run: Callable[[HomeAssistant, dict[str, Any]], Awaitable[Any]]
    destructive: bool = False
    # only reads, also when it needs another switch than read (list_users)
    reads: bool = False

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": {"type": "object", "properties": self.properties, "required": list(self.required)},
            "annotations": {"readOnlyHint": self.right == "read" or self.reads, "destructiveHint": self.destructive},
        }


# ---------------------------------------------------------------------------------------------- arguments


def _str(args: dict[str, Any], key: str, required: bool = False, max_len: int = 200) -> str:
    value = args.get(key)
    if value is None or value == "":
        if required:
            raise ToolError(f'Argument "{key}" is missing')
        return ""
    if not isinstance(value, str):
        raise ToolError(f'Argument "{key}" must be a string')
    return value.strip()[:max_len]


def _int(args: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    value = args.get(key)
    if value is None or value == "":
        return default
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError) as err:
        raise ToolError(f'Argument "{key}" must be a number') from err


def _bool(args: dict[str, Any], key: str, default: bool) -> bool:
    value = args.get(key)
    return value if isinstance(value, bool) else default


def _dict(args: dict[str, Any], key: str) -> dict[str, Any] | None:
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ToolError(f'Argument "{key}" must be an object')
    return value


def _iso(value: Any) -> str | None:
    return dt_util.as_local(value).isoformat() if value else None


# ---------------------------------------------------------------------------------------------- read


async def ha_overview(hass: HomeAssistant, args: dict[str, Any]) -> Any:
    states = hass.states.async_all()
    entries = [e for e in hass.config_entries.async_entries() if not e.disabled_by]
    problems = [
        {"domain": e.domain, "title": e.title, "state": e.state.value, "reason": getattr(e, "reason", None)}
        for e in entries
        if e.state.value in CONFIG_ENTRY_PROBLEMS
    ]
    repairs: list[dict[str, Any]] = []
    try:
        from homeassistant.helpers import issue_registry as ir

        for issue in ir.async_get(hass).issues.values():
            if getattr(issue, "active", True) and not getattr(issue, "dismissed_version", None):
                severity = getattr(issue, "severity", None)
                repairs.append(
                    {
                        "domain": issue.domain,
                        "issue": issue.issue_id,
                        "severity": getattr(severity, "value", severity),
                        "translation_key": getattr(issue, "translation_key", None),
                    }
                )
    except Exception:  # noqa: BLE001 - the overview works without repairs
        pass
    updates = [
        {
            "entity_id": s.entity_id,
            "name": s.name,
            "installed": s.attributes.get("installed_version"),
            "latest": s.attributes.get("latest_version"),
        }
        for s in states
        if s.domain == "update" and s.state == "on"
    ]
    cfg = hass.config
    return {
        "version": HA_VERSION,
        "location_name": cfg.location_name,
        "time_zone": cfg.time_zone,
        "country": getattr(cfg, "country", None),
        "language": getattr(cfg, "language", None),
        "safe_mode": getattr(cfg, "safe_mode", False),
        "recovery_mode": getattr(cfg, "recovery_mode", False),
        "entities": len(states),
        "entities_per_domain": dict(sorted(Counter(s.domain for s in states).items())),
        "unavailable": sorted(s.entity_id for s in states if s.state == "unavailable")[:200],
        "integrations": len(entries),
        "integration_problems": problems,
        "repairs": repairs,
        "updates": updates,
    }


def _entity_row(
    st: State,
    ent_reg: er.EntityRegistry,
    dev_reg: dr.DeviceRegistry,
    area_reg: ar.AreaRegistry,
) -> dict[str, Any]:
    entry = ent_reg.async_get(st.entity_id)
    device = dev_reg.async_get(entry.device_id) if entry and entry.device_id else None
    area_id = (entry.area_id if entry else None) or (device.area_id if device else None)
    area = area_reg.async_get_area(area_id) if area_id else None
    row: dict[str, Any] = {"entity_id": st.entity_id, "name": st.name, "state": st.state}
    if area:
        row["area"] = area.name
    if device:
        row["device"] = device.name_by_user or device.name
    if entry:
        row["platform"] = entry.platform
    return row


async def list_entities(hass: HomeAssistant, args: dict[str, Any]) -> Any:
    domain = _str(args, "domain").lower()
    query = _str(args, "query").lower()
    area_wanted = _str(args, "area").lower()
    limit = _int(args, "limit", 200, 1, 2000)
    ent_reg, dev_reg, area_reg = er.async_get(hass), dr.async_get(hass), ar.async_get(hass)
    rows = []
    matched = 0
    for st in sorted(hass.states.async_all(domain) if domain else hass.states.async_all(), key=lambda s: s.entity_id):
        if query and query not in st.entity_id and query not in (st.name or "").lower():
            continue
        row = _entity_row(st, ent_reg, dev_reg, area_reg)
        if area_wanted and area_wanted not in row.get("area", "").lower():
            continue
        matched += 1
        if len(rows) < limit:
            rows.append(row)
    return {"matched": matched, "entities": rows}


async def get_states(hass: HomeAssistant, args: dict[str, Any]) -> Any:
    ids = args.get("entity_ids") or args.get("entity_id")
    if isinstance(ids, str):
        ids = [ids]
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
        raise ToolError('Argument "entity_ids" is missing: a list of entity ids')
    out = []
    for eid in ids[:100]:
        st = hass.states.get(eid.strip().lower())
        out.append(
            {"entity_id": eid, "missing": True}
            if st is None
            else {
                "entity_id": st.entity_id,
                "state": st.state,
                "attributes": dict(st.attributes),
                "last_changed": _iso(st.last_changed),
                "last_updated": _iso(st.last_updated),
            }
        )
    return out


async def get_history(hass: HomeAssistant, args: dict[str, Any]) -> Any:
    entity_id = _str(args, "entity_id", required=True).lower()
    hours = _int(args, "hours", 24, 1, 24 * 30)
    limit = _int(args, "limit", 500, 1, 5000)
    if "recorder" not in hass.config.components:
        raise ToolError("The recorder of Home Assistant is not running: no history")
    from homeassistant.components.recorder import get_instance, history

    start = dt_util.utcnow() - timedelta(hours=hours)
    job = partial(
        history.state_changes_during_period,
        hass,
        start,
        None,
        entity_id,
        no_attributes=True,
        descending=True,
        limit=limit,
        include_start_time_state=False,
    )
    found = await get_instance(hass).async_add_executor_job(job)
    changes = list(reversed(found.get(entity_id, [])))
    return {
        "entity_id": entity_id,
        "since": _iso(start),
        "changes": [{"state": s.state, "at": _iso(s.last_changed)} for s in changes],
        **({"note": f"only the last {limit} changes"} if len(changes) >= limit else {}),
    }


def _log_entries(lines: list[str]) -> list[str]:
    """Lines of the log as entries: a traceback belongs to the line before it."""
    entries: list[str] = []
    for line in lines:
        line = ANSI.sub("", line.rstrip("\n"))
        if LOG_ENTRY.match(line) or not entries:
            entries.append(line)
        else:
            entries[-1] += "\n" + line
    return entries


def _level_of(entry: str) -> int:
    head = entry.split("\n", 1)[0]
    for i, level in enumerate(LOG_LEVELS):
        if f" {level} " in head:
            return i
    return 1


def _tail(path: str, max_bytes: int) -> list[str]:
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - max_bytes))
        data = f.read().decode("utf-8", errors="replace")
    lines = data.splitlines()
    return lines[1:] if size > max_bytes and lines else lines


async def _supervisor_log(hass: HomeAssistant, count: int) -> list[str] | None:
    token = os.environ.get("SUPERVISOR_TOKEN")
    host = os.environ.get("SUPERVISOR")
    if not token or not host:
        return None
    session = async_get_clientsession(hass)
    try:
        async with session.get(
            f"http://{host}/core/logs",
            headers={"Authorization": f"Bearer {token}", "Accept": "text/plain", "Range": f"entries=:-{count}:"},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as res:
            if res.status not in (200, 206):
                return None
            text = await res.text(errors="replace")
    except Exception:  # noqa: BLE001 - then the errors of system_log
        return None
    return text.splitlines()[-count * 20 :]


def _system_log(hass: HomeAssistant) -> list[str] | None:
    handler = hass.data.get("system_log")
    records = getattr(handler, "records", None)
    if records is None:
        return None
    lines = []
    for r in records.to_list():
        ts = dt_util.as_local(dt_util.utc_from_timestamp(r.get("timestamp", 0))).strftime("%Y-%m-%d %H:%M:%S")
        message = "\n".join(r.get("message") or [])
        count = f" ({r.get('count')}x)" if r.get("count", 1) > 1 else ""
        lines.append(f"{ts} {r.get('level', 'ERROR')} [{r.get('name', '')}] {message}{count}")
        if r.get("exception"):
            lines.extend(r["exception"].splitlines())
    return sorted(lines)


async def read_log(hass: HomeAssistant, args: dict[str, Any]) -> Any:
    count = _int(args, "lines", 200, 1, 2000)
    query = _str(args, "query").lower()
    level = _str(args, "level").upper()
    if level and level not in LOG_LEVELS:
        if level == "WARN":
            level = "WARNING"
        else:
            raise ToolError(f'Argument "level": one of {", ".join(LOG_LEVELS)}')
    filtered = bool(query or level)
    path = hass.config.path("home-assistant.log")
    source = "home-assistant.log"
    lines: list[str] | None = None
    if os.path.isfile(path):
        lines = await hass.async_add_executor_job(_tail, path, (8 if filtered else 2) * 1024 * 1024)
    if lines is None:
        source = "journal of Home Assistant (Supervisor)"
        lines = await _supervisor_log(hass, min(10000, count * (20 if filtered else 1)))
    if lines is None:
        source = "errors and warnings kept by Home Assistant (system_log)"
        lines = _system_log(hass)
    if lines is None:
        raise ToolError("No log found: no home-assistant.log, no Supervisor, no system_log")
    entries = _log_entries(lines)
    if level:
        wanted = LOG_LEVELS.index(level)
        entries = [e for e in entries if _level_of(e) >= wanted]
    if query:
        entries = [e for e in entries if query in e.lower()]
    picked = entries[-count:]
    head = f"{source}: {len(entries)} matching entries, the last {len(picked)}:"
    return head + "\n" + "\n".join(picked)


async def list_integrations(hass: HomeAssistant, args: dict[str, Any]) -> Any:
    domain = _str(args, "domain").lower()
    return [
        {
            "entry_id": e.entry_id,
            "domain": e.domain,
            "title": e.title,
            "state": e.state.value,
            **({"reason": e.reason} if getattr(e, "reason", None) else {}),
            **({"disabled_by": str(e.disabled_by)} if e.disabled_by else {}),
            "source": e.source,
        }
        for e in hass.config_entries.async_entries()
        if not domain or e.domain == domain
    ]


async def list_automations(hass: HomeAssistant, args: dict[str, Any]) -> Any:
    return [
        {
            "entity_id": s.entity_id,
            "name": s.name,
            "state": s.state,
            "id": s.attributes.get("id"),
            "mode": s.attributes.get("mode"),
            "last_triggered": _iso(s.attributes.get("last_triggered")) if s.attributes.get("last_triggered") else None,
        }
        for s in sorted(hass.states.async_all("automation"), key=lambda s: s.entity_id)
    ]


async def get_automation_config(hass: HomeAssistant, args: dict[str, Any]) -> Any:
    entity_id = _str(args, "entity_id", required=True).lower()
    component = hass.data.get("automation")
    entity = component.get_entity(entity_id) if component is not None and hasattr(component, "get_entity") else None
    if entity is None:
        raise ToolError(f"No automation {entity_id}")
    config = getattr(entity, "raw_config", None)
    if config is None:
        raise ToolError(f"{entity_id} has no configuration of its own (e.g. a blueprint without inputs)")
    return config


# ---------------------------------------------------------------------------------------------- write


async def call_service(hass: HomeAssistant, args: dict[str, Any]) -> Any:
    domain = _str(args, "domain", required=True).lower()
    service = _str(args, "service", required=True).lower()
    data = _dict(args, "data") or {}
    target = _dict(args, "target")
    if not hass.services.has_service(domain, service):
        raise ToolError(f"No service {domain}.{service}")
    if domain == "homeassistant" and service in ("restart", "stop"):
        raise ToolError("For a restart use restart_home_assistant; stopping is not allowed")
    from homeassistant.core import SupportsResponse

    wants_response = hass.services.supports_response(domain, service) != SupportsResponse.NONE
    try:
        async with asyncio.timeout(SERVICE_TIMEOUT):
            response = await hass.services.async_call(
                domain,
                service,
                data,
                blocking=True,
                target=target,
                context=Context(),
                return_response=wants_response,
            )
    except TimeoutError as err:
        raise ToolError(f"{domain}.{service} did not finish within {SERVICE_TIMEOUT} s; it may still run") from err
    return {"ok": True, **({"response": response} if wants_response else {})}


async def reload_integration(hass: HomeAssistant, args: dict[str, Any]) -> Any:
    entry_id = _str(args, "entry_id")
    domain = _str(args, "domain").lower()
    if entry_id:
        entries = [e for e in hass.config_entries.async_entries() if e.entry_id == entry_id]
    elif domain:
        entries = hass.config_entries.async_entries(domain)
    else:
        raise ToolError('Argument "entry_id" or "domain" is missing (see list_integrations)')
    if not entries:
        raise ToolError("No such integration entry")
    out = []
    for e in entries:
        ok = await hass.config_entries.async_reload(e.entry_id)
        out.append({"entry_id": e.entry_id, "title": e.title, "reloaded": ok, "state": e.state.value})
    return out


async def restart_home_assistant(hass: HomeAssistant, args: dict[str, Any]) -> Any:
    try:
        from homeassistant.config import async_check_ha_config_file

        errors = await async_check_ha_config_file(hass)
    except ImportError:
        errors = None
    if errors:
        raise ToolError(f"The configuration has errors, no restart:\n{errors}")

    def restart() -> None:
        hass.async_create_task(hass.services.async_call("homeassistant", "restart", context=Context()))

    # after the answer is on its way
    hass.loop.call_later(2, restart)
    return (
        "Home Assistant restarts in 2 seconds. The AI session stays open; the tools answer again once Home "
        "Assistant is up (about a minute)."
    )


# ---------------------------------------------------------------------------------------------- users


async def _user_by_username(hass: HomeAssistant, username: str) -> Any:
    for user in await hass.auth.async_get_users():
        for cred in user.credentials:
            if cred.auth_provider_type == "homeassistant" and cred.data.get("username") == username:
                return user
    return None


async def list_users(hass: HomeAssistant, args: dict[str, Any]) -> Any:
    out = []
    for user in await hass.auth.async_get_users():
        if user.system_generated:
            continue
        usernames = [c.data.get("username") for c in user.credentials if c.auth_provider_type == "homeassistant"]
        mfa = await hass.auth.async_get_enabled_mfa(user)
        out.append(
            {
                "id": user.id,
                "name": user.name,
                "username": usernames[0] if usernames else None,
                "active": user.is_active,
                "owner": user.is_owner,
                "admin": user.is_admin,
                "local_only": user.local_only,
                "groups": [g.id for g in user.groups],
                "two_factor": sorted(mfa),
            }
        )
    return out


async def create_user(hass: HomeAssistant, args: dict[str, Any]) -> Any:
    from homeassistant.auth.const import GROUP_ID_ADMIN, GROUP_ID_USER
    from homeassistant.auth.providers import homeassistant as auth_ha

    username = _str(args, "username", required=True).lower()
    if not USERNAME.match(username):
        raise ToolError("username: lower case letters, digits, dot, dash, underscore")
    password = args.get("password")
    if not isinstance(password, str) or len(password) < 12:
        raise ToolError("password: at least 12 characters")
    name = _str(args, "name", max_len=100) or username
    group = GROUP_ID_ADMIN if _bool(args, "admin", True) else GROUP_ID_USER
    local_only = _bool(args, "local_only", True)
    provider = auth_ha.async_get_provider(hass)
    existing = await _user_by_username(hass, username)
    if existing is not None:
        if not _bool(args, "replace", False):
            raise ToolError(f"The user {username} exists already")
        if existing.is_owner or existing.system_generated:
            raise ToolError("The owner of Home Assistant is not changed")
        await provider.async_change_password(username, password)
        await hass.auth.async_update_user(existing, name=name, is_active=True, group_ids=[group], local_only=local_only)
        return {"created": False, "username": username, "user_id": existing.id}
    user = await hass.auth.async_create_user(name, group_ids=[group], local_only=local_only)
    try:
        await provider.async_add_auth(username, password)
        credentials = await provider.async_get_or_create_credentials({"username": username})
        await hass.auth.async_link_user(user, credentials)
    except Exception as err:
        await hass.auth.async_remove_user(user)
        raise ToolError(f"The user could not be created: {err}") from err
    return {"created": True, "username": username, "user_id": user.id}


async def set_user_active(hass: HomeAssistant, args: dict[str, Any]) -> Any:
    username = _str(args, "username").lower()
    user_id = _str(args, "user_id")
    active = args.get("active")
    if not isinstance(active, bool):
        raise ToolError('Argument "active" is missing: true or false')
    user = await hass.auth.async_get_user(user_id) if user_id else await _user_by_username(hass, username)
    if user is None:
        raise ToolError("No such user (see list_users)")
    if user.is_owner or user.system_generated:
        raise ToolError("The owner of Home Assistant and users of the system are not changed")
    await hass.auth.async_update_user(user, is_active=active)
    return {"user_id": user.id, "name": user.name, "active": active}


async def _upload_icon(hass: HomeAssistant) -> str:
    """The icon of Beekeeper into the images of Home Assistant, as the frontend uploads one; its address."""
    from aiohttp.web_request import FileField
    from multidict import CIMultiDict, CIMultiDictProxy

    images = hass.data.get("image_upload")
    if images is None:
        raise ToolError("Images cannot be uploaded in this Home Assistant (image_upload is not loaded)")
    data = await hass.async_add_executor_job(_read_file, ICON)
    upload = FileField(
        name="file",
        filename="beekeeper.png",
        file=io.BytesIO(data),
        content_type="image/png",
        headers=CIMultiDictProxy(CIMultiDict()),
    )
    item = await images.async_create_item({"file": upload})
    return f"/api/image/serve/{item['id']}/512x512"


def _read_file(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


async def set_user_picture(hass: HomeAssistant, args: dict[str, Any]) -> Any:
    username = _str(args, "username").lower()
    user_id = _str(args, "user_id")
    picture = _str(args, "picture") or "beekeeper"
    user = await hass.auth.async_get_user(user_id) if user_id else await _user_by_username(hass, username)
    if user is None:
        raise ToolError("No such user (see list_users)")
    if user.is_owner or user.system_generated:
        raise ToolError("The owner of Home Assistant and users of the system are not changed")
    persons = hass.data.get("person")
    storage = getattr(persons, "storage_collection", None)
    if storage is None:
        raise ToolError("The integration person is not loaded")
    person = next((p for p in storage.async_items() if p.get("user_id") == user.id), None)
    if person is None:
        raise ToolError(f"{user.name} has no person in Home Assistant (Settings, People)")
    if picture == "beekeeper":
        url: str | None = await _upload_icon(hass)
    elif picture == "none":
        url = None
    else:
        raise ToolError('picture: "beekeeper" or "none"')
    # the update schema sets missing device trackers to none: keep them
    await storage.async_update_item(
        person["id"], {"picture": url, "device_trackers": list(person.get("device_trackers") or [])}
    )
    return {"user": user.name, "person": person.get("name"), "picture": url}


# ---------------------------------------------------------------------------------------------- list

ENTITY_ID = {"type": "string", "description": 'Entity id, e.g. "light.kitchen"'}

TOOLS: list[Tool] = [
    Tool(
        "ha_overview",
        "Overview of Home Assistant",
        "Version, location, entities per domain, unavailable entities, integrations with problems, repairs and "
        "available updates. Start here.",
        {},
        (),
        "read",
        ha_overview,
    ),
    Tool(
        "list_entities",
        "List entities",
        "Entities with name, state, area, device and integration; filtered by domain, a word, or an area.",
        {
            "domain": {"type": "string", "description": 'Only this domain, e.g. "light" or "sensor"'},
            "query": {"type": "string", "description": "Entity id or name contains this"},
            "area": {"type": "string", "description": "Area name contains this"},
            "limit": {"type": "number", "description": "At most this many (200, up to 2000)"},
        },
        (),
        "read",
        list_entities,
    ),
    Tool(
        "get_states",
        "Get states",
        "Current state and all attributes of entities, with the time of the last change.",
        {"entity_ids": {"type": "array", "items": {"type": "string"}, "description": "Entity ids, up to 100"}},
        ("entity_ids",),
        "read",
        get_states,
    ),
    Tool(
        "get_history",
        "History of an entity",
        "The changes of the state of one entity from the recorder, oldest first.",
        {
            "entity_id": ENTITY_ID,
            "hours": {"type": "number", "description": "How far back, hours (24, up to 720)"},
            "limit": {"type": "number", "description": "At most this many changes, the newest (500)"},
        },
        ("entity_id",),
        "read",
        get_history,
    ),
    Tool(
        "read_log",
        "Read the log of Home Assistant",
        "The last entries of the log of Home Assistant (home-assistant.log, else the journal through the "
        "Supervisor, else the errors Home Assistant keeps), filtered, with tracebacks; to find errors.",
        {
            "lines": {"type": "number", "description": "The last that many matching entries (200, up to 2000)"},
            "level": {"type": "string", "enum": LOG_LEVELS, "description": "At least this level"},
            "query": {"type": "string", "description": 'Entry contains this, e.g. "zha" or "Traceback"'},
        },
        (),
        "read",
        read_log,
    ),
    Tool(
        "list_integrations",
        "List integrations",
        "Configured integrations (config entries) with their state, e.g. setup_error or setup_retry, and why.",
        {"domain": {"type": "string", "description": 'Only this integration, e.g. "zha"'}},
        (),
        "read",
        list_integrations,
    ),
    Tool(
        "list_automations",
        "List automations",
        "Automations with state (on or off), id, mode and when they last ran.",
        {},
        (),
        "read",
        list_automations,
    ),
    Tool(
        "get_automation_config",
        "Configuration of an automation",
        "Triggers, conditions and actions of one automation, as configured.",
        {"entity_id": {"type": "string", "description": 'Entity id of the automation, e.g. "automation.night"'}},
        ("entity_id",),
        "read",
        get_automation_config,
    ),
    Tool(
        "call_service",
        "Call a service (action)",
        "Calls a service of Home Assistant, e.g. light.turn_on, automation.reload or script.turn_on, and waits "
        f"for it (up to {SERVICE_TIMEOUT} s); returns its response if it has one. Changes the home: say what you "
        "do before.",
        {
            "domain": {"type": "string", "description": 'e.g. "light"'},
            "service": {"type": "string", "description": 'e.g. "turn_on"'},
            "data": {"type": "object", "description": "Service data"},
            "target": {"type": "object", "description": 'e.g. {"entity_id": "light.kitchen"} or {"area_id": "..."}'},
        },
        ("domain", "service"),
        "write",
        call_service,
        destructive=True,
    ),
    Tool(
        "reload_integration",
        "Reload an integration",
        "Reloads a configured integration (one entry by entry_id, or all entries of a domain), e.g. after it "
        "failed to set up.",
        {
            "entry_id": {"type": "string", "description": "From list_integrations"},
            "domain": {"type": "string", "description": "All entries of this integration"},
        },
        (),
        "write",
        reload_integration,
    ),
    Tool(
        "restart_home_assistant",
        "Restart Home Assistant",
        "Checks the configuration and restarts Home Assistant (about a minute without it). Only with a reason "
        "and after telling the user.",
        {},
        (),
        "write",
        restart_home_assistant,
        destructive=True,
    ),
    Tool(
        "list_users",
        "List users",
        "Users of Home Assistant (not those of the system) with login name, active, owner, administrator, only "
        "local, two-factor.",
        {},
        (),
        "users",
        list_users,
        reads=True,
    ),
    Tool(
        "create_user",
        "Create a user",
        "Only the portal calls this (device_create_ha_login): it makes the password and keeps it.",
        {
            "username": {"type": "string"},
            "name": {"type": "string"},
            "password": {"type": "string"},
            "admin": {"type": "boolean"},
            "local_only": {"type": "boolean"},
            "replace": {"type": "boolean"},
        },
        ("username", "password"),
        "users",
        create_user,
        destructive=True,
    ),
    Tool(
        "set_user_active",
        "Enable or disable a user",
        "Enables or disables a user (not the owner); a disabled user cannot log in.",
        {
            "username": {"type": "string", "description": "Login name, from list_users"},
            "user_id": {"type": "string", "description": "Or its id"},
            "active": {"type": "boolean"},
        },
        ("active",),
        "users",
        set_user_active,
        destructive=True,
    ),
    Tool(
        "set_user_picture",
        "Picture of a user",
        "Gives the person of a user (not the owner) the icon of Beekeeper as picture, e.g. for the login of the "
        'support, or takes the picture away ("none").',
        {
            "username": {"type": "string", "description": "Login name, from list_users"},
            "user_id": {"type": "string", "description": "Or its id"},
            "picture": {"type": "string", "enum": ["beekeeper", "none"], "description": "beekeeper (default) or none"},
        },
        (),
        "users",
        set_user_picture,
    ),
]

BY_NAME = {t.name: t for t in TOOLS}


def allowed(tool: Tool, rights: dict[str, bool]) -> bool:
    return bool(rights.get("read")) and bool(rights.get(tool.right))


def tools_for(rights: dict[str, bool]) -> list[dict[str, Any]]:
    return [t.describe() for t in TOOLS if allowed(t, rights)]


async def call_tool(hass: HomeAssistant, name: str, args: dict[str, Any], rights: dict[str, bool]) -> dict[str, Any]:
    """{text, isError, truncated?}: the result as MCP gives it, errors of the tool as text for the AI."""
    tool = BY_NAME.get(name)
    if tool is None:
        return {"text": f"No tool {name}", "isError": True}
    if not allowed(tool, rights):
        what = {"read": "the AI access", "write": "changes", "users": "managing users"}[tool.right]
        return {"text": f"The owner does not allow {what} (options of the integration Beekeeper)", "isError": True}
    try:
        out = await tool.run(hass, args if isinstance(args, dict) else {})
    except ToolError as err:
        return {"text": str(err), "isError": True}
    except Exception as err:  # noqa: BLE001 - the AI gets the error, Home Assistant goes on
        return {"text": f"{type(err).__name__}: {err}", "isError": True}
    text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, default=str, indent=1)
    if len(text) > MAX_TEXT:
        return {"text": text[:MAX_TEXT], "isError": False, "truncated": True}
    return {"text": text, "isError": False}
