"""End-to-end through runner.run_script / format_file with the mock console,
checking the traceability record and audit trail."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from groovy_runner import audit, database, runner, settings_store
from groovy_runner.clients.mock import MockGroovyConsoleClient
from groovy_runner.config import settings
from groovy_runner.scripts_registry import discover


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(settings, "output_dir", str(tmp_path / "out"))
    monkeypatch.setattr(settings_store, "_cache", None)
    database.initialize()
    yield tmp_path
    settings_store._cache = None


def test_run_format_trace_and_audit(isolated):
    script = {s.id: s for s in discover()}["asset-reference-report"]
    files = runner.run_script(MockGroovyConsoleClient(), script,
                              {"parentDamPath": ["/content/dam/a", "/content/dam/b"]}, session_id="S1")
    assert [f.status for f in files] == ["ok", "ok"]
    first = files[0]
    # executed script saved and hashed; JSON hashed
    assert Path(first.executed_script_path).read_text().startswith("import groovy.json.JsonOutput")
    assert audit.sha256_file(first.executed_script_path) == first.script_sha256
    assert audit.verify_file(first.json_path, first.json_sha256) == "match"

    out = runner.format_file(first, "asset-reference-excel")
    run = database.get_run(first.run_id)
    assert run["aem_user"] == "mock.user@example.com" and run["actor"]
    assert json.loads(run["config_json"])["parentDamPath"] == "/content/dam/a"
    recorded = json.loads(run["outputs_json"])["asset-reference-excel"]
    assert recorded["path"] == out and audit.verify_file(out, recorded["sha256"]) == "match"

    actions = [e["action"] for e in reversed(database.list_audit())]
    assert actions == ["run.batch_started", "run.completed", "run.completed", "format.completed"]
    assert all(e["session_id"] == "S1" for e in database.list_audit())

    # tampering is detected, and a tampered result is not formatted
    Path(first.json_path).write_text("[]")
    assert audit.verify_file(first.json_path, first.json_sha256) == "MODIFIED"
    with pytest.raises(ValueError, match="changed on disk"):
        runner.format_file(first, "generic-excel")
    assert database.list_audit()[0]["action"] == "format.failed"


def test_failed_run_is_recorded(isolated):
    class Broken(MockGroovyConsoleClient):
        def run_script(self, script):
            raise RuntimeError("AEM returned HTTP 403: nope")

    script = {s.id: s for s in discover()}["page-report"]
    [f] = runner.run_script(Broken(), script, {"rootPath": ["/content/x"]}, session_id="S2")
    assert f.status == "error" and "403" in database.get_run(f.run_id)["error"]
    assert database.list_audit()[0]["action"] == "run.failed"
    with pytest.raises(ValueError):
        runner.format_file(f, "generic-excel")
