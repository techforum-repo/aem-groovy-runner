from __future__ import annotations

import streamlit as st

from ..logging_setup import LOG_PATH
from ..utils import display_path
from .shared import audit_event, download_button, get_client, is_mock, render_friendly_error


def render() -> None:
    st.markdown("### Diagnostics")
    st.markdown("#### Connection check")
    st.caption("Checks authentication (`/libs/granite/security/currentuser.json`) and runs a one-line Groovy script.")
    if is_mock():
        st.info("Mock mode is on — this check uses the mock console. Turn it off on the Settings page to test AEM.")
    if st.button("Test connection", type="primary"):
        try:
            st.session_state["_diag_result"] = ("ok", get_client().test_connection())
            audit_event("connection.tested", aem_user=st.session_state["_diag_result"][1][0])
        except Exception as exc:
            st.session_state["_diag_result"] = ("error", exc)
            audit_event("connection.tested", status="error", details={"error": str(exc)[:500]})
    result = st.session_state.get("_diag_result")
    if result:
        status, detail = result
        if status == "ok":
            user, running_time = detail
            st.success(f"Authenticated as **{user}** — Groovy Console ran a test script in {running_time}.")
        else:
            render_friendly_error(detail, key="diag_retry", context="Testing the AEM connection", retry=False)

    st.divider()
    st.markdown("#### Logs")
    st.caption(f"Rotating file log at `{display_path(LOG_PATH)}` (max 1MB × 3 backups).")
    if LOG_PATH.exists():
        download_button("Download log file", lambda: LOG_PATH.read_bytes(), LOG_PATH.name, "text/plain")
    else:
        st.caption("No log file yet.")
