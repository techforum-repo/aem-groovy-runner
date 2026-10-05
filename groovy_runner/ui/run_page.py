from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from .. import auth, database, formatter_links, formatters, jobs, readonly, runner, settings_store
from ..groovy_script import build_script
from ..logging_setup import get_logger
from ..scripts_registry import InputDef, ScriptDef, discover, expand_runs, validate, validate_batch
from ..utils import split_lines, split_paths
from .shared import (audit_event, download_button, get_client, is_auth_rejection, is_mock, multi_download_button,
                     render_friendly_error, sign_out_rejected)

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# --- inputs -------------------------------------------------------------------

def _wkey(script: ScriptDef, inp: InputDef) -> str:
    return f"in:{script.id}:{inp.key}"


def _to_widget(inp: InputDef, value: Any) -> Any:
    if inp.type in ("path_list", "string_list"):
        return "\n".join(str(v) for v in value or [])
    if inp.type == "number":
        return float(value or 0)
    if inp.type == "bool":
        return bool(value)
    return "" if value is None else str(value)


def _from_widget(inp: InputDef, raw: Any) -> Any:
    if inp.type == "path_list":
        return split_paths(raw)
    if inp.type == "string_list":
        return split_lines(raw)
    return raw


def _ensure_defaults(script: ScriptDef) -> None:
    for inp in script.inputs:
        st.session_state.setdefault(_wkey(script, inp), _to_widget(inp, inp.empty_value()))


def _values(script: ScriptDef) -> dict[str, Any]:
    return {inp.key: _from_widget(inp, st.session_state[_wkey(script, inp)]) for inp in script.inputs}


def _render_input(script: ScriptDef, inp: InputDef) -> None:
    key, label, help_ = _wkey(script, inp), inp.label + (" *" if inp.required else ""), inp.help or None
    if inp.type in ("path_list", "string_list"):
        st.text_area(label, key=key, help=help_, placeholder=inp.placeholder,
                     height=120 if inp.iterate else 80)
    elif inp.type == "bool":
        st.checkbox(label, key=key, help=help_)
    elif inp.type == "number":
        st.number_input(label, key=key, help=help_)
    elif inp.type == "select":
        st.selectbox(label, inp.options, key=key, help=help_)
    else:
        st.text_input(label, key=key, help=help_, placeholder=inp.placeholder)


def _existing_preset_name(presets: dict[str, Any], name: str) -> str | None:
    """The stored name a typed name would overwrite: same name ignoring case
    and surrounding spaces, matching the database's NOCASE key."""
    wanted = name.strip().lower()
    return next((n for n in presets if n.strip().lower() == wanted), None) if wanted else None


def _render_presets(script: ScriptDef) -> None:
    presets = database.list_presets(script.id)
    choice_key, name_key = f"preset:{script.id}", f"preset_name:{script.id}"

    def load() -> None:
        chosen = st.session_state.get(choice_key) or ""
        preset = presets.get(chosen)
        for inp in script.inputs:
            if preset and inp.key in preset:
                st.session_state[_wkey(script, inp)] = _to_widget(inp, preset[inp.key])
        # Pre-fill the save box so editing inputs + saving updates this preset.
        st.session_state[name_key] = chosen

    def delete() -> None:
        chosen = st.session_state.get(choice_key)
        if not chosen:
            return
        database.delete_preset(script.id, chosen)
        audit_event("preset.deleted", target=f"{script.id}: {chosen}", details={"values": presets.get(chosen)})
        get_logger().info("Deleted preset %s/%s", script.id, chosen)
        st.session_state[choice_key] = ""
        st.session_state[name_key] = ""
        st.session_state["_preset_message"] = ("success", f"Deleted preset “{chosen}”.")

    if st.session_state.get(choice_key) not in ("", None, *presets):
        st.session_state[choice_key] = ""  # deleted elsewhere / renamed
    c1, c2 = st.columns([3, 1])
    with c1:
        st.selectbox("Preset", [""] + list(presets), key=choice_key, on_change=load,
                     format_func=lambda n: n or "— load a saved preset —")
    with c2:
        st.write("")
        st.write("")
        if st.session_state.get(choice_key):
            st.button("Delete preset", width="stretch", on_click=delete, key=f"preset_delete:{script.id}")


