from __future__ import annotations

from groovy_runner.errors import GroovyScriptError, friendly_error


def test_classification():
    assert friendly_error(RuntimeError("AEM returned HTTP 401: x")).retryable is False
    assert "Groovy Console" in friendly_error(RuntimeError("AEM returned HTTP 403: x")).reasons[0]
    assert friendly_error(RuntimeError("AEM request timed out. Endpoint: y")).retryable is True
    assert friendly_error(RuntimeError("Parent DAM path not found: /x")).title == "Path not found in AEM"
    assert "marker" in friendly_error(RuntimeError("Groovy output did not contain result JSON. x")).reasons[0]
    assert friendly_error(GroovyScriptError("NPE")).retryable is False
    assert friendly_error(RuntimeError("Your Local Development Token expired at x")).retryable is False
