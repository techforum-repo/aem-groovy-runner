from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from .. import audit, database
from .shared import download_button


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _render_events() -> None:
    events = database.list_audit()
    if not events:
        st.info("No audit events yet.")
        return
    df = pd.DataFrame(events)
    df["day"] = pd.to_datetime(df["created_at"]).dt.date

    c1, c2, c3 = st.columns([2, 2, 3])
    with c1:
        actions = st.multiselect("Action", sorted(df["action"].unique()))
    with c2:
        picked = st.date_input("Date range (UTC)", (_utc_today() - timedelta(days=30), _utc_today()))
    with c3:
        text = st.text_input("Search (target, actor, AEM user, details)")
    # date_input returns a 1-tuple while the user is mid-way through picking a range.
    start, end = (picked[0], picked[-1]) if isinstance(picked, (list, tuple)) and picked else (date.min, date.max)
    mask = (df["day"] >= start) & (df["day"] <= end)
    if actions:
        mask &= df["action"].isin(actions)
    if text:
        hay = df["target"] + " " + df["actor"] + " " + df["aem_user"] + " " + df["details_json"]
        mask &= hay.str.contains(text, case=False, regex=False)
    view = df[mask]
    st.caption(f"{len(view)} of {len(df)} events · append-only, newest first")
    st.dataframe(view[["id", "created_at", "actor", "aem_user", "session_id", "action", "target", "status", "details_json"]],
                 width="stretch", hide_index=True)
    download_button("⬇️ Export filtered events (.csv)", view.drop(columns=["day"]).to_csv(index=False),
                    f"audit-events-{_utc_today()}.csv", "text/csv")


def _render_trace() -> None:
    runs = database.list_runs()
    if not runs:
        st.caption("No runs yet.")
        return
    run = st.selectbox("Run", runs, format_func=lambda r: f"#{r['id']} · {r['created_at']} · {r['script_id']} · {r['label']} · {r['status']}")
    outputs = json.loads(run.get("outputs_json") or "{}")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**Who** — {run.get('actor') or '?'} as AEM user `{run.get('aem_user') or '?'}`")
        st.markdown(f"**Where** — `{run['aem_host']}` · session `{run['session_id']}`")
        st.markdown(f"**When** — {run.get('started_at') or '?'} → {run.get('finished_at') or '?'} (AEM: {run['running_time'] or '—'})")
    with c2:
        st.markdown(f"**Script** — `{run['script_id']}` · template sha256 `{(run.get('template_sha256') or '')[:16]}…`")
        st.markdown(f"**Executed script** sha256 `{(run.get('script_sha256') or '')[:16]}…`")
        st.markdown(f"**Result** — {run['status']}, {run['row_count'] if run['row_count'] is not None else '—'} rows")
    st.markdown("**Inputs**")
    st.json(json.loads(run.get("config_json") or "{}"))
    if run.get("error"):
        st.error(run["error"])

    st.markdown("**File integrity** — recomputes SHA-256 and compares with what was recorded at creation")
    checks = []
    if run.get("executed_script_path"):
        checks.append(("executed script", run["executed_script_path"], run.get("script_sha256") or ""))
    if run.get("json_path"):
        checks.append(("result JSON", run["json_path"], run.get("json_sha256") or ""))
    checks += [(f"formatted ({fid})", o["path"], o.get("sha256", "")) for fid, o in outputs.items()]
    if st.button("Verify files", type="primary", disabled=not checks):
        rows = [{"File": kind, "Path": path, "Recorded sha256": sha, "Status": audit.verify_file(path, sha)}
                for kind, path, sha in checks]
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        bad = [r for r in rows if r["Status"] != "match"]
        (st.warning if bad else st.success)(f"{len(rows) - len(bad)}/{len(rows)} file(s) unchanged since creation.")
        audit.log("integrity.checked", session_id=st.session_state.get("session_id", ""), target=f"run #{run['id']}",
                  status="ok" if not bad else "mismatch", details={"results": rows})


def render() -> None:
    st.markdown("### Audit")
    tab_events, tab_trace = st.tabs(["Audit trail", "Run traceability"])
    with tab_events:
        _render_events()
    with tab_trace:
        _render_trace()