def _render_save_preset(script: ScriptDef) -> None:
    presets = database.list_presets(script.id)
    choice_key, name_key = f"preset:{script.id}", f"preset_name:{script.id}"

    def save() -> None:
        name = (st.session_state.get(name_key) or "").strip()
        if not name:
            return
        values = _values(script)
        existing = _existing_preset_name(presets, name)
        stored_name = existing or name  # keep the original spelling when overwriting
        database.save_preset(script.id, stored_name, values)
        if existing:
            audit_event("preset.updated", target=f"{script.id}: {stored_name}",
                        details={"previous": presets[existing], "values": values})
            message = f"Updated preset “{stored_name}”."
        else:
            audit_event("preset.saved", target=f"{script.id}: {stored_name}", details={"values": values})
            message = f"Saved new preset “{stored_name}”."
        get_logger().info("%s preset %s/%s", "Updated" if existing else "Saved", script.id, stored_name)
        st.session_state[choice_key] = stored_name
        st.session_state[name_key] = stored_name
        st.session_state["_preset_message"] = ("success", message)

    c1, c2 = st.columns([3, 1])
    with c1:
        name = st.text_input("Save inputs as preset", key=name_key, placeholder="e.g. Product Library",
                             help="Typing an existing preset's name (any capitalisation) updates that preset.")
    existing = _existing_preset_name(presets, name or "")
    with c2:
        st.write("")
        st.write("")
        st.button("Update preset" if existing else "Save new preset", width="stretch",
                  disabled=not (name or "").strip(), on_click=save, key=f"preset_save:{script.id}",
                  type="primary" if existing else "secondary")
    if existing:
        st.caption(f"“{existing}” already exists. Saving will overwrite its inputs with the current ones.")
    kind_text = st.session_state.pop("_preset_message", None)
    if kind_text:
        st.success(kind_text[1])


# --- running ------------------------------------------------------------------

NO_FORMAT = "__none__"
PREVIEW_ROWS = 1000  # the preview is a sample; the files always hold everything


def _formatter_name(formatter_id: str) -> str:
    f = formatters.FORMATTERS.get(formatter_id)
    return f.name if f else formatter_id


def _execute(script: ScriptDef, values: dict[str, Any], *, formatter_id: str | None,
             only_labels: list[str] | None = None, batch: dict[str, Any] | None = None) -> None:
    """Start the batch in the background; the progress panel takes it from here."""
    total = len([r for r in expand_runs(script, values) if only_labels is None or r[0] in only_labels])
    st.session_state["run_job"] = jobs.start(
        get_client(), script, values, session_id=st.session_state["session_id"], formatter_id=formatter_id,
        only_labels=only_labels, total=total, batch=batch)


def _batch_settings(script: ScriptDef) -> dict[str, Any] | None:
    """The batch options from the form, or None when batching is off."""
    if script.batch is None or not st.session_state.get(f"batch_on:{script.id}"):
        return None
    return {"excludes": split_paths(st.session_state.get(f"batch_excl:{script.id}", "")),
            "levels": int(st.session_state.get(f"batch_levels:{script.id}", 1) or 1)}


