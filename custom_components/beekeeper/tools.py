"""Tools of the AI access of Beekeeper, run inside Home Assistant.

An AI of the integrator reaches them only during an AI session that a person opened in the portal, for a purpose and
a time; every call is in the audit log of the portal. What they may do decides the owner in the options of this
integration: read (overview, entities, states, history, log, integrations, automations and their traces,
diagnostics, system health, repairs, logbook, devices, updates, backups), write (call services, reload integrations,
restart, options of integrations, backups, updates) and users (list, create, enable and disable users). All of it is
off by default. install_update needs in the portal also that an admin allowed updates by the AI for the asset.
create_user is called only by the portal: it makes the password and keeps it in its access data.
"""

from __future__ import annotations

import asyncio
import dataclasses
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
# this integration: its options are the consent of the owner, the AI does not change them
OWN_DOMAIN = "beekeeper"
# UpdateEntityFeature
UPDATE_INSTALL, UPDATE_SPECIFIC_VERSION, UPDATE_PROGRESS, UPDATE_BACKUP, UPDATE_RELEASE_NOTES = 1, 2, 4, 8, 16
# how long install_update and create_backup wait (s); the agent waits 105 s for an answer
UPDATE_WAIT = 60
BACKUP_WAIT = 60
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


def _when(value: Any) -> str:
    """A time for sorting: kept traces have datetimes, restored ones ISO texts."""
    return value.isoformat() if hasattr(value, "isoformat") else str(value or "")


async def get_automation_trace(hass: HomeAssistant, args: dict[str, Any]) -> Any:
    entity_id = _str(args, "entity_id", required=True).lower()
    run_id = _str(args, "run_id")
    count = _int(args, "runs", 10, 1, 50)
    domain = entity_id.split(".", 1)[0]
    if domain not in ("automation", "script"):
        raise ToolError("entity_id: an automation or a script, e.g. automation.night")
    if "trace" not in hass.config.components:
        raise ToolError("The integration trace is not loaded: Home Assistant keeps no traces")
    from homeassistant.components.trace.util import async_get_trace, async_list_traces

    entry = er.async_get(hass).async_get(entity_id)
    item_id = entry.unique_id if entry else None
    if not item_id:
        st = hass.states.get(entity_id)
        if st is None:
            raise ToolError(f"No {domain} {entity_id}")
        item_id = st.attributes.get("id") if domain == "automation" else entity_id.split(".", 1)[1]
    if not item_id:
        raise ToolError(f"{entity_id} has no id: Home Assistant keeps traces only of automations with an id")
    key = f"{domain}.{item_id}"
    runs = sorted(
        await async_list_traces(hass, domain, key),
        key=lambda r: _when((r.get("timestamp") or {}).get("start")),
        reverse=True,
    )
    out: dict[str, Any] = {
        "entity_id": entity_id,
        "runs": runs[:count],
        **({"note": f"{len(runs)} traces kept, the newest {count} listed"} if len(runs) > count else {}),
    }
    if not run_id:
        run_id = next((r["run_id"] for r in runs if not r.get("not_triggered")), "")
    if not run_id:
        out["trace"] = None
        out["note"] = "No run kept: it did not run since Home Assistant keeps traces (stored_traces, default 5)"
        return out
    try:
        trace = dict(await async_get_trace(hass, key, run_id))
    except KeyError as err:
        raise ToolError(f"No trace {run_id} of {entity_id} (see runs)") from err
    if not _bool(args, "include_config", False):
        trace.pop("config", None)
        trace.pop("blueprint_inputs", None)
    out["trace"] = trace
    return out


def _find_entry(hass: HomeAssistant, args: dict[str, Any]) -> Any:
    entry_id = _str(args, "entry_id")
    domain = _str(args, "domain").lower()
    if entry_id:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            raise ToolError(f"No integration entry {entry_id} (see list_integrations)")
        return entry
    if not domain:
        raise ToolError('Argument "entry_id" or "domain" is missing (see list_integrations)')
    entries = hass.config_entries.async_entries(domain)
    if not entries:
        raise ToolError(f"The integration {domain} is not configured")
    if len(entries) > 1:
        names = ", ".join(f"{e.entry_id} ({e.title})" for e in entries)
        raise ToolError(f"{domain} has {len(entries)} entries, give entry_id: {names}")
    return entries[0]


def _device_entry_id(device: Any) -> str | None:
    """The config entry of a device (one since 2026; before, a set of them)."""
    entry_id = getattr(device, "config_entry_id", None) or getattr(device, "primary_config_entry", None)
    if entry_id:
        return entry_id
    return next(iter(getattr(device, "config_entries", None) or ()), None)


