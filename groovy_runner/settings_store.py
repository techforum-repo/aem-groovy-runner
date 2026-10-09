from __future__ import annotations

"""Effective settings: SQLite overrides (edited on the Settings page) layered
on top of the .env-backed defaults in config.py.

Same pattern as adobe-access-manager's settings_store. AEM environments
(name + author URL) are a list edited on the Settings page; you pick one in
the sidebar. No credential is ever stored here: you sign in per environment
and browser session (auth.py / user_token.py; optionally the OS keychain,
token_store.py, keyed by the environment's host).
"""

import json
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

from . import database
from .config import settings

_BOOL_TRUE = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str  # "str" | "float" | "bool"
    default: Callable[[], Any]
    help: str = ""


FIELDS: list[Field] = [
    Field("mock_mode", "Mock mode (sample data, no AEM calls)", "bool", lambda: settings.mock_mode,
          "Turn off once the author URL is set, then sign in from the sidebar."),
    Field("groovy_console_endpoint", "Groovy Console endpoint", "str", lambda: settings.groovy_console_endpoint),
    Field("http_timeout", "Per-run timeout (seconds)", "float", lambda: settings.http_timeout,
          "How long one Groovy run may take before giving up."),
]

_FIELDS_BY_KEY = {f.key: f for f in FIELDS}


def _parse(field: Field, raw: str) -> Any:
    if field.kind == "float":
        return float(raw)
    if field.kind == "bool":
        return str(raw).strip().lower() in _BOOL_TRUE
    return raw


def _serialize(field: Field, value: Any) -> str:
    if field.kind == "bool":
        return "true" if value else "false"
    if field.kind == "str":
        value = str(value).strip()
    return str(value)


_cache: dict[str, Any] | None = None


def current_values() -> dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache
    overrides = database.get_setting_overrides()
    values: dict[str, Any] = {}
    for field in FIELDS:
        raw = overrides.get(field.key)
        try:
            values[field.key] = field.default() if raw is None else _parse(field, raw)
        except (TypeError, ValueError):
            values[field.key] = field.default()
    _cache = values
    return values


def overridden_keys() -> set[str]:
    return set(database.get_setting_overrides())


def save(values: dict[str, Any]) -> None:
    global _cache
    database.set_setting_overrides({k: _serialize(_FIELDS_BY_KEY[k], v) for k, v in values.items() if k in _FIELDS_BY_KEY})
    _cache = None


def reset() -> None:
    global _cache
    database.clear_setting_overrides()
    _cache = None


def reset_fields() -> None:
    """Back to the .env defaults for the general fields only (environments are kept)."""
    global _cache
    database.clear_setting_overrides([f.key for f in FIELDS])
    _cache = None


def get(key: str) -> Any:
    return current_values()[key]


# --- AEM environments ---------------------------------------------------------

ENVIRONMENTS_KEY = "environments"  # JSON list of {"name", "url"} in the settings table
LAST_ENVIRONMENT_KEY = "last_environment"  # the one picked last, preselected on the next visit
LEGACY_URL_KEY = "aem_author_url"  # the single author URL saved before environments existed
DEFAULT_NAME = "Default"


@dataclass(frozen=True)
class Environment:
    name: str
    url: str  # author URL, no trailing slash

    @property
    def host(self) -> str:
        return urlparse(self.url).netloc or self.url


def _normalize_url(url: str) -> str:
    return str(url or "").strip().rstrip("/")


def environments() -> list[Environment]:
    """The configured environments, in order. Before any were saved: the one
    author URL from earlier versions (settings DB, else .env) as "Default"."""
    overrides = database.get_setting_overrides()
    raw = overrides.get(ENVIRONMENTS_KEY)
    if raw:
        try:
            return [Environment(str(e["name"]), _normalize_url(e["url"])) for e in json.loads(raw)
                    if str(e.get("name") or "").strip() and _normalize_url(e.get("url"))]
        except (ValueError, TypeError, KeyError, AttributeError):
            return []
    legacy = _normalize_url(overrides.get(LEGACY_URL_KEY) or settings.author_url)
    return [Environment(DEFAULT_NAME, legacy)] if legacy else []


def validate_environments(rows: list[dict[str, Any]]) -> tuple[list[Environment], list[str]]:
    """Rows from the Settings editor -> (environments, problems). Blank rows are ignored."""
    envs, problems, seen = [], [], set()
    for row in rows:
        name, url = str(row.get("name") or "").strip(), _normalize_url(row.get("url"))
        if not name and not url:
            continue
        if not name:
            problems.append(f"{url}: give it a name")
            continue
        if name.lower() in seen:
            problems.append(f"“{name}” is used twice: names must be unique")
            continue
        if not url.startswith(("https://", "http://")) or not urlparse(url).netloc:
            problems.append(f"“{name}”: the author URL must look like https://author-p12345-e67890.adobeaemcloud.com")
            continue
        seen.add(name.lower())
        envs.append(Environment(name, url))
    return envs, problems


def save_environments(envs: list[Environment]) -> None:
    database.set_setting_overrides({ENVIRONMENTS_KEY: json.dumps([{"name": e.name, "url": e.url} for e in envs])})


def environment(name: str | None) -> Environment | None:
    return next((e for e in environments() if e.name == name), None)


def last_environment() -> str:
    return database.get_setting_overrides().get(LAST_ENVIRONMENT_KEY, "")


def remember_last_environment(name: str) -> None:
    if name and name != last_environment():
        database.set_setting_overrides({LAST_ENVIRONMENT_KEY: name})
