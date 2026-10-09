from __future__ import annotations

"""Shared HTTP plumbing for AEM author calls: auth attachment, CSRF token,
and uniform error normalization (messages errors.friendly_error() keys off)."""

from typing import Any

import httpx

from ..auth import auth_headers
from ..settings_store import Environment
from ..user_token import UserToken


class BaseAemClient:
    """Bound to one AEM environment for its whole life, so a batch keeps
    talking to the server it started on even if you switch in the sidebar."""

    def __init__(self, user_token: UserToken | None = None, environment: Environment | None = None) -> None:
        self._user_token = user_token
        self.environment = environment

    @property
    def base_url(self) -> str:
        if self.environment is None or not self.environment.url:
            raise RuntimeError("AEM is not configured — add an environment (name + author URL) on the Settings page.")
        return self.environment.url

    def _new_http_client(self, timeout: float | None = None) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            headers=auth_headers(self._user_token),
            timeout=httpx.Timeout(timeout or 60.0, connect=30.0),
            trust_env=True,
            follow_redirects=False,
        )

    def _request(self, http: httpx.Client, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{self.base_url}{path}"
        try:
            response = http.request(method, path, **kwargs)
        except httpx.ConnectError as exc:
            raise RuntimeError(f"Cannot connect to AEM. Check AEM author URL/VPN/proxy. Endpoint: {url}") from exc
        except httpx.TimeoutException as exc:
            raise RuntimeError(f"AEM request timed out. Endpoint: {url}") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"AEM request failed: {exc}. Endpoint: {url}") from exc
        if response.is_redirect:
            # A redirect here is almost always AEM bouncing an unauthenticated
            # request to its login page.
            raise RuntimeError(f"AEM returned HTTP 401: redirected to {response.headers.get('location', '?')} (not authenticated)")
        if response.status_code >= 400:
            raise RuntimeError(f"AEM returned HTTP {response.status_code}: {response.text[:500]}. Endpoint: {url}")
        return response

    def csrf_token(self, http: httpx.Client) -> str:
        """Granite's CSRF filter can reject authenticated POSTs without a
        CSRF-Token header. Best effort: an empty token if the endpoint isn't there."""
        try:
            return str(self._request(http, "GET", "/libs/granite/csrf/token.json").json().get("token", ""))
        except RuntimeError as exc:
            if "http 404" in str(exc).lower():
                return ""
            raise

    def current_user(self) -> str:
        with self._new_http_client() as http:
            data = self._request(http, "GET", "/libs/granite/security/currentuser.json").json()
        user = str(data.get("authorizableId") or data.get("userID") or "")
        if not user or user == "anonymous":
            raise RuntimeError("AEM returned HTTP 401: request was treated as anonymous")
        return user