async def get_diagnostics(hass: HomeAssistant, args: dict[str, Any]) -> Any:
    entry = _find_entry(hass, args)
    device_id = _str(args, "device_id")
    diag = hass.data.get("diagnostics")
    platforms = getattr(diag, "platforms", diag)
    info = platforms.get(entry.domain) if isinstance(platforms, dict) else None
    if info is None:
        raise ToolError(f"{entry.domain} has no diagnostics (see read_log and list_integrations instead)")
    if device_id:
        device = dr.async_get(hass).async_get(device_id)
        if device is None:
            raise ToolError(f"No device {device_id} (see list_devices)")
        if _device_entry_id(device) != entry.entry_id and entry.entry_id not in (
            getattr(device, "config_entries", None) or ()
        ):
            raise ToolError(f"The device {device_id} does not belong to {entry.title}")
        if info.device_diagnostics is None:
            raise ToolError(f"{entry.domain} has no diagnostics of devices; leave device_id out")
        data = await info.device_diagnostics(hass, entry, device)
    else:
        if info.config_entry_diagnostics is None:
            raise ToolError(f"{entry.domain} has diagnostics only of devices: give device_id (see list_devices)")
        data = await info.config_entry_diagnostics(hass, entry)
    from homeassistant.helpers import issue_registry as ir
    from homeassistant.loader import async_get_integration

    integration = await async_get_integration(hass, entry.domain)
    issues = [
        {"issue_id": i.issue_id, "severity": getattr(i.severity, "value", i.severity), "active": i.active}
        for (domain, _), i in ir.async_get(hass).issues.items()
        if domain == entry.domain
    ]
    return {
        "domain": entry.domain,
        "title": entry.title,
        "entry_id": entry.entry_id,
        "state": entry.state.value,
        "integration_version": None if integration.is_built_in else str(integration.version),
        "issues": issues,
        "data": data,
    }


async def get_system_health(hass: HomeAssistant, args: dict[str, Any]) -> Any:
    if "system_health" not in hass.config.components:
        raise ToolError("The integration system_health is not loaded")
    from homeassistant.components.system_health import get_info

    try:
        async with asyncio.timeout(45):
            info = await get_info(hass)
    except TimeoutError as err:
        raise ToolError("System health did not answer within 45 s") from err
    domain = _str(args, "domain").lower()
    if domain:
        if domain not in info:
            raise ToolError(f"No system health of {domain}; there are: {', '.join(sorted(info))}")
        return {domain: info[domain]}
    return info


PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _fill(text: str | None, placeholders: dict[str, Any] | None) -> str | None:
    if not text or not placeholders:
        return text
    return PLACEHOLDER.sub(lambda m: str(placeholders.get(m.group(1), m.group(0))), text)


async def list_repairs(hass: HomeAssistant, args: dict[str, Any]) -> Any:
    from homeassistant.helpers import issue_registry as ir
    from homeassistant.helpers.translation import async_get_translations

    with_ignored = _bool(args, "include_ignored", False)
    registry = ir.async_get(hass)
    hidden = getattr(registry, "async_is_hidden", None)
    issues = [
        i
        for i in registry.issues.values()
        if i.active and (with_ignored or not i.dismissed_version) and not (hidden and hidden(i))
    ]
    texts = await async_get_translations(hass, "en", "issues", {i.domain for i in issues}) if issues else {}
    out = []
    for i in sorted(issues, key=lambda i: (i.domain, i.issue_id)):
        prefix = f"component.{i.domain}.issues.{i.translation_key or i.issue_id}."
        description = texts.get(prefix + "description") or next(
            (v for k, v in texts.items() if k.startswith(prefix + "fix_flow.step.") and k.endswith(".description")),
            None,
        )
        severity = getattr(i.severity, "value", i.severity)
        out.append(
            {
                "domain": i.domain,
                **({"for": i.issue_domain} if i.issue_domain and i.issue_domain != i.domain else {}),
                "issue_id": i.issue_id,
                "severity": severity,
                "title": _fill(texts.get(prefix + "title"), i.translation_placeholders),
                "description": (_fill(description, i.translation_placeholders) or "")[:3000] or None,
                "fixable": bool(i.is_fixable),
                "breaks_in": i.breaks_in_ha_version,
                "learn_more": i.learn_more_url,
                "created": _iso(i.created),
                **({"ignored": True} if i.dismissed_version else {}),
            }
        )
    return out


