from __future__ import annotations

"""State, navigation, and small widgets shared across every page in
groovy_runner/ui/*. Page-specific rendering stays in that page's own module."""

import base64
import html
import hashlib
import json
import uuid
from datetime import datetime
from typing import Any, Callable
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from .. import audit, settings_store, token_store, user_token
from ..clients.groovy_console import GroovyConsoleClient
from ..clients.mock import MockGroovyConsoleClient
from ..errors import friendly_error

PAGE_NAMES = ["Run", "Scripts", "Formatters", "History", "Audit", "Settings", "Diagnostics"]

CUSTOM_CSS = """<style>
.block-container{max-width:1450px;padding-top:1.35rem}
.hero{padding:1.1rem 1.35rem;border:1px solid #ddd;border-radius:16px;margin-bottom:1rem}
[data-testid=stMetric]{border:1px solid #ddd;padding:1rem;border-radius:14px}
.badge{padding:.25rem .55rem;border:1px solid #ccc;border-radius:999px;font-size:.8rem}
</style>"""


def init_session_state() -> None:
    defaults = {
        # Scopes output files and the Format step to this browser session.
        "session_id": datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6],
        "session_files": [],  # runner.GeneratedFile, newest last
        "last_run": None,  # {"script_id", "values", "batch_id"} for Retry failed
        # Your own sign-in (user_token.UserToken). In memory; also in the OS
        # keychain only if you ticked "Remember" (token_store.py).
        "user_token": None,
        "aem_user": "",
    }
    new_session = "session_id" not in st.session_state
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value
    if new_session:
        audit.log("session.started", session_id=st.session_state["session_id"],
                  details={"mock_mode": is_mock(), "environments": [e.name for e in settings_store.environments()]})


def audit_event(action: str, **kwargs) -> None:
    """audit.log() with this browser session's id filled in."""
    audit.log(action, session_id=st.session_state.get("session_id", ""), **kwargs)


def download_button(label: str, data: bytes | str | Callable[[], bytes | str], file_name: str,
                    mime: str = "application/octet-stream", *, source_paths: list[str] | None = None,
                    **kwargs: Any) -> None:
    """st.download_button with deferred data: the bytes are produced (read,
    zipped) only when clicked, on Streamlit's download thread, not on every
    page rerun. The file.downloaded audit event is written at that moment with
    the hash of exactly what was handed over."""
    session_id, aem_user = st.session_state.get("session_id", ""), st.session_state.get("aem_user", "")

    def deferred() -> bytes:  # runs without a Streamlit context: no st.* in here
        payload = data() if callable(data) else data
        payload = payload.encode("utf-8") if isinstance(payload, str) else payload
        audit.log("file.downloaded", session_id=session_id, aem_user=aem_user, target=file_name, details={
            "file_name": file_name, "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload),
            "source_paths": source_paths or []})
        return payload

    st.download_button(label, deferred, file_name, mime, on_click="ignore", **kwargs)


# Raw bytes embedded in one multi-download button. They travel base64-encoded
# in the page on every rerun, so larger sets fall back to the zip / single
# downloads (which are only built when clicked).
MULTI_DOWNLOAD_MAX_BYTES = 25 * 1024 * 1024


def _b64_cached(path: Path, data: bytes) -> str:
    """Base64 of a result file, cached per (path, size, mtime) for the session."""
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    cache = st.session_state.setdefault("_b64_cache", {})
    if key not in cache:
        cache[key] = base64.b64encode(data).decode("ascii")
    return cache[key]


