from __future__ import annotations

"""Formatter registry. To add one: create a module exposing FORMATTER
(see base.Formatter) and list it here; scripts opt in via their manifest's
"formatters" list (first entry = default)."""

from . import asset_reference, generic_excel
from .base import Formatter

FORMATTERS: dict[str, Formatter] = {f.id: f for f in (asset_reference.FORMATTER, generic_excel.FORMATTER)}


def get(formatter_id: str) -> Formatter:
    try:
        return FORMATTERS[formatter_id]
    except KeyError:
        raise KeyError(f"Unknown formatter '{formatter_id}' (available: {', '.join(FORMATTERS)})") from None
