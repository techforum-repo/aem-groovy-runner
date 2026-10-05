from __future__ import annotations

"""Turn raw exception text into a title + a short list of likely causes.

Kept framework-agnostic (no Streamlit import) so it stays unit-testable; the
UI wraps this with a small renderer in ui/shared.py.
"""

from dataclasses import dataclass, field


class GroovyScriptError(RuntimeError):
    """The Groovy Console accepted the request but the script threw."""


@dataclass(frozen=True)
class FriendlyError:
    title: str
    reasons: list[str] = field(default_factory=list)
    retryable: bool = True


_CONNECTION_REASONS = [
    "AEM_AUTHOR_URL is wrong, or the environment is hibernated/stopped",
    "No internet connectivity, VPN required, or a proxy/firewall is blocking the request",
]
_TIMEOUT_REASONS = [
    "The script has too much to process within the per-run timeout — narrow the input "
    "(e.g. split a DAM folder into subfolders, or exclude large subfolders)",
    "Raise the per-run timeout on the Settings page (the AEM side may still cut very long requests off)",
]
_AUTH_REASONS = [
    "Your Local Development Token has expired (they last 24 hours) or was copied incompletely — get a fresh one "
    "from Developer Console and sign in again in the sidebar",
    "The token was issued for a different AEM environment than the author URL on the Settings page",
]
_FORBIDDEN_REASONS = [
    "Your user isn't in a group allowed to use the Groovy Console "
    "(Groovy Console OSGi config: allowedGroups) — if you can run scripts in the console UI, check "
    "AEM_AUTHOR_URL points at the same environment",
    "The Groovy Console is disabled on this environment (common on production)",
    "The account lacks read access to the content the script reads",
]
_NOT_FOUND_REASONS = [
    "The Groovy Console isn't installed on this environment",
    "GROOVY_CONSOLE_ENDPOINT is wrong (default /bin/groovyconsole/post.json)",
]
_CONFIG_REASONS = [
    "You're not signed in (sidebar), or the AEM author URL isn't set on the Settings page",
]


def friendly_error(exc: BaseException) -> FriendlyError:
    message = str(exc).strip()
    lowered = message.lower()

    violations = getattr(exc, "violations", None)
    if violations is not None:
        return FriendlyError(
            "Blocked by the read-only check — nothing was sent to AEM",
            [f"Line {v.line}: {v.message} — {v.snippet}" for v in violations[:15]]
            + ["This tool only runs read-only scripts. Remove the write/escape-hatch call, or run the script "
               "in the Groovy Console directly if a write is truly intended."],
            retryable=False,
        )
    if isinstance(exc, GroovyScriptError):
        return FriendlyError(
            "The Groovy script failed inside AEM",
            ["See the stack trace under Technical details", "Check the inputs are valid (e.g. JCR paths exist)"],
            retryable=False,
        )
    if "local development token expired" in lowered:
        return FriendlyError("Your sign-in has expired", [
            "Local Development Tokens last 24 hours. In Developer Console → Integrations → Local token, "
            "click “Get Local Development Token” and sign in again from the sidebar.",
        ], retryable=False)
    if "not configured" in lowered:
        return FriendlyError("AEM is not configured", _CONFIG_REASONS, retryable=False)
    if "groovy runner read-only mode" in lowered:
        return FriendlyError("A write was blocked at runtime by the read-only guard", [
            "The script tried to persist changes (save/commit/create/delete/...) and was stopped inside AEM",
        ], retryable=False)
    if "traversal" in lowered and ("fail" in lowered or "read or traversed" in lowered):
        return FriendlyError("AEM refused the query: no index could serve it", [
            "The query would have had to crawl the repository, and the script asks AEM to fail instead "
            "(OPTION(TRAVERSAL FAIL)), which protects a large repository",
            "Narrow it: a deeper folder path, or exact types instead of a wildcard",
            "Your AEM index definitions may not cover this property (e.g. dc:format in damAssetLucene); ask "
            "your AEM developers to check",
        ], retryable=False)
    if "path not found" in lowered:
        return FriendlyError("Path not found in AEM", ["Check the path for typos (it is case-sensitive)",
                                                        "The account may not have read access to it"], retryable=False)
    if "did not contain result json" in lowered or "malformed result json" in lowered:
        return FriendlyError("Could not read a JSON result from the Groovy output", [
            "The script doesn't print JSON — print it between the ===GROOVY_RUNNER_JSON_START=== / _END=== "
            "marker lines (see README)",
            "The Groovy Console returned an unexpected response (maybe a login page)",
        ], retryable=False)
    if "no __config_b64__ placeholder" in lowered:
        return FriendlyError("Script can't receive its inputs", [
            "Add the CONFIG line from the README to script.groovy, or remove the inputs from manifest.json",
        ], retryable=False)
    if "cannot connect" in lowered:
        return FriendlyError("AEM connection failed", _CONNECTION_REASONS)
    if "timed out" in lowered or "timeout" in lowered:
        return FriendlyError("AEM request timed out", _TIMEOUT_REASONS)
    if "http 401" in lowered:
        return FriendlyError("AEM rejected the credentials (401)", _AUTH_REASONS, retryable=False)
    if "http 403" in lowered:
        return FriendlyError("AEM denied access (403)", _FORBIDDEN_REASONS, retryable=False)
    if "http 404" in lowered:
        return FriendlyError("Groovy Console endpoint not found (404)", _NOT_FOUND_REASONS, retryable=False)
    if "http 429" in lowered:
        return FriendlyError("AEM is rate-limiting requests", ["Wait a moment and retry."])
    if "http 5" in lowered:
        return FriendlyError("AEM returned a server error", ["The author instance may be overloaded or restarting."])

    return FriendlyError("Something went wrong", [message] if message else ["An unexpected error occurred."])