def multi_download_button(paths: list[str], label: str, mime: str) -> None:
    """One click → every file downloads individually (no zip).

    Streamlit's download_button handles one file per button, so this renders
    a small HTML button (in a Streamlit iframe, which allows downloads)
    that starts each file's download in turn from the same user click. The
    browser asks once to allow multiple downloads from this site.

    The click happens inside the browser, out of the app's sight, so the
    audit trail records that the button was *offered* for this file set
    (once per distinct set per session) with each file's hash; the zip and
    single-file buttons still audit actual clicks.
    """
    files, seen_names, total = [], set(), 0
    for path in paths:
        p = Path(path)
        if not p.exists():
            continue
        data = p.read_bytes()
        total += len(data)
        name = p.name
        if name in seen_names:  # same slug from two runs: keep both, distinguishably
            name = f"{p.parent.name}_{p.name}"
        seen_names.add(name)
        files.append({"name": name, "path": str(p), "data": data})
    if not files:
        return
    if total > MULTI_DOWNLOAD_MAX_BYTES:
        st.caption(f"{len(files)} files, {total / 1048576:.0f} MB in total: too large for one-click download. "
                   "Use the zip or single-file download.")
        return

    offer_key = hashlib.sha256("|".join(f["path"] for f in files).encode()).hexdigest()[:16]
    offered = st.session_state.setdefault("_download_offers", set())
    if offer_key not in offered:
        offered.add(offer_key)
        audit_event("files.download_offered", target=f"{len(files)} file(s)", details={"files": [
            {"name": f["name"], "path": f["path"], "sha256": hashlib.sha256(f["data"]).hexdigest()} for f in files]})

    payload = json.dumps([{"name": f["name"], "mime": mime, "b64": _b64_cached(Path(f["path"]), f["data"])}
                          for f in files]).replace("</", "<\\/")
    html = f"""
<style>
  body {{ margin: 0; font-family: "Source Sans Pro", sans-serif; }}
  button {{ width: 100%; padding: .55rem 1rem; border-radius: .5rem; border: 1px solid #ff4b4b;
           background: #ff4b4b; color: #fff; font-size: 1rem; cursor: pointer; }}
  button:hover {{ background: #ff3333; }}
  #msg {{ font-size: .8rem; color: #808495; margin-top: .3rem; }}
</style>
<button id="dl">{label}</button>
<div id="msg"></div>
<script>
const files = {payload};
const msg = document.getElementById("msg");
function toBlob(b64, mime) {{
  const bin = atob(b64), bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Blob([bytes], {{ type: mime }});
}}
document.getElementById("dl").addEventListener("click", async () => {{
  for (let i = 0; i < files.length; i++) {{
    const url = URL.createObjectURL(toBlob(files[i].b64, files[i].mime));
    const a = document.createElement("a");
    a.href = url; a.download = files[i].name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
    msg.textContent = `Started ${{i + 1}} of ${{files.length}}…`;
    await new Promise(r => setTimeout(r, 400));
  }}
  msg.textContent = `Started ${{files.length}} download(s). If fewer arrived, allow “multiple downloads” in the browser prompt and click again.`;
}});
</script>"""
    # st.iframe replaced components.v1.html (deprecated after 2026-06-01); both
    # give a sandboxed frame that allows downloads. The HTML is built here from
    # app-generated names/bytes only (JSON-escaped), never from user input.
    if hasattr(st, "iframe"):
        st.iframe(html, height=78)
    else:  # Streamlit < 1.5x
        components.html(html, height=78)


def is_mock() -> bool:
    return bool(settings_store.get("mock_mode"))


# --- AEM environments: one picked in the sidebar, a separate sign-in each ------

# The session keys that make up one environment's sign-in. The rest of the app
# reads them as "the current sign-in"; switching environments swaps them.
_SIGN_IN_KEYS = ("user_token", "aem_user", "_remembered", "_remember_error", "_sign_in_notice", "_sign_in_error",
                 "_sign_in_url")  # the author URL the token was issued for


def active_environment() -> settings_store.Environment | None:
    """The environment picked in the sidebar (this browser session), else the
    one used last on this machine, else the first configured."""
    envs = settings_store.environments()
    if not envs:
        return None
    names = [e.name for e in envs]
    chosen = st.session_state.get("active_env")
    if chosen not in names:
        last = settings_store.last_environment()
        chosen = last if last in names else names[0]
        st.session_state["active_env"] = chosen
    return envs[names.index(chosen)]