def _render_batch_controls(script: ScriptDef) -> None:
    b = script.batch
    if b is None:
        return
    what = "site / page roots" if b.kind == "page" else "top-level folders"
    st.session_state.setdefault(f"batch_excl:{script.id}", "\n".join(b.default_excludes))
    on = st.checkbox(b.label or f"Batch: run each of the {what} under the entered path(s) as its own run",
                     key=f"batch_on:{script.id}",
                     help="For whole-repository runs (e.g. /content or /content/dam): the roots are discovered first "
                          "with one small read-only request, then the script runs once per root: small requests, live "
                          "progress, cancel and retry per root, one file each (combine them in the Format step).")
    if on:
        c1, c2 = st.columns([3, 1])
        with c1:
            st.text_area("Skip these during discovery (defaults: system areas) — one per line",
                         key=f"batch_excl:{script.id}", height=110)
        with c2:
            st.number_input("Levels below the entered path", min_value=1, max_value=3, value=1, step=1,
                            key=f"batch_levels:{script.id}",
                            help="1 = direct children (e.g. each site under /content). 2 = one level deeper, for "
                                 "when a single top-level root is itself too big.")
        if st.button("Restore default skips", key=f"batch_reset:{script.id}"):
            st.session_state[f"batch_excl:{script.id}"] = "\n".join(b.default_excludes)
            st.rerun()


def _job_active() -> bool:
    job = st.session_state.get("run_job")
    return job is not None and not job.done


def _request_cancel() -> None:
    job = st.session_state.get("run_job")
    if job is not None and not job.done and not job.cancel.is_set():
        job.cancel.set()
        audit_event("run.batch_cancel_requested", target=job.script_id,
                    details={"finished": job.finished, "total": job.total, "in_flight": job.current})


def _finalize(job: jobs.RunJob) -> None:
    """Runs on the page's own thread once the worker is done."""
    del st.session_state["run_job"]
    messages = list(job.messages)
    if job.error is not None:
        messages.insert(0, ("error", f"The run stopped unexpectedly: {job.error}"))
    cancelled = [f for f in job.results if f.status == "cancelled"]
    if cancelled:
        done = len([f for f in job.results if f.status != "cancelled"])
        messages.insert(0, ("info", f"Cancelled: {done} of {len(job.results)} run(s) finished before the cancel; "
                                    f"{len(cancelled)} cancelled. Use “Retry” below to run the rest."))
    for d in job.discoveries:
        if d.error:
            continue
        note = f"Discovered {len(d.children)} root(s) under `{d.root}`"
        if d.skipped:
            note += f"; skipped {len(d.skipped)}: " + ", ".join(f"`{p}`" for p in d.skipped[:8]) + ("…" if len(d.skipped) > 8 else "")
        if d.loose_items:
            note += (f". ⚠️ {d.loose_items} item(s) sit directly in `{d.root}` (not in any discovered root), so the "
                     "batch doesn't cover them; run that path without batching to include them.")
        messages.append(("info", note))
    st.session_state["session_files"].extend(job.results)
    st.session_state["last_run"] = {"script_id": job.script_id, "values": job.values, "formatter_id": job.formatter_id,
                                    "batch": job.batch, "batch_id": job.results[0].batch_id if job.results else ""}
    st.session_state["format_selection"] = [f.run_id for f in job.results if f.ok]
    st.session_state["_run_messages"] = messages
    if not is_mock() and any(f.exception is not None and is_auth_rejection(f.exception) for f in job.results):
        sign_out_rejected("AEM rejected your token during the run (expired or invalid). Please sign in again.")


