from __future__ import annotations

"""AEM Groovy Console HTTP client.

The console UI itself posts to `/bin/groovyconsole/post.json` with a `script`
form field; the response is JSON with `output` (captured println), `result`
(return value), `exceptionStackTrace`, and `runningTime`. This client sends
the same request, authenticated as you (your Local Development Token).

Every script passes through readonly.prepare() here, at the last point
before it leaves the app: a script that fails the read-only check is never
sent, and the runtime guard is always injected.
"""

from dataclasses import dataclass

from .. import readonly, settings_store
from ..errors import GroovyScriptError
from .base import BaseAemClient


@dataclass
class GroovyResult:
    output: str
    result: str
    exception: str
    running_time: str


class GroovyConsoleClient(BaseAemClient):
    def run_script(self, script: str) -> GroovyResult:
        script = readonly.prepare(script)
        timeout = float(settings_store.get("http_timeout"))
        endpoint = settings_store.get("groovy_console_endpoint")
        with self._new_http_client(timeout=timeout) as http:
            headers = {"Accept": "application/json"}
            token = self.csrf_token(http)
            if token:
                headers["CSRF-Token"] = token
            response = self._request(http, "POST", endpoint, data={"script": script}, headers=headers)
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError(f"Groovy output did not contain result JSON. Non-JSON console response: {response.text[:300]}") from exc
        result = GroovyResult(
            output=str(body.get("output") or ""),
            result=str(body.get("result") or ""),
            exception=str(body.get("exceptionStackTrace") or ""),
            running_time=str(body.get("runningTime") or ""),
        )
        if result.exception:
            raise GroovyScriptError(result.exception)
        return result

    def identity(self) -> str:
        """The AEM user this client acts as — recorded on every run for traceability."""
        return self.current_user()

    def test_connection(self) -> tuple[str, str]:
        """(AEM user id, console running time) — proves auth and console access."""
        user = self.current_user()
        result = self.run_script('println "groovy-runner-ping"')
        if "groovy-runner-ping" not in result.output:
            raise RuntimeError("Groovy output did not contain result JSON. Ping script produced no output.")
        return user, result.running_time