def _sync_sign_in() -> None:
    """Keep each environment's sign-in separate: when the selection changes,
    park the current one and bring back the selected environment's."""
    env = active_environment()
    name = env.name if env else ""
    current = st.session_state.get("_sign_in_env")
    if current != name:
        parked = st.session_state.setdefault("sign_ins", {})
        if current is not None:
            parked[current] = {k: st.session_state.get(k) for k in _SIGN_IN_KEYS}
        restored = parked.pop(name, {})
        st.session_state.update({k: restored.get(k) for k in _SIGN_IN_KEYS})
        st.session_state["aem_user"] = st.session_state.get("aem_user") or ""
        st.session_state["_sign_in_env"] = name
    # A token belongs to one server: if this environment's URL changed since sign-in (here or in
    # another tab), drop it rather than send it to the new URL.
    signed_for = st.session_state.get("_sign_in_url")
    if env is not None and st.session_state.get("user_token") is not None and signed_for and signed_for != env.url:
        audit_event("auth.signed_out", aem_user=st.session_state.get("aem_user", ""),
                    details={"reason": "environment URL changed", "environment": env.name, "from": signed_for,
                             "to": env.url})
        st.session_state.update({k: None for k in _SIGN_IN_KEYS})
        st.session_state.update(aem_user="", _sign_in_notice=f"{env.name}'s author URL changed: sign in again.")
        st.session_state.setdefault("_restore_tried", set()).discard(env.name)


def forget_sign_in(env_name: str) -> None:
    """Drop one environment's sign-in from this session (e.g. its URL changed)."""
    st.session_state.setdefault("sign_ins", {}).pop(env_name, None)
    st.session_state.setdefault("_restore_tried", set()).discard(env_name)
    if st.session_state.get("_sign_in_env") == env_name:
        st.session_state.update({k: None for k in _SIGN_IN_KEYS})
        st.session_state["aem_user"] = ""


def get_client() -> GroovyConsoleClient | MockGroovyConsoleClient:
    if is_mock():
        return MockGroovyConsoleClient()
    return GroovyConsoleClient(user_token=st.session_state.get("user_token"), environment=active_environment())


def is_local_client() -> bool:
    """True when the browser is on the same machine as the app. Streamlit
    reports no IP for localhost connections. Streamlit notes this can be
    spoofed behind a proxy, so the real control is .streamlit/config.toml
    binding the server to localhost; this is a second line of defence for the
    remembered sign-in, which belongs to the OS user running the app."""
    try:
        ip = st.context.ip_address
    except Exception:
        return True  # older Streamlit without st.context: rely on the localhost binding
    if ip is not None and not isinstance(ip, str):
        return True  # not a real connection (e.g. Streamlit's test harness)
    return ip is None or ip in ("127.0.0.1", "::1", "::ffff:127.0.0.1")


def _try_restore() -> None:
    """Reuse a remembered sign-in for the selected environment: once per
    browser session per environment, so a sign-out isn't immediately undone.
    Only for browsers on this machine (see is_local_client)."""
    env = active_environment()
    tried = st.session_state.setdefault("_restore_tried", set())
    if env is None or env.name in tried or st.session_state.get("user_token") is not None:
        return
    if not is_local_client():
        return
    tried.add(env.name)
    author = env.url
    remembered = token_store.load(author)
    if remembered is None:
        return
    token, aem_user = remembered
    note = ""
    if not is_mock():
        try:
            aem_user = GroovyConsoleClient(user_token=token, environment=env).current_user()
        except Exception as exc:
            if is_auth_rejection(exc):
                token_store.delete(author)
                st.session_state["_sign_in_notice"] = "Your saved sign-in is no longer valid. Please sign in again."
                audit_event("auth.restore_rejected", aem_user=aem_user, target=aem_user, status="error",
                            details={"token_fingerprint": token.fingerprint, "error": str(exc)[:300]})
                return
            # AEM unreachable (VPN off, network): keep the sign-in; runs will report the real error.
            note = f"couldn't confirm with AEM: {str(exc)[:120]}"
    st.session_state.update(user_token=token, aem_user=aem_user, _remembered=True, _sign_in_url=author)
    audit_event("auth.restored", aem_user=aem_user, target=aem_user, details={
        "source": "os-keychain", "expires_at": token.expires_label, "token_fingerprint": token.fingerprint,
        "environment": env.name, "aem_author_url": author, "verified_with_aem": not note and not is_mock(),
        "note": note})


def is_auth_rejection(exc: BaseException) -> bool:
    """AEM refused the token itself (401 / anonymous), not a network problem."""
    text = str(exc).lower()
    return "http 401" in text or "treated as anonymous" in text or "local development token expired" in text