async def get_logbook(hass: HomeAssistant, args: dict[str, Any]) -> Any:
    ids = args.get("entity_ids") or args.get("entity_id")
    if isinstance(ids, str):
        ids = [ids]
    if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)):
        raise ToolError('Argument "entity_ids" must be a list of entity ids')
    entity_ids = [i.strip().lower() for i in ids[:50]] if ids else None
    device_id = _str(args, "device_id")
    device_ids = [device_id] if device_id else None
    if device_id:
        # as the page of a device: its events and the changes of its entities
        if dr.async_get(hass).async_get(device_id) is None:
            raise ToolError(f"No device {device_id} (see list_devices)")
        own = [e.entity_id for e in er.async_entries_for_device(er.async_get(hass), device_id)]
        entity_ids = sorted({*(entity_ids or []), *own}) or None
    narrow = bool(entity_ids or device_ids)
    hours = _int(args, "hours", 24, 1, 24 * 30 if narrow else 72)
    limit = _int(args, "limit", 200, 1, 2000)
    if "logbook" not in hass.config.components or "recorder" not in hass.config.components:
        raise ToolError("The logbook or the recorder of Home Assistant is not running")
    from homeassistant.components.logbook.helpers import async_determine_event_types
    from homeassistant.components.logbook.processor import EventProcessor
    from homeassistant.components.recorder import get_instance

    end = dt_util.utcnow()
    start = end - timedelta(hours=hours)
    processor = EventProcessor(
        hass,
        async_determine_event_types(hass, entity_ids, device_ids),
        entity_ids,
        device_ids,
        None,
        timestamp=False,
        include_entity_name=True,
    )
    events = await get_instance(hass).async_add_executor_job(processor.get_events, start, end)
    users = {u.id: u.name for u in await hass.auth.async_get_users()}
    picked = events[-limit:]
    for e in picked:
        if e.get("context_user_id"):
            e["context_user"] = users.get(e["context_user_id"], e["context_user_id"])
        e.pop("icon", None)
    return {
        "since": _iso(start),
        "events": picked,
        **({"note": f"{len(events)} entries, the newest {limit}"} if len(events) > limit else {}),
    }


def _field(obj: Any, name: str) -> Any:
    """A field of a registry entry, without the deprecated fallbacks of child devices."""
    fields = getattr(type(obj), "__attrs_attrs__", None)
    if fields is not None and name not in {a.name for a in fields}:
        return None
    return getattr(obj, name, None)


async def list_devices(hass: HomeAssistant, args: dict[str, Any]) -> Any:
    query = _str(args, "query").lower()
    area_wanted = _str(args, "area").lower()
    domain = _str(args, "domain").lower()
    only_problems = _bool(args, "unavailable_only", False)
    limit = _int(args, "limit", 200, 1, 2000)
    dev_reg, ent_reg, area_reg = dr.async_get(hass), er.async_get(hass), ar.async_get(hass)
    entities: dict[str, list[str]] = {}
    for ent in ent_reg.entities.values():
        if ent.device_id and not ent.disabled_by:
            entities.setdefault(ent.device_id, []).append(ent.entity_id)
    rows = []
    matched = 0
    for device in dev_reg.devices.values():
        entry_id = _device_entry_id(device)
        entry = hass.config_entries.async_get_entry(entry_id) if entry_id else None
        if domain and (entry is None or entry.domain != domain):
            continue
        name = device.name_by_user or device.name or ""
        area = area_reg.async_get_area(device.area_id) if device.area_id else None
        if query and query not in name.lower() and query not in str(_field(device, "model") or "").lower():
            continue
        if area_wanted and (area is None or area_wanted not in area.name.lower()):
            continue
        ids = sorted(entities.get(device.id, []))
        unavailable = [i for i in ids if (st := hass.states.get(i)) is not None and st.state == "unavailable"]
        if only_problems and not unavailable:
            continue
        matched += 1
        if len(rows) >= limit:
            continue
        parent = _field(device, "parent_device_id") or _field(device, "via_device_id")
        row = {
            "id": device.id,
            "name": name,
            "manufacturer": _field(device, "manufacturer"),
            "model": _field(device, "model"),
            "sw_version": _field(device, "sw_version"),
            "hw_version": _field(device, "hw_version"),
            "area": area.name if area else None,
            "integration": entry.domain if entry else None,
            "entry_id": entry_id,
            "entities": len(ids),
            "unavailable": unavailable,
        }
        if parent:
            via = dev_reg.async_get(parent)
            row["via"] = (via.name_by_user or via.name) if via else parent
        if device.disabled_by:
            row["disabled_by"] = str(device.disabled_by)
        rows.append({k: v for k, v in row.items() if v not in (None, [])})
    return {"matched": matched, "devices": rows}


