from __future__ import annotations

import streamlit as st

from groovy_runner import database, formatter_links
from groovy_runner.config import harden_env_file
from groovy_runner.scripts_registry import discover
from groovy_runner.ui import audit_page, diagnostics_page, formatters_page, history_page, run_page, scripts_page, settings_page
from groovy_runner.ui.shared import CUSTOM_CSS, init_session_state, render_hero, render_sidebar

harden_env_file()
database.initialize()
# One-time: seed formatter_links.json from where links used to live (manifest
# "formatters" lists, then the old Scripts-page mapping in the DB).
formatter_links.migrate_if_missing(
    [(s.id, s.legacy_manifest_formatters) for s in discover()] + list(database.get_formatter_overrides().items()))
st.set_page_config(page_title="AEM Groovy Runner", page_icon="⚙️", layout="wide")
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

init_session_state()
page = render_sidebar()
render_hero()

PAGES = {
    "Run": run_page.render,
    "Scripts": scripts_page.render,
    "Formatters": formatters_page.render,
    "History": history_page.render,
    "Audit": audit_page.render,
    "Settings": settings_page.render,
    "Diagnostics": diagnostics_page.render,
}
PAGES[page]()
