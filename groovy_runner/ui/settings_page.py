from __future__ import annotations

import streamlit as st

from .. import settings_store
from ..logging_setup import get_logger
from .shared import audit_event, is_mock, render_user_sign_in


def _render_connection() -> None:
    st.markdown("#### AEM connection")
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
        if new["aem_author_url"] and not new["aem_author_url"].startswith(("https://", "http://")):
            st.error("AEM author URL must start with https://")
            return
        changed = {k: {"from": values[k], "to": v} for k, v in new.items() if str(values[k]) != str(v)}
        settings_store.save(new)
        if "aem_author_url" in changed and st.session_state.get("user_token") is not None:
            # A token belongs to one AEM environment; don't send it to another.
            audit_event("auth.signed_out", aem_user=st.session_state.get("aem_user", ""),
                        details={"reason": "AEM author URL changed", "from": values["aem_author_url"],
                                 "to": new["aem_author_url"]})
            st.session_state.update(user_token=None, aem_user="", _remembered=False, _restore_tried_for=None,
                                    _sign_in_notice="AEM author URL changed: sign in for the new environment.")
        get_logger().info("Settings saved: %s", ", ".join(sorted(changed)))
        audit_event("settings.saved", details={"changed": changed})
        st.success("Saved.")
        st.rerun()
    st.caption("• = overridden here (otherwise the `.env` default applies). Stored in the local SQLite DB.")
    if overridden and st.button("Reset all to .env defaults"):
        settings_store.reset()
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
        "The token is held only in this browser session's memory. It is never written to disk, the database or the "
        "logs, and it's gone when you sign out, close the tab, or after 24 hours.\n\n"
        "**Requirements:** access to Developer Console for the environment, and membership of a group in the "
        "Groovy Console's `allowedGroups`. If you can already run scripts in the Groovy Console UI, you have both."
    )
    if is_mock():
        st.caption("Mock mode is on, so no sign-in is needed. Turn it off above to sign in.")
    elif not settings_store.get("aem_author_url"):
        st.caption("Set the AEM author URL above first, then sign in here or in the sidebar.")
    else:
        with st.container(border=True):
            render_user_sign_in("settings")
    st.info("🔒 Because scripts run with *your* permissions (which may include writes), the read-only check and "
            "runtime guard (see the Scripts page) are what keep this tool read-only.")


def render() -> None:
    st.markdown("### Settings")
    _render_connection()
    st.divider()
    _render_sign_in_help()