def sign_out_rejected(reason: str) -> None:
    """Called when AEM rejects the token mid-session: forget it everywhere so
    the sign-in box comes back, with the reason shown there."""
    env = active_environment()
    if env is not None:
        token_store.delete(env.url)
    audit_event("auth.signed_out", aem_user=st.session_state.get("aem_user", ""),
                target=st.session_state.get("aem_user", ""), status="error",
                details={"reason": reason[:300], "environment": env.name if env else ""})
    st.session_state.update(user_token=None, aem_user="", _remembered=False, _sign_in_notice=reason)


def _sign_in(raw: str, remember: bool) -> None:
    env = active_environment()
    try:
        if env is None:
            raise RuntimeError("Add an AEM environment on the Settings page first.")
        token = user_token.parse(raw)
        if token.is_expired():
            raise ValueError(f"Your Local Development Token expired at {token.expires_label} — get a new one.")
        aem_user = GroovyConsoleClient(user_token=token, environment=env).current_user()
    except Exception as exc:
        st.session_state["_sign_in_error"] = exc
        audit_event("auth.sign_in_failed", status="error",
                    details={"error": str(exc)[:300], "environment": env.name if env else ""})
        return
    author = env.url
    remembered, remember_error = False, ""
    if remember:
        try:
            token_store.save(author, token, aem_user)
            remembered = True
        except Exception as exc:
            remember_error = str(exc)
    else:
        token_store.delete(author)  # don't leave an older remembered token behind
    st.session_state.update(user_token=token, aem_user=aem_user, _sign_in_error=None, _remembered=remembered,
                            _remember_error=remember_error, _sign_in_notice=None, _sign_in_url=author)
    for key in [k for k in st.session_state if str(k).startswith("_token_input")]:
        st.session_state[key] = ""
    audit_event("auth.signed_in", aem_user=aem_user, target=aem_user, details={
        "ims_user_id": token.user_id, "ims_client_id": token.client_id, "expires_at": token.expires_label,
        "token_fingerprint": token.fingerprint, "environment": env.name, "aem_author_url": author,
        "remembered_in_os_keychain": remembered, "remember_error": remember_error})


def _sign_out() -> None:
    env = active_environment()
    forgotten = token_store.delete(env.url) if env else False
    audit_event("auth.signed_out", aem_user=st.session_state.get("aem_user", ""),
                target=st.session_state.get("aem_user", ""),
                details={"removed_from_os_keychain": forgotten, "environment": env.name if env else ""})
    st.session_state.update(user_token=None, aem_user="", _remembered=False)


