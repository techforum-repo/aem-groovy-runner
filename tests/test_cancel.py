"""Cancelling a batch: skipped runs are never sent; the run in flight is
abandoned promptly (AEM can't be told to stop, so its result is discarded)."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from groovy_runner import database, jobs, runner, settings_store
from groovy_runner.clients.mock import MockGroovyConsoleClient
from groovy_runner.config import settings
from groovy_runner.scripts_registry import discover

APP = str(Path(__file__).resolve().parent.parent / "app.py")
PATHS = ["/content/dam/a", "/content/dam/b", "/content/dam/c"]


class SlowClient(MockGroovyConsoleClient):
    def __init__(self, seconds: float):
        super().__init__()
        self.seconds, self.sent = seconds, []

    def run_script(self, script):
        self.sent.append(script)
        time.sleep(self.seconds)
        return super().run_script(script)


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(settings, "output_dir", str(tmp_path / "out"))
    settings_store._cache = None
    database.initialize()
    yield tmp_path
    settings_store._cache = None


def _script():
    return {s.id: s for s in discover()}["asset-reference-report"]


def test_cancel_before_start_sends_nothing(isolated):
    client, cancel = SlowClient(0), threading.Event()
    cancel.set()
    results = runner.run_script(client, _script(), {"parentDamPath": PATHS}, session_id="S", cancel=cancel)
    assert [r.status for r in results] == ["cancelled"] * 3 and client.sent == []
    assert {r["status"] for r in database.list_runs()} == {"cancelled"}
    assert [e["action"] for e in database.list_audit()].count("run.cancelled") == 3


def test_cancel_abandons_in_flight_run_promptly(isolated):
    client, cancel = SlowClient(5), threading.Event()
    threading.Timer(0.5, cancel.set).start()
    started = time.monotonic()
    results = runner.run_script(client, _script(), {"parentDamPath": PATHS}, session_id="S", cancel=cancel)
    assert time.monotonic() - started < 2  # didn't wait out the 5s request
    assert [r.status for r in results] == ["cancelled"] * 3
    assert "waiting for AEM" in results[0].error and "before it started" in results[1].error
    assert len(client.sent) == 1  # only the in-flight one ever left the app


def test_job_finishes_and_formats_in_background(isolated):
    job = jobs.start(SlowClient(0), _script(), {"parentDamPath": PATHS[:2]}, session_id="S",
                     formatter_id="asset-reference-excel", total=2)
    job.thread.join(10)
    assert job.done and job.error is None and job.finished == 2
    assert all(r.ok and "asset-reference-excel" in r.outputs for r in job.results)


def test_ui_cancel_mid_batch(isolated, monkeypatch):
    monkeypatch.setattr(MockGroovyConsoleClient, "run_script",
                        lambda self, script, _orig=MockGroovyConsoleClient.run_script: (time.sleep(1.5), _orig(self, script))[1])
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.text_area(key="in:asset-reference-report:parentDamPath").set_value("\n".join(PATHS)).run()
    [b for b in at.button if b.label.startswith("▶️")][0].click().run()
    assert at.button(key="cancel_run") and [b for b in at.button if b.label.startswith("▶️")][0].disabled
    time.sleep(2)  # first run finishes, second is in flight
    at.button(key="cancel_run").click().run()
    for _ in range(40):
        if at.session_state["run_job"] if "run_job" in at.session_state else None:
            time.sleep(0.25)
            at.run()
        else:
            break
    statuses = at.dataframe[0].value["Status"].tolist()
    assert statuses.count("⏹ Cancelled") == 2 and statuses.count("✅") == 1
    assert any("Cancelled: 1 of 3" in i.value for i in at.info)
    assert any(b.label == "Retry 2 failed/cancelled run(s)" for b in at.button)
    actions = [e["action"] for e in database.list_audit()]
    assert "run.batch_cancel_requested" in actions and actions.count("run.cancelled") == 2