async def list_updates(hass: HomeAssistant, args: dict[str, Any]) -> Any:
    notes_of = _str(args, "release_notes").lower()
    if notes_of:
        st = hass.states.get(notes_of)
        if st is None or st.domain != "update":
            raise ToolError(f"No update entity {notes_of} (see list_updates)")
        if not (st.attributes.get("supported_features") or 0) & UPDATE_RELEASE_NOTES:
            return {"entity_id": notes_of, "release_notes": None, "release_url": st.attributes.get("release_url")}
        component = hass.data.get("update")
        entity = component.get_entity(notes_of) if component is not None else None
        if entity is None:
            raise ToolError(f"No update entity {notes_of}")
        notes = await entity.async_release_notes()
        return {"entity_id": notes_of, "release_notes": (notes or "")[:20000], "release_url": st.attributes.get("release_url")}
    every = _bool(args, "all", False)
    ent_reg = er.async_get(hass)
    out = []
    for st in sorted(hass.states.async_all("update"), key=lambda s: s.entity_id):
        if not every and st.state != "on" and not st.attributes.get("in_progress"):
            continue
        a = st.attributes
        features = a.get("supported_features") or 0
        entry = ent_reg.async_get(st.entity_id)
        row = {
            "entity_id": st.entity_id,
            "title": a.get("title") or st.name,
            "available": st.state == "on",
            "installed": a.get("installed_version"),
            "latest": a.get("latest_version"),
            "skipped": a.get("skipped_version"),
            "in_progress": a.get("in_progress") or None,
            "percentage": a.get("update_percentage"),
            "summary": a.get("release_summary"),
            "release_url": a.get("release_url"),
            "auto_update": a.get("auto_update") or None,
            "platform": entry.platform if entry else None,
            "can": [
                name
                for bit, name in (
                    (UPDATE_INSTALL, "install"),
                    (UPDATE_SPECIFIC_VERSION, "version"),
                    (UPDATE_BACKUP, "backup"),
                    (UPDATE_RELEASE_NOTES, "release_notes"),
                )
                if features & bit
            ],
        }
        out.append({k: v for k, v in row.items() if v not in (None, [], "")})
    return out


def _backup_manager(hass: HomeAssistant) -> Any:
    manager = hass.data.get("backup")
    if manager is None or not hasattr(manager, "async_get_backups"):
        raise ToolError("The integration backup of Home Assistant is not loaded")
    return manager


async def list_backups(hass: HomeAssistant, args: dict[str, Any]) -> Any:
    manager = _backup_manager(hass)
    limit = _int(args, "limit", 20, 1, 200)
    backups, errors = await manager.async_get_backups()
    rows = []
    for b in sorted(backups.values(), key=lambda b: b.date, reverse=True)[:limit]:
        row = {
            "backup_id": b.backup_id,
            "name": b.name,
            "date": b.date,
            "homeassistant": b.homeassistant_version if b.homeassistant_included else None,
            "database": b.database_included,
            "addons": [getattr(x, "slug", None) or getattr(x, "name", str(x)) for x in b.addons],
            "folders": [str(getattr(f, "value", f)) for f in b.folders],
            "agents": {k: {"size": v.size, "protected": v.protected} for k, v in b.agents.items()},
            "automatic": getattr(b, "with_automatic_settings", None),
            "failed_agents": list(getattr(b, "failed_agent_ids", None) or []),
        }
        rows.append({k: v for k, v in row.items() if v not in (None, [], "")})
    config = getattr(manager, "config", None)
    data = getattr(config, "data", None)
    create = getattr(data, "create_backup", None)
    schedule = getattr(data, "schedule", None)
    return {
        "state": str(getattr(manager.state, "value", manager.state)),
        "count": len(backups),
        "backups": rows,
        "automatic": {
            "agents": list(getattr(create, "agent_ids", None) or []),
            "encrypted": bool(getattr(create, "password", None)),
            "recurrence": str(getattr(getattr(schedule, "recurrence", None), "value", None) or "") or None,
            "last_completed": _iso(getattr(data, "last_completed_automatic_backup", None)),
        },
        **({"agent_errors": {k: str(v) for k, v in errors.items()}} if errors else {}),
    }


# ---------------------------------------------------------------------------------------------- write


