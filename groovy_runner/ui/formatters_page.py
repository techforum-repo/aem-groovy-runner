from __future__ import annotations

import pandas as pd
import streamlit as st

from .. import formatter_links, formatters
from ..scripts_registry import discover
from .shared import audit_event

_NONE = "— not linked —"


def _name(fid: str) -> str:
    return formatters.FORMATTERS[fid].name if fid in formatters.FORMATTERS else f"{fid} (unknown)"


def _render_catalog(links: dict[str, tuple[list[str], str]], script_names: dict[str, str]) -> None:
    st.markdown("#### Formatters")
    st.dataframe(pd.DataFrame([{
        "Formatter": f.name, "ID": f.id, "Output": f.extension, "What it produces": f.description,
        "Linked scripts": ", ".join(script_names.get(s, f"{s} (missing)") for s in formatter_links.scripts_linked_to(f.id, links)) or "—",
    } for f in formatters.FORMATTERS.values()]), width="stretch", hide_index=True)


def _render_links_editor(links: dict[str, tuple[list[str], str]], scripts) -> None:
    st.markdown("#### Links")
    st.caption("Tick the formatters each script may use and pick its **default**: it runs right after a run, and "
               "the Run page lets you choose another linked formatter for a single run. "
               f"Saved to `{formatter_links.LINKS_PATH.name}`, separate from scripts and formatters.")
    ids = list(formatters.FORMATTERS)
    rows = []
    for script in scripts:
        link = formatter_links.link_for(script.id, links)
        row = {"script_id": script.id, "Script": script.name,
               "Default": _name(link.default) if link.linked else _NONE}
        row.update({_name(fid): link.linked and fid in link.formatters for fid in ids})
        rows.append(row)
    edited = st.data_editor(
        pd.DataFrame(rows), hide_index=True, width="stretch", key="links_editor",
        column_order=["Script", "Default", *(_name(f) for f in ids)],
        disabled=["Script"],
        column_config={
            "Default": st.column_config.SelectboxColumn("Default", options=[_NONE, *(_name(f) for f in ids)], required=True),
            **{_name(f): st.column_config.CheckboxColumn(_name(f)) for f in ids},
        },
    )
    if st.button("Save links", type="primary"):
        by_name = {_name(f): f for f in ids}
        new_links = formatter_links.from_grid(
            [{"script_id": r["script_id"], "default": by_name.get(r["Default"]),
              "ticked": [f for f in ids if r.get(_name(f))]} for r in edited.to_dict("records")],
            links, {s.id for s in scripts})
        if new_links != links:
            formatter_links.save(new_links)
            audit_event("formatter_links.changed", target=formatter_links.LINKS_PATH.name, details={
                "before": {k: {"formatters": v[0], "default": v[1]} for k, v in links.items()},
                "after": {k: {"formatters": v[0], "default": v[1]} for k, v in new_links.items()}})
            st.session_state["_links_message"] = "Links saved."
        else:
            st.session_state["_links_message"] = "No changes."
        st.rerun()
    if st.session_state.get("_links_message"):
        st.success(st.session_state.pop("_links_message"))

    unlinked = [s.name for s in scripts if s.id not in links]
    if unlinked:
        st.warning("Not linked yet (using Generic Excel until you link one): " + ", ".join(unlinked))
    orphans = sorted(set(links) - {s.id for s in scripts})
    if orphans:
        st.caption("Links kept for scripts that aren't in `scripts/` right now: " + ", ".join(orphans))
    unknown = sorted({f for ids_, _ in links.values() for f in ids_ if f not in formatters.FORMATTERS})
    if unknown:
        st.error("The links file names formatter(s) that don't exist and will be ignored: " + ", ".join(unknown))


def render() -> None:
    st.markdown("### Formatters")
    st.caption("Formatters turn a run's JSON result into a file. They're independent of scripts; the links below "
               "connect the two.")
    try:
        links = formatter_links.load()
    except ValueError as exc:
        st.error(f"{exc}. Fix or delete the file; saving below rewrites it.")
        links = {}
    scripts = discover()
    _render_catalog(links, {s.id: s.name for s in scripts})
    st.divider()
    _render_links_editor(links, scripts)
    st.divider()
    st.markdown("#### Adding a formatter")
    st.markdown("Create a module in `groovy_runner/formatters/` exposing `FORMATTER` (id, name, description, "
                "extension, `check`, `to_dataframe`, `write`; `asset_reference.py` is a short example), register "
                "it in `formatters/__init__.py`, and restart the app. It then shows up here, ready to link.")
