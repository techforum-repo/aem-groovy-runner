from __future__ import annotations

"""Effective settings: SQLite overrides (edited on the Settings page) layered
on top of the .env-backed defaults in config.py.

Same pattern as adobe-access-manager's settings_store, except the AEM author
URL is UI-editable here too. No credential is ever stored: you sign in per
browser session (auth.py / user_token.py).
"""

from dataclasses import dataclass
from typing import Any, Callable

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
    Field("aem_author_url", "AEM author URL", "str", lambda: settings.author_url,
          "e.g. https://author-p12345-e67890.adobeaemcloud.com"),
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
        if field.key == "aem_author_url":
            value = value.rstrip("/")
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


def get(key: str) -> Any:
    return current_values()[key]
