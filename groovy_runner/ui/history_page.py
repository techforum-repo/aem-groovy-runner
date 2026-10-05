from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from .. import database
from .shared import download_button


def render() -> None:
    st.markdown("### History")
    st.caption("Every run across sessions. Files from earlier sessions can be downloaded here but not re-formatted; "
               "re-run the script to format it again.")
    runs = database.list_runs()
    if not runs:
        st.info("No runs yet.")
        return
    for run in runs:
        run["outputs"] = {fid: o["path"] for fid, o in json.loads(run.get("outputs_json") or "{}").items()}
    df = pd.DataFrame(runs)
    df["formatted"] = df["outputs"].map(lambda o: ", ".join(o) or "—")
    st.dataframe(df[["id", "created_at", "actor", "aem_user", "script_id", "label", "status", "row_count", "running_time",
                     "aem_host", "formatted", "error"]],
                 width="stretch", hide_index=True)
    files = [(r, p) for r in runs for p in [r["json_path"], r.get("executed_script_path"), *r["outputs"].values()]
             if p and Path(p).exists()]
    if files:
        run, path = st.selectbox("Download a file", files,
                                 format_func=lambda rp: f"{rp[0]['created_at']} · {rp[0]['script_id']} · {Path(rp[1]).name}")
        p = Path(path)
        download_button(f"⬇️ {p.name}", lambda: p.read_bytes(), p.name, source_paths=[str(p)])
