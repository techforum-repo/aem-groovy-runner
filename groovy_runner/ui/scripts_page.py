from __future__ import annotations

import pandas as pd
import streamlit as st

from .. import readonly
from ..groovy_script import JSON_END, JSON_START
from ..scripts_registry import SCRIPTS_DIR, discover

_HOW_TO = f'''
Create a folder under `{SCRIPTS_DIR}` with two files. It shows up here on the next page refresh.

**`script.groovy`**: read inputs from `CONFIG` and print one JSON result between the markers:

```groovy
import groovy.json.JsonOutput
import groovy.json.JsonSlurper

def CONFIG = new JsonSlurper().parseText(new String("__CONFIG_B64__".decodeBase64(), "UTF-8"))
def rows = []
// ... use CONFIG.rootPath etc., add maps to rows ...
println "{JSON_START}"
println JsonOutput.toJson([rows: rows])   // extra keys become run details; {{error: "..."}} fails the run
println "{JSON_END}"
```

**`manifest.json`**: name, description and the inputs that drive the form. Formatting isn't part of a script:
link formatters to it on the **Formatters** page (it shows as "not linked" until you do):

```json
{{
  "name": "My Script",
  "description": "What it does",
  "inputs": [
    {{"key": "rootPath", "label": "Root paths", "type": "path_list", "iterate": true, "required": true}},
    {{"key": "includeDrafts", "label": "Include drafts", "type": "bool", "default": false, "advanced": true}}
  ]
}}
```

Input types: `text`, `path`, `path_list`, `string_list`, `bool`, `number`, `select` (with `options`).
`iterate: true` on a list input means one run (and one output file) per line.
A plain `.groovy` file without a manifest also works: it has no inputs, and its JSON output is found
even without markers (the first line starting with `[` or `{{`).
'''


def render() -> None:
    st.markdown("### Scripts")
    scripts = discover()
    st.caption(f"{len(scripts)} script(s) in `{SCRIPTS_DIR}`. Re-scanned on every refresh.")
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
            if script.script_path.exists():
                st.code(script.template(), language="groovy")

    st.caption("Which formatters apply to which script is managed separately, on the **Formatters** page.")

    st.markdown("#### Adding a script")
    st.markdown(_HOW_TO)
