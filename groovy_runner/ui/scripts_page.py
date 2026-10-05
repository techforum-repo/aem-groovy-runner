from __future__ import annotations

import pandas as pd
import streamlit as st

from .. import readonly
from ..groovy_script import JSON_END, JSON_START
from ..config import PROJECT_ROOT
from ..scripts_registry import SCRIPTS_DIR, discover
from ..utils import display_path

SAMPLES_DIR = PROJECT_ROOT / "examples"
# Tab label -> sample folder, shown under "Adding a script"
SAMPLES = {"Asset script": SAMPLES_DIR / "asset-sample", "Page script": SAMPLES_DIR / "page-sample"}

_HOW_TO_INTRO = f"""
Create a folder under `{display_path(SCRIPTS_DIR)}/` with two files, `script.groovy` and `manifest.json`. It shows up here
on the next page refresh. The quickest start is to copy one of the samples below (in `{display_path(SAMPLES_DIR)}/`)
and change its report logic: one for DAM assets, one for pages. Both already work with Batch.
"""

_HOW_TO_DETAILS = f"""
**Inputs** come from `manifest.json` and drive the form. Types: `text`, `path`, `path_list`, `string_list`, `bool`,
`number`, `select` (with `options`). `iterate: true` on a list input means one run (and one output file) per line.
Other keys: `required`, `default`, `advanced`, `help`, `placeholder`, `must_start_with`.

**Output**: one JSON document between `{JSON_START}` and `{JSON_END}`. `rows` become the result file (the keys of
each row are the Excel columns); other keys show as run details; `{{"error": "..."}}` fails the run.

**Formatting** isn't part of a script: link formatters to it on the **Formatters** page (it shows as
"not linked" until you do).

**Batch** (for large paths) is done by the app, so the script has no batching code. Batch is offered when:

1. a `path_list` input has `"iterate": true`: one path per run (`rootPath` in the sample);
2. there's an excluded-paths input: a `path_list` whose key or label contains "exclude", or the one named in
   `systemExcludes` (`excludedPaths` in the sample);
3. the script skips the **whole subtree** under each excluded path (`isExcluded` in the sample).

The app may add excluded paths itself, to cover only the content sitting directly in a folder. That's why
rule 3 matters: if the script skipped only the folder itself and not what's under it, rows would be duplicated.

Optional, under `"batch"` in the manifest, only when they apply:

| Setting | When | Example |
|---|---|---|
| `false` (instead of an object) | never offer Batch for this script | |
| `"notWith": [...]` | options counted from the entered path, which mean something else per part: Batch is refused while set | `maxDepth` |
| `"mergeRows": {{"groupBy": [...], "sum": [...]}}` | rows are totals, so parts are added up | counts per type |
| `"includeRootInput": "..."` | a bool input for "include the root itself" | `includeRoot` |
| `"kind": "page"` or `"folder"` | override the guess (`/content/dam` = folders, else pages) | |

A plain `.groovy` file without a manifest also works: it has no inputs, and its JSON output is found even
without markers (the first line starting with `[` or `{{`).
"""


def _render_how_to() -> None:
    st.markdown(_HOW_TO_INTRO)
    for tab, folder in zip(st.tabs(list(SAMPLES)), SAMPLES.values()):
        with tab:
            st.caption(f"`{display_path(folder)}/`")
            for name, language in (("script.groovy", "groovy"), ("manifest.json", "json")):
                path = folder / name
                st.markdown(f"**`{name}`**")
                if path.exists():
                    st.code(path.read_text(encoding="utf-8"), language=language)
                else:
                    st.caption(f"Sample not found at `{display_path(path)}`.")
    st.markdown(_HOW_TO_DETAILS)


def render() -> None:
    st.markdown("### Scripts")
    scripts = discover()
    st.caption(f"{len(scripts)} script(s) in `{display_path(SCRIPTS_DIR)}/`. Re-scanned on every refresh.")
    st.info(
        "🔒 **Read-only enforcement** applies to every script, with no setting to turn it off. "
        "(1) A static check blocks write APIs and escape hatches (reflection, dynamic calls, evaluate, "
        "shell/file/network, privileged services) before anything is sent. "
        "(2) A runtime guard injected into the script makes `session.save()`, `resourceResolver.commit()` "
        "and `pageManager` writes throw. "
        "(3) Scripts run as **you**, so AEM itself only blocks what your account can't do. If your account "
        "can write, (1) and (2) are what keep this tool read-only."
    )
    for script in scripts:
        status = "⚠️" if script.problems else "✅"
        with st.expander(f"{status} {script.name}  ·  `{script.id}`"):
            if script.description:
                st.write(script.description)
            violations = readonly.check(script.template()) if script.script_path.exists() else []
            if violations:
                st.error(f"🔒 Read-only check failed: {len(violations)} finding(s). This script can't be run.")
                st.dataframe(pd.DataFrame([{"Line": v.line, "Rule": v.rule, "Finding": v.message, "Code": v.snippet}
                                           for v in violations]), width="stretch", hide_index=True)
            elif script.script_path.exists():
                st.success("🔒 Read-only check passed")
            for problem in (p for p in script.problems if not p.startswith("Read-only check")):
                st.error(problem)
            if script.inputs:
                st.dataframe(pd.DataFrame([{
                    "Key": i.key, "Label": i.label, "Type": i.type, "Required": i.required,
                    "One run per line": i.iterate, "Advanced": i.advanced,
                    "Default": "" if i.default is None else str(i.default),
                } for i in script.inputs]), width="stretch", hide_index=True)
            else:
                st.caption("No inputs.")
            if script.batch:
                b = script.batch
                st.caption(f"Batch: available ({'pages' if b.kind == 'page' else 'folders'}; parts' exclusions go "
                           f"into `{b.exclude_input}`"
                           + (f"; not with {', '.join(f'`{k}`' for k in b.not_with)}" if b.not_with else "")
                           + ("; totals merged across parts" if b.merge_rows else "") + ")")
            elif script.iterate_input and script.iterate_input.type == "path_list":
                st.caption("Batch: not available (no excluded-paths input to cover a folder's direct content; "
                           "see “Adding a script” below)")
            if script.script_path.exists():
                st.code(script.template(), language="groovy")

    st.caption("Which formatters apply to which script is managed separately, on the **Formatters** page.")

    st.markdown("#### Adding a script")
    _render_how_to()
