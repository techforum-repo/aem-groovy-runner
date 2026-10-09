from __future__ import annotations

import pandas as pd
import streamlit as st

from .. import settings_store
from ..logging_setup import get_logger
from .shared import active_environment, audit_event, forget_sign_in, is_mock, render_user_sign_in


def _render_environments() -> None:
    st.markdown("#### AEM environments")
    st.caption("One row per server: a short name (shown in the sidebar and on the Run button) and its author URL. "
               "Each environment has its own sign-in. Add or remove rows, then Save.")
    envs = settings_store.environments()
    rows = pd.DataFrame([{"name": e.name, "url": e.url} for e in envs] or [{"name": "", "url": ""}])
    edited = st.data_editor(rows, num_rows="dynamic", width="stretch", hide_index=True, key="env_editor",
                            column_config={
                                "name": st.column_config.TextColumn("Name", help="e.g. DEV, QA, STAGE, PROD",
                                                                    required=True),
                                "url": st.column_config.TextColumn(
                                    "Author URL", help="e.g. https://author-p12345-e67890.adobeaemcloud.com",
                                    required=True)})
    if st.button("Save environments", type="primary"):
        new, problems = settings_store.validate_environments(edited.fillna("").to_dict("records"))
        if problems:
            for problem in problems:
                st.error(problem)
            return
        if not new:
            st.error("Add at least one environment.")
            return
        old = {e.name: e.url for e in envs}
        now = {e.name: e.url for e in new}
        # A sign-in belongs to one server: never send it to a changed URL.
        for name in [n for n, url in old.items() if now.get(n) != url]:
            forget_sign_in(name)
        settings_store.save_environments(new)
        changes = {"added": sorted(set(now) - set(old)), "removed": sorted(set(old) - set(now)),
                   "url_changed": sorted(n for n in set(old) & set(now) if old[n] != now[n])}
        audit_event("settings.environments_saved", details={**changes, "environments": [
            {"name": e.name, "url": e.url} for e in new]})
        get_logger().info("Environments saved: %s", ", ".join(now))
        st.success("Saved." + (" Signed out of: " + ", ".join(changes["removed"] + changes["url_changed"])
                               if changes["removed"] or changes["url_changed"] else ""))
        st.rerun()


def _render_connection() -> None:
    st.markdown("#### General")
    values = settings_store.current_values()
    overridden = settings_store.overridden_keys()
    with st.form("settings_form"):
        new: dict = {}
        for field in settings_store.FIELDS:
            label = field.label + (" •" if field.key in overridden else "")
            value = values[field.key]
            if field.kind == "bool":
                new[field.key] = st.toggle(label, value=bool(value), help=field.help)
            elif field.kind == "float":
                new[field.key] = st.number_input(label, value=float(value), min_value=30.0, step=60.0, help=field.help)
            else:
                new[field.key] = st.text_input(label, value=str(value), help=field.help)
        saved = st.form_submit_button("Save settings", type="primary")
    if saved:
        changed = {k: {"from": values[k], "to": v} for k, v in new.items() if str(values[k]) != str(v)}
        settings_store.save(new)
        get_logger().info("Settings saved: %s", ", ".join(sorted(changed)))
        audit_event("settings.saved", details={"changed": changed})
        st.success("Saved.")
        st.rerun()
    st.caption("• = overridden here (otherwise the `.env` default applies). Stored in the local SQLite DB.")
    general = overridden & {f.key for f in settings_store.FIELDS}
    if general and st.button("Reset these to .env defaults"):
        settings_store.reset_fields()
        audit_event("settings.reset")
        st.rerun()


def _render_sign_in_help() -> None:
    st.markdown("#### Signing in")
    st.markdown(
        "This tool runs every script **as you**, using a 24-hour **Local Development Token** issued for your own "
        "SSO login, so AEM applies your permissions and records your name. Sign in below (or from the sidebar):\n"
        "1. Cloud Manager → your program → environment **⋯** → **Developer Console** (sign in with your usual SSO).\n"
        "2. **Integrations** → **Local token** → **Get Local Development Token**.\n"
        "3. Paste it (the whole JSON is fine) and click **Sign in**.\n\n"
        "Each environment needs its own token (from that environment's Developer Console) and has its own "
        "sign-in. The token is held only in this browser session's memory. It is never written to disk, the database or the "
        "logs, and it's gone when you sign out, close the tab, or after 24 hours.\n\n"
        "**Requirements:** access to Developer Console for the environment, and membership of a group in the "
        "Groovy Console's `allowedGroups`. If you can already run scripts in the Groovy Console UI, you have both."
    )
    env = active_environment()
    if is_mock():
        st.caption("Mock mode is on, so no sign-in is needed. Turn it off above to sign in.")
    elif env is None:
        st.caption("Add an environment above first, then sign in here or in the sidebar.")
    else:
        with st.container(border=True):
            st.caption(f"Environment: **{env.name}** ({env.host}). Pick another in the sidebar.")
            render_user_sign_in("settings")
    st.info("🔒 Because scripts run with *your* permissions (which may include writes), the read-only check and "
            "runtime guard (see the Scripts page) are what keep this tool read-only.")


def render() -> None:
    st.markdown("### Settings")
    _render_environments()
    st.divider()
    _render_connection()
    st.divider()
    _render_sign_in_help()