@st.fragment(run_every=1.0)
def _render_job_panel() -> None:
    """Live progress + Cancel. Refreshes itself every second without
    rerunning the rest of the page; hands over to a full rerun when done."""
    job: jobs.RunJob | None = st.session_state.get("run_job")
    if job is None:
        return
    if job.done:
        _finalize(job)
        st.rerun()  # full app rerun: results table, sidebar (sign-in state), messages
    with st.container(border=True):
        fraction = job.finished / job.total if job.total else 0.0
        if job.cancelling:
            text = f"Cancelling… finished {job.finished}/{job.total}"
        elif job.current.startswith("discovering"):
            text = job.current[0].upper() + job.current[1:] + "…"
        elif job.current == "formatting":
            text = f"Formatting {job.finished} result(s)…"
        else:
            text = f"Running {min(job.finished + 1, job.total)}/{job.total}: {job.current or 'starting'}"
        st.progress(fraction, text=f"{job.script_name}: {text}  ·  {job.elapsed:.0f}s")
        if job.lines:
            st.markdown("\n".join(job.lines))
        c1, c2 = st.columns([1, 3])
        with c1:
            st.button("⏹ Cancel", key="cancel_run", on_click=_request_cancel, disabled=job.cancel.is_set(),
                      width="stretch")
        with c2:
            st.caption("Cancel skips the remaining paths and stops waiting for the current one. The Groovy Console "
                       "can't stop a script already running in AEM, so that one finishes there (read-only) and its "
                       "result is discarded.")


# --- session files + formatting ----------------------------------------------

def _file_label(f: runner.GeneratedFile) -> str:
    return f"{f.script_name} · {f.label} · {f.batch_id}"


def _load_links() -> dict[str, tuple[list[str], str]]:
    try:
        return formatter_links.load()
    except ValueError as exc:
        st.error(f"Formatter links file is unreadable, so every script falls back to Generic Excel: {exc}")
        return {}


