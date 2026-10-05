from __future__ import annotations

"""AEM authentication: your own SSO identity only.

Every request carries the Local Development Token you sign in with from the
sidebar (see user_token.py), so AEM sees *you*: your permissions, your Groovy
Console access, your name in AEM's own logs and in this app's audit trail.
There is deliberately no technical account, basic auth, or token in .env.
The token is held in the browser session's memory, and in the OS keychain
only if you ticked "Remember" (token_store.py).
"""

from .user_token import UserToken


def auth_headers(user_token: UserToken | None) -> dict[str, str]:
    if user_token is None:
        raise RuntimeError("AEM is not configured — sign in with your Local Development Token in the sidebar.")
    if user_token.is_expired():
        raise RuntimeError(f"Your Local Development Token expired at {user_token.expires_label} — "
                           "get a new one from Developer Console and sign in again in the sidebar.")
    return {"Authorization": f"Bearer {user_token.access_token}"}


def is_signed_in(user_token: UserToken | None) -> bool:
    return user_token is not None and not user_token.is_expired()
