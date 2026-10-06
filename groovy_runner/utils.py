from __future__ import annotations

import os
import re
from pathlib import Path


def harden_file_permissions(path: Path, *, mode: int = 0o600) -> None:
    """Restrict a local data file (SQLite DB, log file, .env) to the owning
    user only. Defaults to 0o600; pass mode=0o700 for a directory.

    POSIX only — chmod doesn't provide equivalent access control on Windows,
    so this is a no-op there. Best-effort: never raises, so it can't block
    app startup or logging."""
    if os.name == "nt":
        return
    try:
        path.chmod(mode)
    except OSError:
        pass


def split_lines(text: str) -> list[str]:
    """One entry per non-blank line (commas also accepted as separators),
    trimmed, order-preserving, de-duplicated."""
    seen: dict[str, None] = {}
    for part in re.split(r"[\n,]", text or ""):
        part = part.strip()
        if part:
            seen.setdefault(part, None)
    return list(seen)


def split_paths(text: str) -> list[str]:
    """Like split_lines() but newline-only: commas and spaces are legal in
    DAM folder names, so JCR path lists must never split on them."""
    seen: dict[str, None] = {}
    for part in (text or "").splitlines():
        part = part.strip()
        if part:
            seen.setdefault(part, None)
    return list(seen)


def normalize_jcr_path(path: str) -> str:
    """Trim whitespace and trailing slashes so "/a/b/" and "/a/b" compare
    equal inside the Groovy script's startsWith(excluded + "/") checks."""
    path = path.strip()
    while len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return path


def slug_for_path(dam_path: str) -> str:
    """Filesystem-safe file stem from the LAST TWO parts of a path, e.g.
    "/content/dam/acme/Product Library/datasheets/" -> "Product-Library_datasheets",
    "/content/acme/en-us/products" -> "en-us_products". Two runs with the same
    stem don't overwrite each other: the runner adds -2, -3... and the zip
    keeps both."""
    path = normalize_jcr_path(dam_path)
    for prefix in ("/content/dam/", "/content/"):
        if path.startswith(prefix):
            path = path[len(prefix):]
            break
    segments = [re.sub(r"[^A-Za-z0-9._-]+", "-", seg).strip("-") for seg in path.strip("/").split("/")]
    return "_".join([s for s in segments if s][-2:]) or "dam"


def display_path(path: Path) -> str:
    """A path as shown on screen: relative to the app folder, so no user or
    machine paths appear in the UI (or in screenshots of it)."""
    from .config import PROJECT_ROOT
    try:
        return Path(path).resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return Path(path).name
