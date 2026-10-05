"""Refresh/restart behaviour: the sign-in box only appears when there's no
token, it expired, or AEM rejects it."""
from __future__ import annotations

import base64
import json
import time

import keyring
import pytest
from streamlit.testing.v1 import AppTest

from groovy_runner import database, settings_store
from groovy_runner.clients.groovy_console import GroovyConsoleClient
from groovy_runner.config import settings
from test_token_store import MemoryKeyring

AUTHOR = "https://author-p9-e9.adobeaemcloud.com"
APP = str(__import__("pathlib").Path(__file__).resolve().parent.parent / "app.py")


def _token(offset=0.0):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{enc({'a': 1})}.{enc({'user_id': 'U', 'created_at': str(int((time.time() + offset) * 1000)), 'expires_in': '86400000'})}.s"


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    previous = keyring.get_keyring()
    kr = MemoryKeyring()
    keyring.set_keyring(kr)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(settings, "output_dir", str(tmp_path / "out"))
    settings_store._cache = None
    database.initialize()
    settings_store.save({"mock_mode": False, "aem_author_url": AUTHOR})
    yield kr
    keyring.set_keyring(previous)
    settings_store._cache = None


def _signed_in(at) -> bool:
    return any("Signed in as" in m.value for m in at.sidebar.markdown)


def _sign_in(monkeypatch, remember=True):
    monkeypatch.setattr(GroovyConsoleClient, "current_user", lambda self: "me@company.com")
    at = AppTest.from_file(APP, default_timeout=60).run()
    if not remember:
        at.sidebar.checkbox(key="_remember_sidebar").uncheck()
    at.sidebar.text_input(key="_token_input_sidebar").set_value(_token())
    at.sidebar.button(key="sign_in_sidebar").click().run()
    assert _signed_in(at)
    return at


def test_refresh_keeps_you_signed_in(app_env, monkeypatch):
    _sign_in(monkeypatch)
    refreshed = AppTest.from_file(APP, default_timeout=60).run()  # new browser session
    assert _signed_in(refreshed) and not refreshed.sidebar.text_input


def test_without_remember_refresh_asks_again(app_env, monkeypatch):
    _sign_in(monkeypatch, remember=False)
    assert not _signed_in(AppTest.from_file(APP, default_timeout=60).run())


def test_rejected_saved_token_is_forgotten_with_notice(app_env, monkeypatch):
    _sign_in(monkeypatch)

    def reject(self):
        raise RuntimeError("AEM returned HTTP 401: invalid token")
    monkeypatch.setattr(GroovyConsoleClient, "current_user", reject)
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not _signed_in(at) and app_env.store == {}
    assert any("no longer valid" in w.value for w in at.sidebar.warning)


def test_unreachable_aem_keeps_saved_sign_in(app_env, monkeypatch):
    _sign_in(monkeypatch)

    def offline(self):
        raise RuntimeError("Cannot connect to AEM. Check AEM author URL/VPN/proxy.")
    monkeypatch.setattr(GroovyConsoleClient, "current_user", offline)
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert _signed_in(at) and app_env.store


def test_token_rejected_during_run_signs_out(app_env, monkeypatch):
    at = _sign_in(monkeypatch)

    def reject_run(self, script):
        raise RuntimeError("AEM returned HTTP 401: token revoked")
    monkeypatch.setattr(GroovyConsoleClient, "run_script", reject_run)
    at.text_area(key="in:asset-reference-report:parentDamPath").set_value("/content/dam/x").run()
    [b for b in at.button if b.label.startswith("▶️")][0].click().run()
    for _ in range(40):  # runs happen in the background now; poll until the page finalizes the batch
        if "run_job" not in at.session_state:
            break
        time.sleep(0.25)
        at.run()
    assert not _signed_in(at) and app_env.store == {}
    assert any("rejected your token" in w.value for w in at.sidebar.warning)


def test_remembered_sign_in_not_restored_for_remote_browsers(app_env, monkeypatch):
    _sign_in(monkeypatch)
    from groovy_runner.ui import shared
    monkeypatch.setattr(shared, "is_local_client", lambda: False)
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not _signed_in(at) and app_env.store  # kept for the local user, not handed to a remote one
    assert at.sidebar.checkbox(key="_remember_sidebar").disabled


def test_changing_author_url_signs_out(app_env, monkeypatch):
    at = _sign_in(monkeypatch)
    at.sidebar.radio[0].set_value("Settings").run()
    url_input = [t for t in at.text_input if t.label.startswith("AEM author URL")][0]
    url_input.set_value("https://author-p8-e8.adobeaemcloud.com")
    [b for b in at.button if b.label == "Save settings"][0].click().run()
    assert not _signed_in(at)
    assert any("author URL changed" in w.value for w in at.sidebar.warning)
