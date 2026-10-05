from __future__ import annotations

"""Local SQLite: run history, audit trail, per-script input presets, and UI setting overrides.
Hardened to 0600 like the sibling tools' databases."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .utils import harden_file_permissions

DB_PATH = Path(__file__).resolve().parent.parent / "groovy_runner.db"


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """Commit on success, roll back on error, and always close. (A bare
    `with sqlite3.connect()` only commits; the connection stays open until
    garbage collection, which holds file locks, which matters on Windows and
    with the background run thread writing concurrently.)"""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def initialize() -> None:
    with _connect() as conn:
        # WAL lets the page read (History, Audit) while a background run writes.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          created_at TEXT NOT NULL,
          session_id TEXT NOT NULL,
          batch_id TEXT NOT NULL,
          aem_host TEXT NOT NULL,
          script_id TEXT NOT NULL,
          label TEXT NOT NULL,
          status TEXT NOT NULL,
          row_count INTEGER,
          running_time TEXT,
          json_path TEXT,
          outputs_json TEXT NOT NULL DEFAULT '{}',
          meta_json TEXT NOT NULL DEFAULT '{}',
          error TEXT,
          config_json TEXT,
          started_at TEXT,
          finished_at TEXT,
          actor TEXT,
          aem_user TEXT,
          template_sha256 TEXT,
          script_sha256 TEXT,
          executed_script_path TEXT,
          json_sha256 TEXT
        )""")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          created_at TEXT NOT NULL,
          actor TEXT NOT NULL,
          aem_user TEXT NOT NULL DEFAULT '',
          session_id TEXT NOT NULL DEFAULT '',
          action TEXT NOT NULL,
          target TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL,
          details_json TEXT NOT NULL DEFAULT '{}'
        )""")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS presets (
          script_id TEXT NOT NULL,
          name TEXT NOT NULL COLLATE NOCASE,
          values_json TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          PRIMARY KEY (script_id, name)
        )""")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS script_formatters (
          script_id TEXT PRIMARY KEY,
          formatters_json TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )""")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )""")
    for path in (DB_PATH, DB_PATH.with_name(DB_PATH.name + "-wal"), DB_PATH.with_name(DB_PATH.name + "-shm")):
        if path.exists():
            harden_file_permissions(path)


# --- runs -------------------------------------------------------------------

def record_run(*, session_id: str, batch_id: str, aem_host: str, script_id: str, label: str, status: str,
               row_count: int | None, running_time: str, json_path: str, meta: dict[str, Any], error: str,
               config: dict[str, Any], started_at: str, finished_at: str, actor: str, aem_user: str,
               template_sha256: str, script_sha256: str, executed_script_path: str, json_sha256: str) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO runs (created_at, session_id, batch_id, aem_host, script_id, label, status, row_count,"
            " running_time, json_path, meta_json, error, config_json, started_at, finished_at, actor, aem_user,"
            " template_sha256, script_sha256, executed_script_path, json_sha256)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (_now(), session_id, batch_id, aem_host, script_id, label, status, row_count, running_time,
             json_path, json.dumps(meta, default=str), error, json.dumps(config), started_at, finished_at,
             actor, aem_user, template_sha256, script_sha256, executed_script_path, json_sha256),
        )
        return int(cur.lastrowid)


def record_outputs(run_id: int, outputs: dict[str, dict[str, str]]) -> None:
    """outputs: formatter id -> {"path", "sha256", "formatted_at"}"""
    with _connect() as conn:
        conn.execute("UPDATE runs SET outputs_json = ? WHERE id = ?", (json.dumps(outputs), run_id))


def list_runs(limit: int = 300) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# --- audit (append-only: no update/delete functions exist on purpose) --------

def record_audit(*, actor: str, aem_user: str, session_id: str, action: str, target: str, status: str,
                 details: dict[str, Any]) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO audit_events (created_at, actor, aem_user, session_id, action, target, status, details_json)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (_now(), actor, aem_user, session_id, action, target, status, json.dumps(details, default=str)),
        )


def list_audit(limit: int = 5000) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM audit_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_run(run_id: int) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


# --- presets (per script) -----------------------------------------------------

def save_preset(script_id: str, name: str, values: dict[str, Any]) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO presets (script_id, name, values_json, updated_at) VALUES (?,?,?,?)"
            " ON CONFLICT(script_id, name) DO UPDATE SET values_json=excluded.values_json, updated_at=excluded.updated_at",
            (script_id, name.strip(), json.dumps(values), _now()),
        )


def list_presets(script_id: str) -> dict[str, dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute("SELECT name, values_json FROM presets WHERE script_id = ? ORDER BY name", (script_id,)).fetchall()
    return {r["name"]: json.loads(r["values_json"]) for r in rows}


def delete_preset(script_id: str, name: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM presets WHERE script_id = ? AND name = ?", (script_id, name))


# --- legacy script → formatter mapping ---------------------------------------
# Superseded by formatter_links.json; read once to seed that file, never written.

def get_formatter_overrides() -> dict[str, list[str]]:
    with _connect() as conn:
        rows = conn.execute("SELECT script_id, formatters_json FROM script_formatters").fetchall()
    return {r["script_id"]: json.loads(r["formatters_json"]) for r in rows}


# --- settings overrides -------------------------------------------------------

def get_setting_overrides() -> dict[str, str]:
    with _connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


def set_setting_overrides(values: dict[str, str]) -> None:
    with _connect() as conn:
        for key, value in values.items():
            conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, value, _now()),
            )


def clear_setting_overrides() -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM settings")
