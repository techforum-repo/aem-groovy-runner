"""Batch runs: discover roots, run once per root, combine results."""
from __future__ import annotations

import time
from pathlib import Path

import openpyxl
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from groovy_runner import database, runner, settings_store
from groovy_runner.clients.mock import MockGroovyConsoleClient
from groovy_runner.config import settings
from groovy_runner.formatters import base
from groovy_runner.scripts_registry import discover

APP = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(settings, "output_dir", str(tmp_path / "out"))
    settings_store._cache = None
    database.initialize()
    yield tmp_path
    settings_store._cache = None


def _script(sid):
    return {s.id: s for s in discover()}[sid]


def test_batch_discovers_sites_and_runs_one_per_root(isolated):
    script = _script("page-report")
    found = []
    results = runner.run_script(MockGroovyConsoleClient(), script, {"rootPath": ["/content"]}, session_id="S",
                                batch={"excludes": list(script.batch.default_excludes), "levels": 1},
                                on_discovered=found.extend)
    assert [r.label for r in results] == ["/content/acme", "/content/acme-corporate", "/content/acme-careers"]
    assert all(r.ok for r in results)
    assert "/content/campaigns" in found[0].skipped  # a default skip (an AEM system area that can be a page)
    assert "/content/dam" not in [r.label for r in results]  # not a page, so never a site: no exclude needed
    assert "run.batch_discovered" in [e["action"] for e in database.list_audit()]


def test_without_batch_runs_the_entered_path_itself(isolated):
    results = runner.run_script(MockGroovyConsoleClient(), _script("page-report"), {"rootPath": ["/content"]}, session_id="S")
    assert [r.label for r in results] == ["/content"]


def test_discovery_failure_is_reported_as_a_row(isolated):
    class Broken(MockGroovyConsoleClient):
        def run_script(self, script):
            raise RuntimeError("AEM returned HTTP 500: boom")
    results = runner.run_script(Broken(), _script("assets-by-type"), {"rootPath": ["/content/dam"]}, session_id="S",
                                batch={"excludes": [], "levels": 1})
    assert [r.label for r in results] == ["/content/dam (discovery)"] and "boom" in results[0].error


def test_combine_concatenates_rows_into_one_file(isolated):
    script = _script("page-report")
    results = runner.run_script(MockGroovyConsoleClient(), script, {"rootPath": ["/content/a", "/content/b"]},
                                session_id="S")
    out = runner.format_combined(results, "generic-excel", "S")
    rows = openpyxl.load_workbook(out).active.max_row - 1
    assert rows == sum(r.row_count for r in results)
    assert "format.combined" in [e["action"] for e in database.list_audit()]
    with pytest.raises(ValueError, match="at least two"):
        runner.format_combined(results[:1], "generic-excel", "S")
    Path(results[0].json_path).write_text("[]")
    with pytest.raises(ValueError, match="changed on disk"):
        runner.format_combined(results, "generic-excel", "S")


def test_excel_row_limit_gives_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "EXCEL_MAX_DATA_ROWS", 3)
    with pytest.raises(ValueError, match="more than one Excel sheet can hold"):
        base.write_styled_excel(pd.DataFrame({"a": range(4)}), tmp_path / "x.xlsx", "S")


def test_ui_batch_run_and_combine(isolated):
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.selectbox(key="script_id").set_value("assets-by-type").run()
    at.text_area(key="in:assets-by-type:rootPath").set_value("/content/dam").run()
    at.checkbox(key="batch_on:assets-by-type").check().run()
    assert "/content/dam/projects" in at.text_area(key="batch_excl:assets-by-type").value
    button = [b for b in at.button if b.label.startswith("▶️")][0]
    assert button.label == "▶️ Discover & run (1 path)"
    button.click().run()
    for _ in range(40):
        if "run_job" not in at.session_state:
            break
        time.sleep(0.25)
        at.run()
    table = at.dataframe[0].value
    assert sorted(table["Input"]) == ["/content/dam/acme", "/content/dam/acme-campaigns", "/content/dam/acme-legal"]
    assert any("Discovered 3 root(s) under `/content/dam`" in i.value and "directly in" in i.value for i in at.info)
    at.checkbox(key="format_combine").check().run()
    [b for b in at.button if b.label.startswith("Combine 3 results")][0].click().run()
    assert any("Combined 3 results into combined_assets-by-type_3-results_" in s.value for s in at.success)
    assert any(b.label.startswith("⬇️ combined_assets-by-type") for b in at.get("download_button"))
