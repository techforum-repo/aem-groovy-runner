from __future__ import annotations

import base64
import json
import time

import keyring
import pytest
from keyring.backend import KeyringBackend

from groovy_runner import token_store
from groovy_runner.user_token import parse


class MemoryKeyring(KeyringBackend):
    """Stands in for the OS keychain so tests never touch the real one."""
    priority = 1

    def __init__(self):
        super().__init__()
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        if self.store.pop((service, username), None) is None:
            raise keyring.errors.PasswordDeleteError(username)


@pytest.fixture()
def memory_keyring():
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)


def _token(offset: float = 0) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{enc({'alg': 'x'})}.{enc({'user_id': 'U', 'created_at': str(int((time.time() + offset) * 1000)), 'expires_in': '86400000'})}.s"


A = "https://author-p1-e1.adobeaemcloud.com"
B = "https://author-p1-e2.adobeaemcloud.com"


def test_round_trip_per_author_host(memory_keyring):
    token_store.save(A, parse(_token()), "me@company.com")
    token, user = token_store.load(A)
    assert user == "me@company.com" and not token.is_expired()
    assert token_store.load(B) is None
    assert {k[1].split("#")[0] for k in memory_keyring.store} == {"author-p1-e1.adobeaemcloud.com"}


def test_expired_and_corrupt_entries_are_deleted(memory_keyring):
    token_store.save(A, parse(_token(-90000)), "me")
    assert token_store.load(A) is None and memory_keyring.store == {}
    memory_keyring.set_password("aem-groovy-runner", "author-p1-e1.adobeaemcloud.com", "not json")
    assert token_store.load(A) is None and memory_keyring.store == {}


def test_delete(memory_keyring):
    token_store.save(A, parse(_token()), "me")
    assert token_store.delete(A) is True
    assert token_store.delete(A) is False


def test_refuses_insecure_backends():
    previous = keyring.get_keyring()
    try:
        from keyring.backends import fail
        keyring.set_keyring(fail.Keyring())
        ok, reason = token_store.availability()
        assert not ok and ("credential store" in reason or "insecure" in reason)
        with pytest.raises(RuntimeError, match="Can't remember"):
            token_store.save(A, parse(_token()), "me")
        assert token_store.load(A) is None
    finally:
        keyring.set_keyring(previous)


class WindowsLikeKeyring(MemoryKeyring):
    """Emulates Windows Credential Manager: CredWrite rejects a secret over
    2,560 bytes (stored as UTF-16) with error 1783."""

    def __init__(self, fail_on_write: int | None = None):
        super().__init__()
        self.writes, self.fail_on_write = 0, fail_on_write

    def set_password(self, service, username, password):
        self.writes += 1
        if self.fail_on_write == self.writes:
            raise OSError(1783, "CredWrite", "The stub received bad data.")
        if len(password.encode("utf-16-le")) > 2560:
            raise OSError(1783, "CredWrite", "The stub received bad data.")
        super().set_password(service, username, password)


def _long_token() -> str:
    """Real IMS tokens run 1,100-1,600+ characters, over Windows' ~1,280-char cap."""
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    payload = {"user_id": "U@AdobeID", "created_at": str(int(time.time() * 1000)), "expires_in": "86400000",
               "as": "ims-na1", "scope": ",".join(f"scope_{i}" for i in range(120))}
    return f"{enc({'alg': 'RS256', 'x5u': 'ims_na1-key-at-1.cer'})}.{enc(payload)}.{'s' * 342}"


@pytest.fixture()
def windows_keyring():
    previous = keyring.get_keyring()
    backend = WindowsLikeKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)


def test_long_token_fits_windows_credential_manager(windows_keyring):
    raw = _long_token()
    assert len(json.dumps({"token": raw, "aem_user": "me"}).encode("utf-16-le")) > 2560  # would have failed before
    token_store.save(A, parse(raw), "me@company.com")
    restored, user = token_store.load(A)
    assert restored.access_token == raw and user == "me@company.com"
    assert all(len(v.encode("utf-16-le")) <= 2560 for v in windows_keyring.store.values())
    assert token_store.delete(A) and windows_keyring.store == {}


def test_failed_write_leaves_nothing_behind():
    previous = keyring.get_keyring()
    backend = WindowsLikeKeyring(fail_on_write=2)
    keyring.set_keyring(backend)
    try:
        with pytest.raises(RuntimeError, match="keychain refused"):
            token_store.save(A, parse(_long_token()), "me")
        assert backend.store == {} and token_store.load(A) is None
    finally:
        keyring.set_keyring(previous)


def test_shorter_value_replaces_longer_without_stale_chunks(windows_keyring):
    short = _token()
    token_store.save(A, parse(_long_token()), "me")
    token_store.save(A, parse(short), "me")
    assert token_store.load(A)[0].access_token == short
    assert len([k for k in windows_keyring.store if k[1].startswith("author-p1-e1")]) == 2  # head + 1 chunk


def test_value_saved_before_chunking_still_loads(memory_keyring):
    memory_keyring.set_password("aem-groovy-runner", "author-p1-e1.adobeaemcloud.com",
                                json.dumps({"token": _token(), "aem_user": "old"}))
    assert token_store.load(A)[1] == "old"
    assert token_store.delete(A) and memory_keyring.store == {}


def test_missing_chunk_is_treated_as_corrupt(memory_keyring):
    token_store.save(A, parse(_long_token()), "me")
    del memory_keyring.store[("aem-groovy-runner", "author-p1-e1.adobeaemcloud.com#1")]
    assert token_store.load(A) is None and memory_keyring.store == {}