def _render_session_files(scripts: dict[str, ScriptDef], links: dict[str, tuple[list[str], str]]) -> None:
    files: list[runner.GeneratedFile] = st.session_state["session_files"]
    st.divider()
    st.markdown("### This session's results")
    if not files:
        st.caption("Nothing generated in this session yet. Formatting only works on files generated in the current session.")
        return

    last = st.session_state.get("last_run")
    retryable = [f for f in files if not f.ok and last and f.batch_id == last["batch_id"]]
    for i, f in enumerate(f for f in retryable if f.status != "cancelled"):
        render_friendly_error(f.exception or RuntimeError(f.error), key=f"err_{i}", context=f"{f.script_name} · {f.label}", retry=False)
    discovery_failed = any(f.label.endswith("(discovery)") for f in retryable)
    retry_label = ("Retry the whole batch (root discovery failed)" if discovery_failed
                   else f"Retry {len(retryable)} failed/cancelled run(s)")
    if retryable and last["script_id"] in scripts and st.button(retry_label, disabled=_job_active()):
        _execute(scripts[last["script_id"]], last["values"], formatter_id=last.get("formatter_id"),
                 only_labels=None if discovery_failed else [f.label for f in retryable], batch=last.get("batch"))
        st.rerun()

    st.dataframe(pd.DataFrame([{
        "Script": f.script_name, "Input": f.label, "Run": f.batch_id, "Status": "✅" if f.ok else "⏹ Cancelled" if f.status == "cancelled" else "❌",
        "Rows": f.row_count, "AEM run time": f.running_time,
        "Details": ", ".join(f"{k}={v}" for k, v in f.meta.items() if not isinstance(v, (list, dict))),
        "Formatter used": ", ".join(_formatter_name(fid) for fid in f.outputs) or "— (JSON only)",
        "Run ID": f.run_id, "AEM user": f.aem_user,
    } for f in reversed(files)]), width="stretch", hide_index=True)

    ok = [f for f in files if f.ok]
    if not ok:
        return
    by_id = {f.run_id: f for f in ok}
    st.session_state["format_selection"] = [i for i in st.session_state.get("format_selection", []) if i in by_id]
    st.markdown("#### Format")
    selected_ids = st.multiselect("Files (this session only)", list(by_id), key="format_selection",
                                  format_func=lambda i: _file_label(by_id[i]))
    selected = [by_id[i] for i in selected_ids]

    declared = [fid for f in selected for fid in formatter_links.link_for(f.script_id, links).formatters]
    options = list(dict.fromkeys(declared + list(formatters.FORMATTERS)))
    formatter_id = st.selectbox("Formatter", options, format_func=lambda i: f"{formatters.get(i).name} — {formatters.get(i).description}"
                                + ("" if i in declared else " (not linked to this script)"))
    formatter = formatters.get(formatter_id)
    combine = st.checkbox("Combine selected into one file", key="format_combine", disabled=len(selected) < 2,
                          help="One file with the rows of all selected results, e.g. every site of a batched report. "
                               "Individual files are not created by this.")
    if combine and len(selected) >= 2:
        if st.button(f"Combine {len(selected)} results into one {formatter.name} file", type="primary"):
            try:
                path = runner.format_combined(selected, formatter_id, st.session_state["session_id"])
                st.session_state.setdefault("combined_outputs", []).append(path)
                st.session_state["_format_messages"] = [("success", f"Combined {len(selected)} results into {Path(path).name}.")]
            except Exception as exc:
                st.session_state["_format_messages"] = [("warning", f"Not combined: {exc}")]
            st.rerun()
    elif st.button(f"Format {len(selected)} file(s) with {formatter.name}", type="primary", disabled=not selected):
        messages, done = [], 0
        for f in selected:
            try:
                runner.format_file(f, formatter_id)
                done += 1
            except Exception as exc:
                messages.append(("warning", f"`{f.label}`: {exc}"))
        if done:
            messages.append(("success", f"Formatted {done} file(s) with {formatter.name}."))
        # Rerun so the session table above reflects the new outputs.
        st.session_state["_format_messages"] = messages
        st.rerun()
    for kind, text in st.session_state.pop("_format_messages", []):
        (st.success if kind == "success" else st.warning)(text)

    combined_files = [p for p in st.session_state.get("combined_outputs", []) if Path(p).exists()]
    if combined_files:
        st.markdown("##### Combined files")
        for p in reversed(combined_files):
            download_button(f"⬇️ {Path(p).name}", lambda p=p: Path(p).read_bytes(), Path(p).name, XLSX_MIME,
                            source_paths=[p], key=f"dl_combined:{p}")

    if selected:
        paths = [p for f in selected for p in [f.json_path, f.executed_script_path, *f.outputs.values()] if p]
        excel = [p for f in selected for p in f.outputs.values() if Path(p).exists()]
        if excel:
            multi_download_button(excel, f"⬇️ Download all {len(excel)} Excel file(s) (individually, one click)", XLSX_MIME)
        else:
            st.caption("No formatted Excel files for the selected results yet: click Format above first.")
        c1, c2 = st.columns(2)
        with c1:
            download_button("⬇️ Download selected (.zip: json + formatted + executed script)",
                            lambda: runner.zip_files(paths),
                            f"groovy-runner-{st.session_state['session_id']}.zip", "application/zip",
                            source_paths=paths, width="stretch")
        with c2:
            singles = [p for f in selected for p in f.outputs.values() if Path(p).exists()]
            if singles:
                one = st.selectbox("Single file", singles, format_func=lambda p: Path(p).name, label_visibility="collapsed")
                download_button(f"⬇️ {Path(one).name}", lambda: Path(one).read_bytes(), Path(one).name, XLSX_MIME,
                                source_paths=[one], width="stretch")

        st.markdown("#### Preview")
        target = st.selectbox("Preview file", selected, format_func=_file_label) if len(selected) > 1 else selected[0]
        data = target.load()
        fits = formatter.check(data) is None
        sample = data[:PREVIEW_ROWS] if isinstance(data, list) else data
        df = (formatter if fits else formatters.get("generic-excel")).to_dataframe(sample)
        total = len(data) if isinstance(data, list) else len(df)
        shown = f"first {len(df):,} of {total:,}" if total > len(df) else f"{total:,}"
        st.caption(f"{shown} rows · columns as {formatter.name if fits else 'Generic Excel'} would write them"
                   + (" · download for the full result" if total > len(df) else ""))
        st.dataframe(df, width="stretch", hide_index=True)


