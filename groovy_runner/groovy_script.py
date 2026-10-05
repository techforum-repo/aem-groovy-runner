from __future__ import annotations

"""Injecting inputs into a Groovy script and reading its result back.

Inputs are injected as one base64-encoded JSON blob replacing the
`__CONFIG_B64__` placeholder, rather than string-formatted into Groovy
source, so a value containing spaces, quotes, `$` or backslashes can't break
the script or change what it does. In the script:

    def CONFIG = new JsonSlurper().parseText(new String("__CONFIG_B64__".decodeBase64(), "UTF-8"))

Output contract: print one JSON document between the two marker lines. As a
fallback (scripts written for the console by hand), the first line of output
that starts with `[` or `{` is parsed as JSON — so a script that prints
"Total rows generated: N" followed by a pretty-printed JSON array works
unchanged.
"""

import base64
import json
import re
from typing import Any

PLACEHOLDER = "__CONFIG_B64__"
JSON_START = "===GROOVY_RUNNER_JSON_START==="
JSON_END = "===GROOVY_RUNNER_JSON_END==="

_CONFIG_RE = re.compile(r'"([A-Za-z0-9+/=]*)"\.decodeBase64\(\)')


class GroovyOutputError(RuntimeError):
    """The console ran, but its output didn't contain a JSON result."""


def build_script(template: str, config: dict[str, Any]) -> str:
    if PLACEHOLDER not in template:
        if config:
            raise RuntimeError(f"Script declares inputs but has no {PLACEHOLDER} placeholder to receive them")
        return template
    payload = json.dumps(config, ensure_ascii=False).encode("utf-8")
    return template.replace(PLACEHOLDER, base64.b64encode(payload).decode("ascii"))


def extract_config(script: str) -> dict[str, Any]:
    """Inverse of build_script() — used by the mock console and tests."""
    match = _CONFIG_RE.search(script)
    if not match:
        return {}
    return json.loads(base64.b64decode(match.group(1)).decode("utf-8"))


def parse_output(output: str) -> Any:
    text = output or ""
    start, end = text.rfind(JSON_START), text.rfind(JSON_END)
    if start != -1 and end > start:
        body = text[start + len(JSON_START):end].strip()
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise GroovyOutputError(f"Groovy output had malformed result JSON: {exc}") from exc
    decoder = json.JSONDecoder()
    offset = 0
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith(("[", "{")):
            try:
                value, _ = decoder.raw_decode(text[offset:].lstrip())
                return value
            except json.JSONDecodeError:
                pass
        offset += len(line)
    preview = text.strip()[:500] or "(empty output)"
    raise GroovyOutputError(f"Groovy output did not contain result JSON. Output began with: {preview}")


def split_payload(payload: Any) -> tuple[Any, dict[str, Any]]:
    """(data to save/format, summary metadata). A `{"rows": [...], ...}`
    result saves just the rows — the shape format_references_batch.py
    always read — and keeps the other keys as run metadata."""
    if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        return payload["rows"], {k: v for k, v in payload.items() if k != "rows"}
    return payload, {}
