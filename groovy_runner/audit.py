from __future__ import annotations

"""Audit trail + traceability helpers.

Every state-changing or data-producing action (runs, formatting,
downloads, settings/credential/preset changes, connection tests) goes
through `log()`, which appends to the `audit_events` table and mirrors to
the rotating log file. The table is append-only: nothing in the app updates
or deletes rows. Secrets are never logged; credentials are identified by
technical-account id + a SHA-256 fingerprint of the file.
"""

import getpass
import hashlib
import json
import socket
from pathlib import Path
from typing import Any

from . import database
from .logging_setup import get_logger


def local_actor() -> str:
    """Who is operating the app: OS user @ host (the app has no login of its own)."""
    try:
        user = getpass.getuser()
    except Exception:
        user = "unknown"
    return f"{user}@{socket.gethostname()}"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def log(action: str, *, session_id: str = "", target: str = "", status: str = "ok",
        details: dict[str, Any] | None = None, aem_user: str = "") -> None:
    """Never raises — an audit write failure is logged, not allowed to break the action."""
    actor = local_actor()
    try:
        database.record_audit(actor=actor, aem_user=aem_user, session_id=session_id, action=action,
                              target=target, status=status, details=details or {})
    except Exception as exc:  # pragma: no cover
        get_logger().error("AUDIT WRITE FAILED for %s %s: %s", action, target, exc)
    get_logger().info("AUDIT %s actor=%s aem_user=%s session=%s target=%s status=%s details=%s",
                      action, actor, aem_user or "-", session_id or "-", target or "-", status,
                      json.dumps(details or {}, default=str)[:1000])


def verify_file(path: str, expected_sha256: str) -> str:
    """"match" | "MODIFIED" | "missing" — used by the Audit page's integrity check."""
    if not path or not Path(path).exists():
        return "missing"
    return "match" if sha256_file(path) == expected_sha256 else "MODIFIED"
