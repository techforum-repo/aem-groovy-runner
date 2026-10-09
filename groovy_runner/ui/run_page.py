from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from .. import auth, database, formatter_links, formatters, jobs, readonly, runner, settings_store
from ..groovy_script import build_script
from ..logging_setup import get_logger
from ..scripts_registry import (InputDef, ScriptDef, batch_conflicts, discover, expand_runs, validate, validate_skips,
                                with_skips)
from ..utils import slug_for_path, split_lines, split_paths
from .shared import (active_environment, audit_event, download_button, get_client, is_auth_rejection, is_mock,
                     multi_download_button, render_friendly_error, sign_out_rejected)

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
             only_labels: list[str] | None = None, batch: dict[str, Any] | None = None,
             lineage: list[str] | None = None) -> None:
    """Start the batch in the background; the progress panel takes it from here."""
    total = len([r for r in expand_runs(script, values) if only_labels is None or r[0] in only_labels])
    # A batch Retry rebuilds each entered path's combined file from the earlier successes of THE SAME batch
    # (and its earlier retries) plus the retried roots. A fresh run never borrows results from other runs.
    prior = ([f for f in st.session_state["session_files"] if f.ok and f.batch_id in lineage]
             if batch and lineage else None)
    client = get_client()
    job = jobs.start(
        client, script, values, session_id=st.session_state["session_id"], formatter_id=formatter_id,
        only_labels=only_labels, total=total, batch=batch, prior_results=prior, lineage=lineage)
    job.environment = _env_name(client)
    st.session_state["run_job"] = job


def _env_name(client: Any = None) -> str:
    """The environment a run goes to ("" in mock mode)."""
    if is_mock():
        return ""
    env = getattr(client, "environment", None) if client is not None else active_environment()
    return env.name if env is not None else ""


def _skips(script: ScriptDef) -> list[str]:
    """The system areas to skip for this run (empty when switched off)."""
    if script.system_excludes is None or not st.session_state.get(f"skip_on:{script.id}", True):
        return []
    return split_paths(st.session_state.get(f"skip_list:{script.id}", ""))


def _render_skip_controls(script: ScriptDef) -> None:
    se = script.system_excludes
    if se is None:
        return
    st.session_state.setdefault(f"skip_list:{script.id}", "\n".join(se.paths))
    count = len(split_paths(st.session_state[f"skip_list:{script.id}"]))
    on = st.checkbox(f"Skip AEM system areas ({count})", value=True, key=f"skip_on:{script.id}",
                     help="Applied to every run, batch or not: merged into this script's excluded paths, so these "
                          "areas aren't queried or walked. With Batch, they're also skipped during discovery.")
    if on:
        with st.expander("Edit the skipped system areas"):
            st.text_area("One per line", key=f"skip_list:{script.id}", height=150, label_visibility="collapsed")
            if st.button("Restore defaults", key=f"skip_reset:{script.id}"):
                st.session_state[f"skip_list:{script.id}"] = "\n".join(se.paths)
                st.rerun()


def _batch_settings(script: ScriptDef, values: dict[str, Any]) -> dict[str, Any] | None:
    """The batch options, or None when batching is off. Discovery skips = the
    script's excluded paths for this run (user's + system areas, if on)."""
    if script.batch is None or not st.session_state.get(f"batch_on:{script.id}"):
        return None
    excluded = values.get(script.batch.exclude_input, [])
    return {"excludes": list(excluded), "levels": int(st.session_state.get(f"batch_levels:{script.id}", 1) or 1)}


def _render_batch_controls(script: ScriptDef) -> None:
    b = script.batch
    if b is None:
        return
    what = "site / page roots" if b.kind == "page" else "top-level folders"
    on = st.checkbox(b.label or f"Batch: run each of the {what} under the entered path(s) as its own run",
                     key=f"batch_on:{script.id}",
                     help="For whole-repository runs (e.g. /content or /content/dam): the roots are discovered first "
                          "with one small read-only request, then the script runs once per root: small requests, live "
                          "progress, cancel and retry per part. Everything under the entered path is included, and "
                          "you still get ONE file per entered path: the parts are combined automatically (rebuilt "
                          "after a Retry). Excluded paths and skipped "
                          "system areas are skipped during discovery too.")
    if on:
        st.number_input("Levels below the entered path", min_value=1, max_value=3, value=1, step=1,
                        key=f"batch_levels:{script.id}",
                        help="Only how the work is split into smaller requests; everything under the entered "
                             "path is always included, and you still get one file per entered path. 1 = one request "
                             "per direct child (each site under /content, each top-level DAM folder). 2 = one per "
                             "child of those, for when one top-level folder is itself too big. Assets sitting "
                             "directly in a folder above that level (or that page's own row) get a small request "
                             "of their own.")


