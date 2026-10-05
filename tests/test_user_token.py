from __future__ import annotations

import base64
import json
import time

import pytest

from groovy_runner import auth
from groovy_runner.errors import friendly_error
from groovy_runner.user_token import parse


def _jwt(payload: dict) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{enc({'alg': 'RS256'})}.{enc(payload)}.c2ln"


def _ims_token(created_s: float, lifetime_s: float) -> str:
    # IMS writes created_at / expires_in as millisecond strings.
    return _jwt({"user_id": "ABC@AdobeID", "client_id": "dev-console-prod", "type": "access_token",
                 "created_at": str(int(created_s * 1000)), "expires_in": str(int(lifetime_s * 1000))})


def test_parses_developer_console_json_bearer_and_raw():
    raw = _ims_token(time.time(), 86400)
    for pasted in (json.dumps({"ok": True, "statusCode": 200, "accessToken": raw}, indent=2), f"Bearer {raw}", f"  {raw}\n"):
        token = parse(pasted)
        assert token.access_token == raw
        assert token.user_id == "ABC@AdobeID" and token.client_id == "dev-console-prod"
        assert 86000 < token.seconds_left() <= 86400 and not token.is_expired()
    assert len(parse(raw).fingerprint) == 12 and parse(raw).fingerprint not in raw


def test_expired_and_unreadable_tokens():
    assert parse(_ims_token(time.time() - 90000, 86400)).is_expired()
    opaque = parse("not-a-jwt-token")
    assert opaque.expires_at is None and not opaque.is_expired()
    for bad in ("", '{"ok": true}', "{broken", "abc def"):
        with pytest.raises(ValueError):
            parse(bad)


def test_auth_uses_only_the_signed_in_user_token():
    good = parse(_ims_token(time.time(), 3600))
    assert auth.auth_headers(good) == {"Authorization": f"Bearer {good.access_token}"}
    assert auth.is_signed_in(good) and not auth.is_signed_in(None)
    with pytest.raises(RuntimeError, match="sign in"):
        auth.auth_headers(None)
    expired = parse(_ims_token(time.time() - 7200, 3600))
    assert not auth.is_signed_in(expired)
    with pytest.raises(RuntimeError) as err:
        auth.auth_headers(expired)
    assert friendly_error(err.value).title == "Your sign-in has expired"