async def _service(
    hass: HomeAssistant,
    domain: str,
    service: str,
    data: dict[str, Any],
    target: dict[str, Any] | None = None,
    return_response: bool = False,
    wait: float = SERVICE_TIMEOUT,
) -> tuple[bool, Any]:
    """Calls a service and waits for it up to wait seconds; (finished, response). It is not cancelled after that."""
    task = hass.async_create_task(
        hass.services.async_call(
            domain,
            service,
            data,
            blocking=True,
            target=target,
            context=Context(),
            return_response=return_response,
        )
    )
    done, _ = await asyncio.wait({task}, timeout=wait)
    if not done:
        return False, None
    return True, task.result()


SECRET_KEY = re.compile(r"pass|secret|token|credential|(^|_)(pin|key|api_?key)($|_)", re.IGNORECASE)
# options flows this tool started and not finished, per entry: a new start aborts the old one
_OPTION_FLOWS: dict[str, str] = {}


def _redact(value: Any) -> Any:
    """Values of keys like password, token or api_key as ***: the AI does not see them."""
    if isinstance(value, dict):
        return {
            k: "***" if isinstance(k, str) and SECRET_KEY.search(k) and v not in (None, "") else _redact(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    return value


def _secret_field(field: dict[str, Any]) -> bool:
    text = (field.get("selector") or {}).get("text") if isinstance(field.get("selector"), dict) else None
    return bool(SECRET_KEY.search(str(field.get("name", "")))) or (
        isinstance(text, dict) and text.get("type") == "password"
    )


async def _describe_flow(hass: HomeAssistant, entry: Any, result: dict[str, Any]) -> dict[str, Any]:
    """A step of an options flow for the AI: its fields with labels, the menu, or how it ended."""
    try:
        from homeassistant.helpers.data_entry_flow import _BaseFlowManagerView

        data = _BaseFlowManagerView._prepare_result_json(None, result)  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001 - without the fields then
        data = {k: v for k, v in result.items() if k not in ("data_schema", "data", "context")}
    kind = str(getattr(data.get("type"), "value", data.get("type")))
    out: dict[str, Any] = {"type": kind, "flow_id": data.get("flow_id"), "step_id": data.get("step_id")}
    from homeassistant.helpers.translation import async_get_translations

    texts = await async_get_translations(hass, "en", "options", [entry.domain])
    prefix = f"component.{entry.domain}.options.step.{data.get('step_id')}."
    placeholders = data.get("description_placeholders")
    if kind == "form":
        fields = []
        for field in data.get("data_schema") or []:
            field = dict(field)
            name = field.get("name")
            if _secret_field(field):
                field.pop("default", None)
                description = field.get("description")
                if isinstance(description, dict) and description.get("suggested_value") not in (None, ""):
                    field["description"] = {**description, "suggested_value": "***"}
            label = texts.get(f"{prefix}data.{name}")
            help_text = texts.get(f"{prefix}data_description.{name}")
            fields.append({**field, **({"label": label} if label else {}), **({"help": help_text} if help_text else {})})
        out.update(
            {
                "title": _fill(texts.get(prefix + "title"), placeholders),
                "description": _fill(texts.get(prefix + "description"), placeholders),
                "fields": fields,
                "errors": data.get("errors") or None,
                "last_step": data.get("last_step"),
            }
        )
    elif kind == "menu":
        options = data.get("menu_options") or []
        names = options if isinstance(options, list) else list(options)
        out["menu"] = {n: texts.get(f"{prefix}menu_options.{n}") or n for n in names}
        out["title"] = _fill(texts.get(prefix + "title"), placeholders)
    elif kind == "abort":
        reason = data.get("reason")
        out["reason"] = _fill(texts.get(f"component.{entry.domain}.options.abort.{reason}"), placeholders) or reason
    elif kind == "create_entry":
        out["done"] = True
    return {k: v for k, v in out.items() if v is not None}


def _abort_flow(hass: HomeAssistant, flow_id: str) -> None:
    try:
        hass.config_entries.options.async_abort(flow_id)
    except Exception:  # noqa: BLE001 - it is gone already
        pass


async def _start_options(hass: HomeAssistant, entry: Any) -> dict[str, Any]:
    if not entry.supports_options:
        raise ToolError(f"{entry.title} ({entry.domain}) has no options")
    old = _OPTION_FLOWS.pop(entry.entry_id, None)
    if old:
        _abort_flow(hass, old)
    result = await hass.config_entries.options.async_init(
        entry.entry_id, context={"source": "user", "show_advanced_options": True}
    )
    if result.get("flow_id") and str(getattr(result["type"], "value", result["type"])) in ("form", "menu"):
        _OPTION_FLOWS[entry.entry_id] = result["flow_id"]
    return result


async def get_integration_options(hass: HomeAssistant, args: dict[str, Any]) -> Any:
    entry = _find_entry(hass, args)
    result = await _start_options(hass, entry)
    step = await _describe_flow(hass, entry, result)
    # only a look: the dialog is not left open
    if _OPTION_FLOWS.pop(entry.entry_id, None):
        _abort_flow(hass, result["flow_id"])
    step.pop("flow_id", None)
    return {
        "entry_id": entry.entry_id,
        "domain": entry.domain,
        "title": entry.title,
        "options": _redact(dict(entry.options)),
        "first_step": step,
    }


async def set_integration_options(hass: HomeAssistant, args: dict[str, Any]) -> Any:
    from homeassistant.data_entry_flow import InvalidData, UnknownFlow

    entry = _find_entry(hass, args)
    if entry.domain == OWN_DOMAIN:
        raise ToolError("The options of Beekeeper decide what the AI may do here: only the owner changes them")
    flows = hass.config_entries.options
    flow_id = _str(args, "flow_id")
    user_input = _dict(args, "input")
    next_step = _str(args, "next_step")
    if _bool(args, "abort", False):
        if flow_id:
            _abort_flow(hass, flow_id)
        _OPTION_FLOWS.pop(entry.entry_id, None)
        return {"aborted": True}
    if flow_id:
        if _OPTION_FLOWS.get(entry.entry_id) != flow_id:
            raise ToolError("No open options dialog with this flow_id for this entry: leave flow_id out to start again")
    else:
        result = await _start_options(hass, entry)
        if result.get("flow_id") is None or result["flow_id"] != _OPTION_FLOWS.get(entry.entry_id):
            return await _describe_flow(hass, entry, result)
        flow_id = result["flow_id"]
        kind = str(getattr(result["type"], "value", result["type"]))
        if (kind == "form" and user_input is None) or (kind == "menu" and not next_step):
            return await _describe_flow(hass, entry, result)
    before = dict(entry.options)
    try:
        result = await flows.async_configure(flow_id, {"next_step_id": next_step} if next_step else (user_input or {}))
    except UnknownFlow as err:
        _OPTION_FLOWS.pop(entry.entry_id, None)
        raise ToolError("The options dialog is gone: start again without flow_id") from err
    except InvalidData as err:
        raise ToolError(f"Invalid input: {getattr(err, 'schema_errors', None) or err}") from err
    except Exception as err:
        if type(err).__name__ in ("Invalid", "MultipleInvalid"):
            raise ToolError(f"Invalid input: {err}") from err
        raise
    step = await _describe_flow(hass, entry, result)
    if step["type"] in ("form", "menu"):
        return step
    _OPTION_FLOWS.pop(entry.entry_id, None)
    if step["type"] == "create_entry":
        after = dict(entry.options)
        changed = sorted(k for k in {*before, *after} if before.get(k) != after.get(k))
        return {"done": True, "changed": changed, "options": _redact(after)}
    return step


async def create_backup(hass: HomeAssistant, args: dict[str, Any]) -> Any:
    manager = _backup_manager(hass)
    create = manager.config.data.create_backup
    agents = list(create.agent_ids) or [a for a in manager.backup_agents if a.endswith(".local")]
    if not agents:
        raise ToolError("No place for backups is set up (Settings, System, Backups)")
    name = _str(args, "name", max_len=100) or f"Beekeeper {dt_util.now().strftime('%Y-%m-%d %H:%M')}"
    try:
        new = await manager.async_initiate_backup(
            agent_ids=agents,
            include_addons=create.include_addons,
            include_all_addons=create.include_all_addons,
            include_database=_bool(args, "include_database", create.include_database),
            include_folders=create.include_folders,
            include_homeassistant=True,
            name=name,
            # encrypted as the automatic backups, with their key
            password=create.password,
        )
    except Exception as err:  # noqa: BLE001 - e.g. a backup is running already
        raise ToolError(f"No backup: {err}") from err
    for _ in range(BACKUP_WAIT):
        if str(getattr(manager.state, "value", manager.state)) != "create_backup":
            break
        await asyncio.sleep(1)
    running = str(getattr(manager.state, "value", manager.state)) == "create_backup"
    event = getattr(manager, "last_action_event", None)
    return {
        "backup_job_id": getattr(new, "backup_job_id", None),
        "name": name,
        "agents": agents,
        "encrypted": bool(create.password),
        "finished": not running,
        "last_event": event,
        **({"note": "Still running: see list_backups"} if running else {}),
    }


async def install_update(hass: HomeAssistant, args: dict[str, Any]) -> Any:
    entity_id = _str(args, "entity_id", required=True).lower()
    version = _str(args, "version")
    st = hass.states.get(entity_id)
    if st is None or st.domain != "update":
        raise ToolError(f"No update entity {entity_id} (see list_updates)")
    features = st.attributes.get("supported_features") or 0
    if not features & UPDATE_INSTALL:
        raise ToolError(f"{st.name} cannot be installed from Home Assistant")
    if st.attributes.get("in_progress"):
        raise ToolError(f"{st.name} is being installed already")
    if st.state != "on" and not version:
        raise ToolError(f"{st.name} is up to date ({st.attributes.get('installed_version')})")
    data: dict[str, Any] = {"entity_id": entity_id}
    if version:
        if not features & UPDATE_SPECIFIC_VERSION:
            raise ToolError(f"{st.name} installs only the latest version")
        data["version"] = version
    notes = []
    if _bool(args, "backup", True):
        if features & UPDATE_BACKUP:
            data["backup"] = True
        else:
            notes.append("This update makes no backup itself (for an integration: create_backup before)")
    entry = er.async_get(hass).async_get(entity_id)
    before = st.attributes.get("installed_version")
    try:
        finished, _ = await _service(hass, "update", "install", data, wait=UPDATE_WAIT)
    except Exception as err:  # noqa: BLE001 - the AI gets why
        raise ToolError(f"The update failed: {err}") from err
    now = hass.states.get(entity_id)
    after = now.attributes.get("installed_version") if now else None
    if entry is not None and entry.platform == "hacs" and finished:
        notes.append("Home Assistant loads it after a restart (restart_home_assistant)")
    if not finished:
        notes.append(f"Still installing after {UPDATE_WAIT} s: see list_updates (in_progress)")
    return {"entity_id": entity_id, "finished": finished, "before": before, "installed": after, "notes": notes}



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
    finished, response = await _service(hass, domain, service, data, target, wants_response)
    if not finished:
        return {"ok": True, "note": f"{domain}.{service} still runs after {SERVICE_TIMEOUT} s"}
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
        "get_automation_trace",
        "Trace of an automation or script",
        "The last runs of an automation or a script (trigger, state, error, last step) and, step by step, one "
        "run: which trigger fired, which conditions passed or failed, what each action did, the variables. "
        "The way to find out why an automation did not do what it should.",
        {
            "entity_id": {"type": "string", "description": 'e.g. "automation.night" or "script.goodbye"'},
            "run_id": {"type": "string", "description": "This run, from runs; the newest run if left out"},
            "runs": {"type": "number", "description": "List this many runs, the newest (10, up to 50)"},
            "include_config": {"type": "boolean", "description": "Also the configuration of the run (false)"},
        },
        ("entity_id",),
        "read",
        get_automation_trace,
    ),
    Tool(
        "get_diagnostics",
        "Diagnostics of an integration",
        "The diagnostics of an integration entry or one of its devices, as Home Assistant offers them for bug "
        "reports (the integration hides passwords and keys in them), with its repairs.",
        {
            "entry_id": {"type": "string", "description": "From list_integrations"},
            "domain": {"type": "string", "description": "Or the integration, if it has one entry"},
            "device_id": {"type": "string", "description": "Diagnostics of this device of it (from list_devices)"},
        },
        (),
        "read",
        get_diagnostics,
    ),
    Tool(
        "get_system_health",
        "System health",
        "System health of Home Assistant: installation type, versions, database, cloud, Supervisor, "
        "integrations that report their state.",
        {"domain": {"type": "string", "description": 'Only this part, e.g. "homeassistant" or "recorder"'}},
        (),
        "read",
        get_system_health,
    ),
    Tool(
        "list_repairs",
        "List repairs",
        "The repairs Home Assistant shows (Settings, Repairs) with title, description, severity, the version "
        "that breaks and a link to learn more.",
        {"include_ignored": {"type": "boolean", "description": "Also those the owner ignored (false)"}},
        (),
        "read",
        list_repairs,
    ),
    Tool(
        "get_logbook",
        "Logbook",
        "What happened, newest last: changes of entities and what caused them (a user, an automation, a "
        "script, a service), starts of automations. Filtered by entities or a device; to find out why "
        "something switched.",
        {
            "entity_ids": {"type": "array", "items": {"type": "string"}, "description": "Only these entities"},
            "device_id": {"type": "string", "description": "Only this device (from list_devices)"},
            "hours": {"type": "number", "description": "How far back, hours (24; up to 720 filtered, else 72)"},
            "limit": {"type": "number", "description": "At most this many entries, the newest (200, up to 2000)"},
        },
        (),
        "read",
        get_logbook,
    ),
    Tool(
        "list_devices",
        "List devices",
        "Devices with manufacturer, model, firmware, area, integration, the device they connect through, and "
        "their unavailable entities.",
        {
            "query": {"type": "string", "description": "Name or model contains this"},
            "area": {"type": "string", "description": "Area name contains this"},
            "domain": {"type": "string", "description": 'Only of this integration, e.g. "zha"'},
            "unavailable_only": {"type": "boolean", "description": "Only devices with unavailable entities"},
            "limit": {"type": "number", "description": "At most this many (200, up to 2000)"},
        },
        (),
        "read",
        list_devices,
    ),
    Tool(
        "list_updates",
        "List updates",
        "Available updates (update entities): Home Assistant, add-ons, integrations of HACS, firmware of "
        "devices; what each can do (install, a version, a backup). With release_notes the notes of one.",
        {
            "all": {"type": "boolean", "description": "Also those that are up to date (false)"},
            "release_notes": {"type": "string", "description": "Only the release notes of this update entity"},
        },
        (),
        "read",
        list_updates,
    ),
    Tool(
        "list_backups",
        "List backups",
        "Backups of Home Assistant (newest first) with where they are, size, encrypted or not, and the "
        "settings of the automatic backups.",
        {"limit": {"type": "number", "description": "At most this many (20)"}},
        (),
        "read",
        list_backups,
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
        "get_integration_options",
        "Options of an integration",
        "The current options of an integration entry and the first step of its options dialog (Configure): "
        "the fields with labels, defaults and what they accept, or a menu. Passwords and keys show as ***.",
        {
            "entry_id": {"type": "string", "description": "From list_integrations"},
            "domain": {"type": "string", "description": "Or the integration, if it has one entry"},
        },
        (),
        "write",
        get_integration_options,
        reads=True,
    ),
    Tool(
        "set_integration_options",
        "Change the options of an integration",
        "Goes through the options dialog of an integration entry as the owner would with Configure. Without "
        "flow_id it starts it (and, with input or next_step, fills the first step); then each answer gives the "
        "next step with its flow_id, until done (the options are saved, the integration reloads) or aborted. "
        "Fields left out keep their defaults. Say what you change before.",
        {
            "entry_id": {"type": "string", "description": "From list_integrations"},
            "domain": {"type": "string", "description": "Or the integration, if it has one entry"},
            "flow_id": {"type": "string", "description": "Of the open dialog, from the previous answer"},
            "input": {"type": "object", "description": "Values of the fields of the current step"},
            "next_step": {"type": "string", "description": "In a menu: the step to go to"},
            "abort": {"type": "boolean", "description": "Close the dialog without saving"},
        },
        (),
        "write",
        set_integration_options,
        destructive=True,
    ),
    Tool(
        "create_backup",
        "Create a backup",
        "Creates a backup of Home Assistant where and as the automatic backups are kept (encrypted with their "
        "key), e.g. before an update. Waits up to a minute; list_backups shows it.",
        {
            "name": {"type": "string", "description": 'e.g. "Before the update of HACS"'},
            "include_database": {"type": "boolean", "description": "With the database (as the automatic ones)"},
        },
        (),
        "write",
        create_backup,
    ),
    Tool(
        "install_update",
        "Install an update",
        "Installs an update (see list_updates): an integration of HACS, the firmware of a device, an add-on or "
        "Home Assistant (with the Supervisor). Home Assistant and add-ons make a backup before if backup is "
        "true; for an integration make one with create_backup before and restart afterwards. Waits up to a "
        "minute. Tell the user before.",
        {
            "entity_id": {"type": "string", "description": 'Update entity, e.g. "update.hacs_update"'},
            "version": {"type": "string", "description": "This version instead of the latest, if it can"},
            "backup": {"type": "boolean", "description": "Backup before, where the update can (true)"},
        },
        ("entity_id",),
        "write",
        install_update,
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


def _json_default(value: Any) -> Any:
    """States, contexts, times and sets in results (traces, logbook, diagnostics)."""
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    return str(value)


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
    text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, default=_json_default, indent=1)
    if len(text) > MAX_TEXT:
        return {"text": text[:MAX_TEXT], "isError": False, "truncated": True}
    return {"text": text, "isError": False}