def _job_active() -> bool:
    job = st.session_state.get("run_job")
    return job is not None and not job.done


def _request_cancel() -> None:
    job = st.session_state.get("run_job")
    if job is not None and not job.done and not job.cancel.is_set():
        job.cancel.set()
        audit_event("run.batch_cancel_requested", target=job.script_id,
                    details={"finished": job.finished, "total": job.total, "in_flight": job.current})


def _script_kind(script_id: str) -> str:
    script = next((s for s in discover() if s.id == script_id), None)
    return script.batch.kind if script and script.batch else ""


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
        skipped = (f"; skipped {len(d.skipped)}: " + ", ".join(f"`{p}`" for p in d.skipped[:8])
                   + ("…" if len(d.skipped) > 8 else "")) if d.skipped else ""
        if not d.labels:
            kind = "pages" if job.batch and _script_kind(job.script_id) == "page" else "folders or assets"
            messages.append(("warning", f"Nothing to run under `{d.root}`: no {kind} found there{skipped}. Check the "
                                        "path, or run it without Batch."))
            continue
        messages.append(("info", f"`{d.root}` was split into {len(d.labels)} part(s) for processing{skipped}"))
    st.session_state["session_files"].extend(job.results)
    outputs = st.session_state.setdefault("combined_outputs", {})
    origin = next((f.origin for f in job.results if f.origin), "")
    if job.batch is not None:
        if job.formatter_id is None and any(not d.error for d in job.discoveries):
            messages.append(("info", "Formatter “None (JSON only)” was chosen, so no combined Excel file was made; "
                                     "use Format below to make one file per entered path."))
    for root, path in job.combined:  # per entered path AND batch: a Retry's rebuilt file replaces its own batch's
        outputs[(job.script_id, root, origin)] = path
    batch_id = job.results[0].batch_id if job.results else ""
    st.session_state["last_run"] = {"script_id": job.script_id, "values": job.values, "formatter_id": job.formatter_id,
                                    "batch": job.batch, "batch_id": batch_id, "environment": job.environment,
                                    "lineage": [*job.lineage, batch_id] if job.batch else []}
    st.session_state["format_selection"] = list(dict.fromkeys(
        key for f in job.results if f.ok
        for key in ([f"g{f.origin}:{f.script_id}:{g}" for g in f.groups] or [f"r{f.run_id}"])))
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
        elif job.current.startswith("combining"):
            text = job.current[0].upper() + job.current[1:] + "…"
        else:
            text = f"Running {min(job.finished + 1, job.total)}/{job.total}: {job.current or 'starting'}"
        where = f" on {job.environment}" if job.environment else ""
        st.progress(fraction, text=f"{job.script_name}{where}: {text}  ·  {job.elapsed:.0f}s")
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

@dataclass
class _Entry:
    """One line of the results: a plain run, or ONE entered path of a batch
    (its parts are internal: they're combined into that path's file)."""
    key: str
    script_id: str
    script_name: str
    label: str
    run: str
    parts: list[runner.GeneratedFile]
    group: str = ""  # the entered path, for a batch

    @property
    def ok_parts(self) -> list[runner.GeneratedFile]:
        return [f for f in self.parts if f.ok]

    @property
    def live_parts(self) -> list[runner.GeneratedFile]:
        """Without a failed discovery that a later Retry already redid."""
        redone = any(not f.label.endswith("(discovery)") for f in self.parts)
        return [f for f in self.parts if not (redone and f.label.endswith("(discovery)"))]

    def outputs(self) -> list[str]:
        if self.group:
            path = st.session_state.get("combined_outputs", {}).get((self.script_id, self.group, self.run))
            return [path] if path and Path(path).exists() else []
        return [p for p in self.parts[0].outputs.values() if Path(p).exists()]


def _entries(files: list[runner.GeneratedFile]) -> list[_Entry]:
    entries: list[_Entry] = []
    groups = runner.batch_groups(files)
    seen: set[tuple[str, str, str]] = set()
    for f in files:
        if not f.groups:
            entries.append(_Entry(f"r{f.run_id}", f.script_id, f.script_name, f.label, f.batch_id, [f]))
        for group in f.groups:
            if (key := (f.script_id, group, f.origin)) not in seen:
                seen.add(key)
                entries.append(_Entry(f"g{f.origin}:{f.script_id}:{group}", f.script_id, f.script_name, group,
                                      f.origin, groups[key], group=group))
    return entries