# --- page ---------------------------------------------------------------------

def render() -> None:
    st.markdown("### Run")
    scripts = {s.id: s for s in discover()}
    links = _load_links()
    if not scripts:
        st.warning("No scripts found. Add one under `scripts/` — see the Scripts page.")
        return
    script_id = st.selectbox("Script", list(scripts), key="script_id", format_func=lambda i: scripts[i].name)
    script = scripts[script_id]
    _ensure_defaults(script)
    if script.description:
        st.caption(script.description)
    for problem in script.problems:
        st.error(f"Script problem: {problem}")

    if script.inputs:
        _render_presets(script)
        for inp in [i for i in script.inputs if not i.advanced]:
            _render_input(script, inp)
        advanced = [i for i in script.inputs if i.advanced]
        if advanced:
            with st.expander("Advanced options"):
                for inp in advanced:
                    _render_input(script, inp)
        _render_batch_controls(script)
        _render_save_preset(script)
    else:
        st.info("This script takes no inputs.")

    values = _values(script)
    errors = validate(script, values)
    batch_now = _batch_settings(script)
    if batch_now is not None:
        errors += validate_batch(script, batch_now["excludes"])
    if not is_mock() and not (settings_store.get("aem_author_url") and auth.is_signed_in(st.session_state.get("user_token"))):
        errors.append("Not connected to AEM: set the author URL (Settings) and sign in with your account (sidebar)")
    runs = expand_runs(script, values) if not script.problems else []
    link = formatter_links.link_for(script.id, links)
    linked = [fid for fid in link.formatters if fid in formatters.FORMATTERS]
    choice_key = f"run_formatter:{script.id}"
    options = linked + [NO_FORMAT]
    if st.session_state.get(choice_key) not in options:  # links changed since last visit
        st.session_state[choice_key] = options[0]
    c1, c2 = st.columns([1, 2])
    with c2:
        formatter_choice = st.selectbox(
            "Format results with", options, key=choice_key,
            format_func=lambda fid: "None (JSON only)" if fid == NO_FORMAT
            else _formatter_name(fid) + (" (default)" if fid == link.default else ""),
            help="Defaults to the formatter linked to this script (Formatters page). Pick another just for this run if needed.")
        if link.linked:
            st.caption("Linked formatters: " + ", ".join(_formatter_name(f) for f in linked) + " (Formatters page)")
        else:
            st.caption("⚠️ No formatter linked to this script yet: using Generic Excel. Link one on the Formatters page.")
    with c1:
        st.write("")
        batch = _batch_settings(script)
        label = (f"▶️ Discover & run ({len(runs)} path{'s' if len(runs) != 1 else ''})" if batch
                 else f"▶️ Run ({len(runs)} run{'s' if len(runs) != 1 else ''})")
        clicked = st.button(label, type="primary", disabled=bool(errors) or not runs or _job_active(), width="stretch")
    for err in errors:
        st.caption(f"⚠️ {err}")
    st.caption("Mock mode — sample data, nothing is sent to AEM." if is_mock()
               else "Runs go one after another (one Groovy Console request each) to keep load on the author low.")
    if runs and not script.problems:
        with st.expander("Script exactly as sent for the first run (inputs + read-only guard injected)"):
            text = readonly.prepare(build_script(script.template(), runs[0][2]))
            download_button("Download .groovy", text, f"{script.id}.groovy", "text/plain")
            st.code(text, language="groovy")

    if clicked and not _job_active():
        _execute(script, values, formatter_id=None if formatter_choice == NO_FORMAT else formatter_choice,
                 batch=_batch_settings(script))
        st.rerun()  # redraw with Run disabled and the progress panel showing

    if st.session_state.get("run_job") is not None:
        _render_job_panel()
    for kind, text in st.session_state.pop("_run_messages", []):
        getattr(st, kind)(text)

    _render_session_files(scripts, links)
