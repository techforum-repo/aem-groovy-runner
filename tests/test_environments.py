"""Several AEM environments: pick one in the sidebar, a separate sign-in each,
the Run button names it, and runs stay on the environment they started on."""
from __future__ import annotations

import time

import keyring
import pytest
from streamlit.testing.v1 import AppTest

from groovy_runner import database, settings_store
from groovy_runner.clients.groovy_console import GroovyConsoleClient
from groovy_runner.config import settings
from test_sign_in_restore import APP, _token
from test_token_store import MemoryKeyring

DEV = settings_store.Environment("DEV", "https://author-p1-e1.adobeaemcloud.com")
QA = settings_store.Environment("QA", "https://author-p1-e2.adobeaemcloud.com")


@pytest.fixture()
def two_envs(tmp_path, monkeypatch):
    previous = keyring.get_keyring()
    kr = MemoryKeyring()
    keyring.set_keyring(kr)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(settings, "output_dir", str(tmp_path / "out"))
    settings_store._cache = None
    database.initialize()
    settings_store.save({"mock_mode": False})
    settings_store.save_environments([DEV, QA])
    # Each environment answers with its own user, so a token sent to the wrong one would show.
    monkeypatch.setattr(GroovyConsoleClient, "current_user",
                        lambda self: {DEV.url: "dev.user", QA.url: "qa.user"}[self.base_url])
    yield kr
    keyring.set_keyring(previous)
    settings_store._cache = None


def _status(at) -> str:
    return " ".join(m.value for m in at.sidebar.markdown)


def _sign_in(at):
    at.sidebar.text_input(key="_token_input_sidebar").set_value(_token())
    at.sidebar.button(key="sign_in_sidebar").click().run()


def _run_button(at):
    return [b for b in at.button if b.label.startswith("▶️")][0]


def test_environment_list_validation_and_migration(two_envs):
    envs, problems = settings_store.validate_environments([
        {"name": "DEV", "url": "https://a.example.com/"}, {"name": "dev", "url": "https://b.example.com"},
        {"name": "", "url": "https://c.example.com"}, {"name": "X", "url": "author.example.com"}, {"name": "", "url": ""}])
    assert envs == [settings_store.Environment("DEV", "https://a.example.com")]  # trailing slash dropped
    assert len(problems) == 3  # duplicate name (any case), missing name, not a URL; the blank row is ignored

    database.clear_setting_overrides([settings_store.ENVIRONMENTS_KEY])
    database.set_setting_overrides({settings_store.LEGACY_URL_KEY: "https://old.example.com/"})
    assert settings_store.environments() == [settings_store.Environment("Default", "https://old.example.com")]


def test_each_environment_has_its_own_sign_in(two_envs):
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert at.sidebar.selectbox(key="active_env").value == "DEV"  # the first one by default
    _sign_in(at)
    assert "Signed in as **dev.user**" in _status(at) and "🟢 Signed in" in _status(at)
    assert _run_button(at).label.startswith("▶️ Run on DEV")

    at.sidebar.selectbox(key="active_env").set_value("QA").run()
    assert "⚪ Not signed in" in _status(at) and at.sidebar.text_input(key="_token_input_sidebar")
    assert _run_button(at).label.startswith("▶️ Run on QA") and _run_button(at).disabled
    _sign_in(at)
    assert "Signed in as **qa.user**" in _status(at)

    at.sidebar.selectbox(key="active_env").set_value("DEV").run()
    assert "Signed in as **dev.user**" in _status(at)  # DEV's sign-in was kept, not replaced by QA's
    hosts = {account.split("#")[0] for (_, account) in two_envs.store}
    assert hosts == {DEV.host, QA.host}  # remembered separately in the keychain


def test_refresh_restores_each_environment_and_preselects_the_last_one(two_envs):
    at = AppTest.from_file(APP, default_timeout=60).run()
    _sign_in(at)
    at.sidebar.selectbox(key="active_env").set_value("QA").run()
    _sign_in(at)
    refreshed = AppTest.from_file(APP, default_timeout=60).run()  # new browser session
    assert refreshed.sidebar.selectbox(key="active_env").value == "QA"
    assert "Signed in as **qa.user**" in _status(refreshed)
    refreshed.sidebar.selectbox(key="active_env").set_value("DEV").run()
    assert "Signed in as **dev.user**" in _status(refreshed)


def test_signing_out_of_one_environment_keeps_the_other(two_envs):
    at = AppTest.from_file(APP, default_timeout=60).run()
    _sign_in(at)
    at.sidebar.selectbox(key="active_env").set_value("QA").run()
    _sign_in(at)
    at.sidebar.button(key="sign_out_sidebar").click().run()
    assert "⚪ Not signed in" in _status(at)
    at.sidebar.selectbox(key="active_env").set_value("DEV").run()
    assert "Signed in as **dev.user**" in _status(at)
    assert {account.split("#")[0] for (_, account) in two_envs.store} == {DEV.host}


def test_runs_go_to_the_selected_environment_and_retry_stays_there(two_envs, monkeypatch):
    sent_to = []

    def fail(self, script):
        sent_to.append(self.base_url)
        raise RuntimeError("AEM returned HTTP 503: busy")
    monkeypatch.setattr(GroovyConsoleClient, "run_script", fail)
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.selectbox(key="active_env").set_value("QA").run()
    _sign_in(at)
    at.text_area(key="in:asset-reference-report:parentDamPath").set_value("/content/dam/x").run()
    _run_button(at).click().run()
    for _ in range(40):
        if "run_job" not in at.session_state:
            break
        time.sleep(0.25)
        at.run()
    assert sent_to == [QA.url]
    run = database.list_runs()[0]
    assert run["aem_host"] == QA.host
    started = [e for e in database.list_audit() if e["action"] == "run.batch_started"][0]
    assert '"environment": "QA"' in started["details_json"]
    retry = [b for b in at.button if b.label.startswith("Retry")][0]
    assert not retry.disabled

    at.sidebar.selectbox(key="active_env").set_value("DEV").run()
    _sign_in(at)
    retry = [b for b in at.button if b.label.startswith("Retry")][0]
    assert retry.disabled and retry.label.endswith("on QA")  # never retried on a different server
    assert any("switch to it in the sidebar" in c.value for c in at.caption)