def _status(entry: _Entry) -> str:
    parts = entry.live_parts
    ok = len([f for f in parts if f.ok])
    if ok == len(parts):
        return "✅"
    if any(f.status == "cancelled" for f in parts) and ok + len([f for f in parts if f.status == "cancelled"]) == len(parts):
        return f"⏹ Cancelled ({ok}/{len(parts)} parts done)" if entry.group else "⏹ Cancelled"
    return f"⚠️ {ok}/{len(parts)} parts" if entry.group and ok else "❌"


def _entry_label(e: _Entry) -> str:
    return f"{e.script_name} · {e.label}" + (" (batch)" if e.group else "") + f" · {e.run}"


def _merge(e: _Entry) -> Any:
    script = next((s for s in discover() if s.id == e.script_id), None)
    return script.batch.merge_rows if e.group and script and script.batch else None


def _format_entry(e: _Entry, formatter_id: str) -> str:
    if not e.group:
        return runner.format_file(e.parts[0], formatter_id)
    path = runner.format_combined(e.ok_parts, formatter_id, st.session_state["session_id"], min_files=1,
                                  name=slug_for_path(e.group), merge=_merge(e))
    st.session_state.setdefault("combined_outputs", {})[(e.script_id, e.group, e.run)] = path
    return path


def _entry_data(e: _Entry) -> Any:
    """Exactly the rows Format would write (same checks); raises ValueError."""
    return e.parts[0].load() if not e.group else runner.load_rows(e.ok_parts, _merge(e))


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
    # A Retry goes to the environment the run went to, never to whichever is selected now.
    other_env = (last or {}).get("environment", "") != _env_name()
    if retryable and other_env:
        retry_label += f" on {last.get('environment') or 'mock'}"
        st.caption(f"⚠️ These runs went to **{last.get('environment') or 'mock'}**: switch to it in the sidebar to retry.")
    if retryable and last["script_id"] in scripts and st.button(retry_label, disabled=_job_active() or other_env):
        _execute(scripts[last["script_id"]], last["values"], formatter_id=last.get("formatter_id"),
                 only_labels=None if discovery_failed else [f.label for f in retryable], batch=last.get("batch"),
                 lineage=last.get("lineage"))
        st.rerun()

    entries = _entries(files)
    st.dataframe(pd.DataFrame([{
        "Script": e.script_name, "Input": e.label + (f" (batch, {len(e.live_parts)} parts)" if e.group else ""),
        "Run": e.run, "Status": _status(e),
        "Rows": sum(f.row_count or 0 for f in e.ok_parts) if e.ok_parts else None,
        "AEM run time": e.parts[0].running_time if not e.group else "",
        "Details": ", ".join(f"{k}={v}" for k, v in e.parts[0].meta.items() if not isinstance(v, (list, dict)))
                   if not e.group else "",
        "Output": ", ".join(Path(p).name for p in e.outputs()) or "— (JSON only)",
        "AEM user": e.parts[0].aem_user,
    } for e in reversed(entries)]), width="stretch", hide_index=True)
    batch_parts = [(e.group, f) for e in entries if e.group for f in e.parts]
    if batch_parts:
        with st.expander(f"Batch parts ({len(batch_parts)}): how the batched paths were split into requests"):
            st.caption("Internal to batching: each entered path's parts are combined into that path's one file. "
                       "“(direct items)” = assets sitting directly in that folder, or that page's own row.")
            st.dataframe(pd.DataFrame([{
                "Entered path": group, "Part": f.label, "Status": "✅" if f.ok else "⏹" if f.status == "cancelled" else "❌",
                "Rows": f.row_count, "AEM run time": f.running_time, "Run ID": f.run_id,
            } for group, f in batch_parts]), width="stretch", hide_index=True)

    usable = [e for e in entries if e.ok_parts]
    if not usable:
        return
    by_key = {e.key: e for e in usable}
    st.session_state["format_selection"] = [k for k in st.session_state.get("format_selection", []) if k in by_key]
    st.markdown("#### Format and download")
    selected_keys = st.multiselect("Results (this session only)", list(by_key), key="format_selection",
                                   format_func=lambda k: _entry_label(by_key[k]))
    selected = [by_key[k] for k in selected_keys]

    declared = [fid for e in selected for fid in formatter_links.link_for(e.script_id, links).formatters]
    options = list(dict.fromkeys(declared + list(formatters.FORMATTERS)))
    formatter_id = st.selectbox("Formatter", options, format_func=lambda i: f"{formatters.get(i).name} — {formatters.get(i).description}"
                                + ("" if i in declared else " (not linked to this script)"))
    formatter = formatters.get(formatter_id)
    combine = st.checkbox("Combine selected into one file", key="format_combine", disabled=len(selected) < 2,
                          help="One file with the rows of all selected results. Individual files are not created by this.")
    if combine and len(selected) >= 2:
        if st.button(f"Combine {len(selected)} results into one {formatter.name} file", type="primary"):
            try:
                path = runner.format_combined([f for e in selected for f in e.ok_parts], formatter_id,
                                              st.session_state["session_id"])
                st.session_state.setdefault("combined_outputs", {})[("manual", path)] = path
                st.session_state["_format_messages"] = [("success", f"Combined {len(selected)} results into {Path(path).name}.")]
            except Exception as exc:
                st.session_state["_format_messages"] = [("warning", f"Not combined: {exc}")]
            st.rerun()
    elif st.button(f"Format {len(selected)} result(s) with {formatter.name}", type="primary", disabled=not selected):
        messages, done = [], 0
        for e in selected:
            try:
                _format_entry(e, formatter_id)
                done += 1
                if e.group and len(e.ok_parts) < len(e.live_parts):
                    messages.append(("warning", f"`{e.label}`: {len(e.live_parts) - len(e.ok_parts)} batch part(s) "
                                                "failed or were cancelled and aren't in the file. Retry them first."))
            except Exception as exc:
                messages.append(("warning", f"`{e.label}`: {exc}"))
        if done:
            messages.append(("success", f"Formatted {done} result(s) with {formatter.name}."))
        st.session_state["_format_messages"] = messages
        st.rerun()
    for kind, text in st.session_state.pop("_format_messages", []):
        (st.success if kind == "success" else st.warning)(text)

    manual = [p for k, p in st.session_state.get("combined_outputs", {}).items() if k[0] == "manual" and Path(p).exists()]
    if manual:
        st.markdown("##### Combined across results")
        for p in reversed(manual):
            download_button(f"⬇️ {Path(p).name}", lambda p=p: Path(p).read_bytes(), Path(p).name, XLSX_MIME,
                            source_paths=[p], key=f"dl_combined:{p}")

    if selected:
        excel = [p for e in selected for p in e.outputs()]
        paths = [p for e in selected for f in e.parts for p in [f.json_path, f.executed_script_path] if p] + excel
        if excel:
            multi_download_button(excel, f"⬇️ Download all {len(excel)} Excel file(s) (individually, one click)", XLSX_MIME)
        else:
            st.caption("No formatted Excel files for the selected results yet: click Format above first.")
        c1, c2 = st.columns(2)
        with c1:
            download_button("⬇️ Download selected (.zip: json + formatted + executed script)",
                            lambda paths=list(paths): runner.zip_files(paths),
                            f"groovy-runner-{st.session_state['session_id']}.zip", "application/zip",
                            source_paths=paths, width="stretch")
        with c2:
            if excel:
                one = st.selectbox("Single file", excel, format_func=lambda p: Path(p).name, label_visibility="collapsed")
                download_button(f"⬇️ {Path(one).name}", lambda one=one: Path(one).read_bytes(), Path(one).name, XLSX_MIME,
                                source_paths=[one], width="stretch")

        st.markdown("#### Preview")
        target = st.selectbox("Preview", selected, format_func=_entry_label) if len(selected) > 1 else selected[0]
        try:
            data = _entry_data(target)
        except ValueError as exc:
            st.warning(f"No preview: {exc}")
            return
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
        _render_skip_controls(script)
        _render_batch_controls(script)
        _render_save_preset(script)
    else:
        st.info("This script takes no inputs.")

    skips = _skips(script)
    errors = validate(script, _values(script)) + validate_skips(script, skips)
    if _batch_settings(script, _values(script)) is not None:
        errors += batch_conflicts(script, _values(script))
    values = with_skips(script, _values(script), skips)  # what is sent and audited
    env = None if is_mock() else active_environment()
    if not is_mock() and env is None:
        errors.append("No AEM environment: add one (name + author URL) on the Settings page")
    elif not is_mock() and not auth.is_signed_in(st.session_state.get("user_token")):
        errors.append(f"Not signed in to {env.name}: sign in with your account in the sidebar")
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
        batch = _batch_settings(script, values)
        on = f" on {env.name}" if env else ""
        count = (f"{len(runs)} path{'s' if len(runs) != 1 else ''}" if batch
                 else f"{len(runs)} run{'s' if len(runs) != 1 else ''}") + (" · mock" if is_mock() else "")
        label = f"▶️ {'Discover & run' if batch else 'Run'}{on} ({count})"
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
                 batch=_batch_settings(script, values))
        st.rerun()  # redraw with Run disabled and the progress panel showing

    if st.session_state.get("run_job") is not None:
        _render_job_panel()
    for kind, text in st.session_state.pop("_run_messages", []):
        getattr(st, kind)(text)

    _render_session_files(scripts, links)