def render_user_sign_in(where: str = "sidebar") -> None:
    """Sign-in panel; shown in the sidebar on every page and on the Settings
    page. `where` keeps the two copies' widget keys distinct."""
    _sync_sign_in()
    _try_restore()
    token: user_token.UserToken | None = st.session_state.get("user_token")
    if token is not None:
        left = token.seconds_left()
        st.markdown(f"Signed in as **{st.session_state.get('aem_user') or token.user_id or '?'}**")
        if left is None:
            st.caption("Token expiry unknown.")
        elif left <= 0:
            st.error(f"Sign-in expired at {token.expires_label}. Sign in again.")
        else:
            hours, minutes = int(left // 3600), int(left % 3600 // 60)
            (st.warning if left < 3600 else st.caption)(f"Token valid for {hours}h {minutes:02d}m ({token.expires_label})")
        if st.session_state.get("_remembered"):
            st.caption("🔑 Remembered in your OS keychain until it expires. Sign out to forget it.")
        if st.session_state.get("_remember_error"):
            st.caption(f"⚠️ Not remembered: {st.session_state['_remember_error']}")
        st.button("Sign out", on_click=_sign_out, width="stretch", key=f"sign_out_{where}")
        st.caption("🔒 Runs with *your* AEM permissions, so the read-only check and guard are what keep this tool read-only.")
        return

    st.markdown("**Sign in with your account (SSO)**")
    if st.session_state.get("_sign_in_notice"):
        st.warning(st.session_state["_sign_in_notice"])
    with st.expander("How to get your token", expanded=False):
        st.markdown(
            "1. Cloud Manager → your program → environment **⋯** → **Developer Console** (you sign in with your usual SSO)\n"
            "2. **Integrations** → **Local token** → **Get Local Development Token**\n"
            "3. Copy it (the whole JSON is fine) and paste below.\n\n"
            "It's valid for 24 hours and is kept only in this browser session's memory."
        )
    input_key = f"_token_input_{where}"
    st.text_input("Local Development Token", type="password", key=input_key, label_visibility="collapsed",
                  placeholder="Paste Local Development Token")
    can_remember, backend = token_store.availability()
    if can_remember and not is_local_client():
        can_remember, backend = False, "only offered when the app is opened on the machine it runs on"
    remember_key = f"_remember_{where}"
    st.session_state.setdefault(remember_key, can_remember)
    st.checkbox("Remember on this computer until it expires", key=remember_key, disabled=not can_remember,
                help=(f"Stores the token in your OS keychain ({backend}), not in a file, so refreshes and app "
                      "restarts don't ask again until it expires (24h). Sign out removes it.")
                if can_remember else f"Unavailable: {backend}")
    if not can_remember:
        st.caption(f"⚠️ Can't remember sign-ins on this computer ({backend}), so a page refresh will ask again.")
    st.button("Sign in", type="primary", width="stretch", key=f"sign_in_{where}",
              on_click=lambda: _sign_in(st.session_state.get(input_key, ""),
                                        bool(st.session_state.get(remember_key)) and can_remember))
    if st.session_state.get("_sign_in_error"):
        render_friendly_error(st.session_state["_sign_in_error"], key=f"sign_in_err_{where}", retry=False)


def _on_environment_change() -> None:
    settings_store.remember_last_environment(st.session_state.get("active_env", ""))
    _sync_sign_in()


def render_environment_picker() -> None:
    """Sidebar: pick the server, see whether you're signed in to it, sign in if not."""
    envs = settings_store.environments()
    if not envs:
        st.warning("Add an AEM environment (name + author URL) on the Settings page.")
        return
    active_environment()  # makes sure the selection is a valid environment before the widget renders
    st.selectbox("AEM environment", [e.name for e in envs], key="active_env", on_change=_on_environment_change,
                 help="Each environment has its own sign-in. A run already in progress keeps using the "
                      "environment it started on.")
    _sync_sign_in()
    _try_restore()
    env = active_environment()
    token = st.session_state.get("user_token")
    signed_in = token is not None and not token.is_expired()
    st.markdown(f"<span class='badge'>{'🟢 Signed in' if signed_in else '⚪ Not signed in'}</span> "
                f"&nbsp;{html.escape(env.host)}", unsafe_allow_html=True)
    render_user_sign_in("sidebar")


def render_sidebar() -> str:
    with st.sidebar:
        st.markdown("## ⚙️ AEM Groovy Runner")
        page = st.radio("Navigation", PAGE_NAMES, label_visibility="collapsed", key="navigation")
        job = st.session_state.get("run_job")
        if job is not None and page != "Run":
            st.info(f"⏳ {job.script_name}: {job.finished}/{job.total} done" + (" (cancelling)" if job.cancelling else "")
                    + ". See the Run page for progress and Cancel.")
        st.divider()
        if is_mock():
            st.markdown("<span class='badge'>Mock / demo data</span>", unsafe_allow_html=True)
            st.caption("Turn off Mock mode on the Settings page once AEM is configured.")
        else:
            render_environment_picker()
    return page


def render_hero() -> None:
    st.markdown(
        "<div class='hero'><h1>AEM Groovy Runner</h1>"
        "<p>Pick a Groovy script, fill in its inputs, run it on AEM through the Groovy Console API, "
        "and format this session's results into Excel.</p></div>",
        unsafe_allow_html=True,
    )


def render_friendly_error(exc: BaseException, *, key: str, context: str = "", retry: bool = True) -> bool:
    """Plain-language error box instead of a raw traceback. Returns True if
    the user clicked Retry."""
    info = friendly_error(exc)
    with st.container(border=True):
        st.error(f"**{info.title}**")
        if context:
            st.caption(context)
        if info.reasons:
            st.markdown("Possible reasons:\n\n" + "\n".join(f"- {reason}" for reason in info.reasons))
        with st.expander("Technical details"):
            st.code(str(exc) or "(no message)")
        if retry and info.retryable:
            return st.button("Retry", key=key)
    return False
