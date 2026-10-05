from __future__ import annotations

"""Signing in as yourself: the AEM Local Development Token.

Developer Console → Integrations → Local token → "Get Local Development
Token" issues an Adobe IMS access token for whoever is signed in to
Developer Console, i.e. your own SSO identity. AEM treats requests carrying it
as you: your permissions, your Groovy Console access, your name in AEM's
logs. It's valid for 24 hours.

The token lives only in this browser session's memory. It is never written
to disk, the database, or the logs; audit events identify it by a short
SHA-256 fingerprint.

The token is a JWT; its payload (user id, client id, issue time, lifetime)
is decoded *without* signature verification, only to show who it belongs to
and when it expires. AEM does the real verification on every request.
"""

import base64
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class UserToken:
    access_token: str
    user_id: str = ""
    client_id: str = ""
    expires_at: float | None = None  # epoch seconds, None when unknown

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.access_token.encode()).hexdigest()[:12]

    def seconds_left(self, now: float | None = None) -> float | None:
        return None if self.expires_at is None else self.expires_at - (now or time.time())

    def is_expired(self, now: float | None = None) -> bool:
        left = self.seconds_left(now)
        return left is not None and left <= 0

    @property
    def expires_label(self) -> str:
        if self.expires_at is None:
            return "unknown expiry"
        return datetime.fromtimestamp(self.expires_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse(raw: str) -> UserToken:
    """Accepts what Developer Console gives you: the full JSON
    (`{"ok": true, "accessToken": "…"}`), a bare token, or "Bearer …"."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("Paste the Local Development Token (or the JSON Developer Console shows).")
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"That looks like JSON but isn't valid: {exc}") from exc
        text = str(data.get("accessToken") or data.get("access_token") or "").strip()
        if not text:
            raise ValueError("The JSON has no accessToken field — copy the whole Local Development Token JSON.")
    if text.lower().startswith("bearer "):
        text = text[7:].strip()
    if any(c.isspace() for c in text):
        raise ValueError("The token contains spaces or line breaks — copy it again without wrapping.")

    payload = _jwt_payload(text)
    if not payload:
        # Not a JWT we can read: still usable, just without identity/expiry hints.
        return UserToken(access_token=text)
    # IMS tokens carry created_at / expires_in in milliseconds; standard JWT `exp` is seconds.
    created, lifetime = _number(payload.get("created_at")), _number(payload.get("expires_in"))
    if created is not None and lifetime is not None:
        expires_at: float | None = (created + lifetime) / 1000.0
    else:
        expires_at = _number(payload.get("exp"))
    return UserToken(
        access_token=text,
        user_id=str(payload.get("user_id") or payload.get("sub") or ""),
        client_id=str(payload.get("client_id") or ""),
        expires_at=expires_at,
    )
