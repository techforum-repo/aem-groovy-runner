from __future__ import annotations

"""Opt-in "remember my sign-in until it expires".

When the user ticks Remember on sign-in, the Local Development Token is put
in the OS credential store via `keyring` (GNOME Keyring / Secret Service,
macOS Keychain, Windows Credential Manager), never in a plain file, .env, or
the database. New browser sessions and app restarts then reuse it until it
expires; it's deleted on sign-out or once found expired.

If no secure backend is available (e.g. a headless Linux box without a
Secret Service), remembering is simply unavailable: there is deliberately
no plaintext fallback.

Entries are keyed by AEM author host, so tokens for different environments
don't overwrite each other.
"""

import json
from urllib.parse import urlparse

from .logging_setup import get_logger
from .user_token import UserToken, parse

SERVICE = "aem-groovy-runner"

# keyring backends that keep secrets in plain files or nowhere at all.
_INSECURE_BACKENDS = {"PlaintextKeyring", "EncryptedKeyring", "Keyring", "ChainerBackend"}
_INSECURE_MODULES = ("keyrings.alt", "keyring.backends.fail", "keyring.backends.null")


def _account(author_url: str) -> str:
    return urlparse(author_url).netloc or author_url


def _keyring():
    try:
        import keyring
    except ImportError:
        return None
    return keyring


def availability() -> tuple[bool, str]:
    """(usable, backend name or reason it isn't)."""
    kr = _keyring()
    if kr is None:
        return False, "the keyring package isn't installed (run start-unix.sh / pip install -r requirements.txt)"
    try:
        backend = kr.get_keyring()
    except Exception as exc:  # pragma: no cover
        return False, f"no OS credential store ({exc})"
    module, name = type(backend).__module__, type(backend).__name__
    if module.startswith(_INSECURE_MODULES) or (name in _INSECURE_BACKENDS and module.startswith("keyrings")):
        return False, f"only an insecure keyring backend is available ({module}.{name})"
    if module == "keyring.backends.chainer":
        # Chainer is fine as long as at least one real backend is behind it.
        if not getattr(backend, "backends", None):
            return False, "no OS credential store available"
    return True, f"{module}.{name}"


# Windows Credential Manager caps a secret at 2,560 bytes (CRED_MAX_CREDENTIAL_BLOB_SIZE)
# and keyring stores it as UTF-16, so ~1,280 characters. IMS tokens alone can
# exceed that (CredWrite then fails with error 1783, "The stub received bad
# data"). Values are therefore split into chunks stored as "<account>#<n>",
# with the main entry holding just "chunks:<count>". 1,000 characters keeps
# each chunk well under the cap on every backend.
CHUNK_CHARS = 1000
_CHUNK_PREFIX = "chunks:"
_MAX_CHUNKS = 50


def _write(account: str, value: str) -> None:
    kr = _keyring()
    _delete_all(account)  # no stale chunks from a longer previous value
    parts = [value[i:i + CHUNK_CHARS] for i in range(0, len(value), CHUNK_CHARS)] or [""]
    try:
        for n, part in enumerate(parts):
            kr.set_password(SERVICE, f"{account}#{n}", part)
        kr.set_password(SERVICE, account, f"{_CHUNK_PREFIX}{len(parts)}")  # written last: marks it complete
    except Exception:
        _delete_all(account)  # never leave a half-written sign-in behind
        raise


def _read(account: str) -> str | None:
    kr = _keyring()
    head = kr.get_password(SERVICE, account)
    if head is None or not head.startswith(_CHUNK_PREFIX):
        return head  # nothing stored, or a value saved before chunking existed
    count = int(head[len(_CHUNK_PREFIX):])
    if not 0 < count <= _MAX_CHUNKS:
        raise ValueError("bad chunk count")
    parts = [kr.get_password(SERVICE, f"{account}#{n}") for n in range(count)]
    if any(p is None for p in parts):
        raise ValueError("missing chunk")
    return "".join(parts)


def _delete_all(account: str) -> bool:
    kr = _keyring()
    removed = False
    # Scan every possible chunk slot rather than stopping at the first gap, so a
    # partially damaged entry is still removed completely.
    for name in [account, *(f"{account}#{n}" for n in range(_MAX_CHUNKS))]:
        try:
            kr.delete_password(SERVICE, name)
            removed = True
        except Exception:
            pass  # slot empty
    return removed


def save(author_url: str, token: UserToken, aem_user: str) -> None:
    ok, reason = availability()
    if not ok:
        raise RuntimeError(f"Can't remember the sign-in: {reason}")
    try:
        _write(_account(author_url), json.dumps({"token": token.access_token, "aem_user": aem_user}))
    except Exception as exc:
        raise RuntimeError(f"Can't remember the sign-in: the OS keychain refused it ({exc})") from exc


def load(author_url: str) -> tuple[UserToken, str] | None:
    """The remembered (token, aem_user) for this author, or None. Expired or
    unreadable entries are deleted."""
    if not author_url or not availability()[0]:
        return None
    try:
        raw = _read(_account(author_url))
    except ValueError:
        delete(author_url)
        return None
    except Exception as exc:
        get_logger().warning("Keyring read failed: %s", exc)
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
        token = parse(data["token"])
    except (ValueError, KeyError, TypeError):
        delete(author_url)
        return None
    if token.is_expired():
        delete(author_url)
        return None
    return token, str(data.get("aem_user") or "")


def delete(author_url: str) -> bool:
    if _keyring() is None or not author_url:
        return False
    try:
        return _delete_all(_account(author_url))
    except Exception:
        return False
