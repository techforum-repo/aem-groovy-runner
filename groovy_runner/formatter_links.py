from __future__ import annotations

"""Links between scripts and formatters, kept apart from both.

Scripts (scripts/) know nothing about formatting, and formatters
(groovy_runner/formatters/) know nothing about scripts. This module owns the
only place they meet: `formatter_links.json` at the project root.

    {
      "asset-reference-report": {"formatters": ["asset-reference-excel", "generic-excel"],
                                 "default": "asset-reference-excel"},
      "page-report": {"formatters": ["generic-excel"], "default": "generic-excel"}
    }

It's a plain, reviewable file that can live in git, so links are shared
with whoever uses the same checkout. It's edited from the Formatters page
(every change is audited) or by hand.

A script with no entry is "not linked": it falls back to Generic Excel and
the UI flags it. When the file doesn't exist yet it is created once from
the old places links used to live (manifest.json "formatters" lists and the
Scripts-page mapping stored in the database), so nothing is lost; after
that, neither of those is read again.
"""

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .config import PROJECT_ROOT
from .formatters import FORMATTERS

LINKS_PATH = PROJECT_ROOT / "formatter_links.json"
FALLBACK_FORMATTER = "generic-excel"


@dataclass(frozen=True)
class Link:
    formatters: tuple[str, ...]  # default first
    default: str
    linked: bool  # False = no entry for this script; using the fallback

    @property
    def unknown(self) -> list[str]:
        return [f for f in self.formatters if f not in FORMATTERS]


def _normalize(entry: Any) -> tuple[list[str], str] | None:
    if isinstance(entry, list):  # tolerate the short form: ["default", "other", ...]
        entry = {"formatters": entry}
    if not isinstance(entry, dict):
        return None
    ids = [str(f) for f in entry.get("formatters") or [] if str(f).strip()]
    default = str(entry.get("default") or (ids[0] if ids else ""))
    if not default:
        return None
    ordered = [default] + [f for f in dict.fromkeys(ids) if f != default]
    return ordered, default


def load(path: Path | None = None) -> dict[str, tuple[list[str], str]]:
    """script id -> (formatter ids with the default first, default id)."""
    path = path or LINKS_PATH  # resolved per call, so LINKS_PATH can be redirected (tests)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name} must be a JSON object of script id -> link")
    links = {}
    for script_id, entry in raw.items():
        normalized = _normalize(entry)
        if normalized:
            links[str(script_id)] = normalized
    return links


def save(links: dict[str, tuple[list[str], str]], path: Path | None = None) -> None:
    """Atomic write, sorted by script id so diffs stay readable."""
    path = path or LINKS_PATH
    data = {sid: {"formatters": ids, "default": default} for sid, (ids, default) in sorted(links.items())}
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".formatter_links.", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def link_for(script_id: str, links: dict[str, tuple[list[str], str]]) -> Link:
    if script_id in links:
        ids, default = links[script_id]
        return Link(tuple(ids), default, linked=True)
    return Link((FALLBACK_FORMATTER,), FALLBACK_FORMATTER, linked=False)


def scripts_linked_to(formatter_id: str, links: dict[str, tuple[list[str], str]]) -> list[str]:
    return sorted(sid for sid, (ids, _) in links.items() if formatter_id in ids)


def from_grid(rows: Iterable[dict[str, Any]], existing: dict[str, tuple[list[str], str]],
              shown_script_ids: set[str]) -> dict[str, tuple[list[str], str]]:
    """Links from the Formatters-page grid: one row per script with
    `script_id`, `default` (formatter id or None) and `ticked` (formatter ids).
    A default that wasn't ticked is added; a row with neither is unlinked.
    Links for scripts not shown (not in scripts/ right now) are kept."""
    links = {sid: v for sid, v in existing.items() if sid not in shown_script_ids}
    for row in rows:
        ticked, default = list(row.get("ticked") or []), row.get("default")
        if not default and not ticked:
            continue
        default = default or ticked[0]
        links[row["script_id"]] = ([default] + [f for f in ticked if f != default], default)
    return links


def migrate_if_missing(legacy: Iterable[tuple[str, list[str]]], path: Path | None = None) -> bool:
    """Create the links file once from legacy (script id, formatter ids)
    pairs: manifest lists, then DB overrides, later entries winning.
    Returns True if it wrote the file."""
    path = path or LINKS_PATH
    if path.exists():
        return False
    links: dict[str, tuple[list[str], str]] = {}
    for script_id, ids in legacy:
        normalized = _normalize(list(ids))
        if normalized:
            links[script_id] = normalized
    save(links, path)
    return True
