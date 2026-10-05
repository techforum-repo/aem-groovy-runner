from __future__ import annotations

"""Discovers the Groovy scripts the app can run.

    scripts/<script-id>/script.groovy   the script (reads inputs via __CONFIG_B64__)
    scripts/<script-id>/manifest.json   name, description, inputs, formatters
    scripts/<anything>.groovy           a plain script: listed with no inputs

Scripts know nothing about formatting: which formatters apply to a script
is a separate link (formatter_links.py / formatter_links.json).

Re-scanned on every page render, so a script dropped into the folder shows
up without restarting the app.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT
from . import readonly
from .groovy_script import PLACEHOLDER
from .utils import normalize_jcr_path, slug_for_path

SCRIPTS_DIR = PROJECT_ROOT / "scripts"
INPUT_TYPES = ("text", "path", "path_list", "string_list", "bool", "number", "select")
LIST_TYPES = ("path_list", "string_list")


@dataclass(frozen=True)
class InputDef:
    key: str
    label: str
    type: str = "text"
    default: Any = None
    required: bool = False
    iterate: bool = False
    advanced: bool = False
    help: str = ""
    placeholder: str = ""
    options: tuple[str, ...] = ()
    must_start_with: str = ""

    def empty_value(self) -> Any:
        if self.default is not None:
            return self.default
        return {"bool": False, "number": 0.0, "path_list": [], "string_list": [],
                "select": self.options[0] if self.options else ""}.get(self.type, "")


BATCH_KINDS = ("page", "folder")


@dataclass(frozen=True)
class BatchDef:
    """Batching is done by the app, never by the script: discover the parts
    under each entered path, then run the unchanged script once per part,
    setting `input` (the iterate input) to that part. A part that must cover
    only the content sitting directly at a path (assets directly in a folder,
    a page's own row) is run with its children added to `exclude_input`, the
    script's own excluded-paths input. `include_root_input` (optional, a bool
    input) says whether the entered root's own content is wanted: the app
    forces it on for the parts, which are roots only internally."""
    input: str
    kind: str  # "page" (child pages, e.g. sites) | "folder" (child folders, e.g. DAM folders)
    exclude_input: str
    label: str = ""
    include_root_input: str = ""
    # Inputs measured from the root (a depth, a row cap...) mean something different
    # per part, so Batch is refused while any of them is set (non-empty / non-zero).
    not_with: tuple[str, ...] = ()
    # Rows that are already totals (e.g. counts per type) are merged across parts:
    # rows with the same `group_by` values have their `sum` columns added up.
    merge_rows: "MergeRows | None" = None


@dataclass(frozen=True)
class MergeRows:
    group_by: tuple[str, ...]
    sum: tuple[str, ...]


@dataclass(frozen=True)
class SystemExcludes:
    """Manifest "systemExcludes": AEM system areas skipped on every run, batch or
    not. Merged into the script's own exclusion input (`input`, a path_list) at
    run time, and used as discovery skips when batching."""
    input: str
    paths: tuple[str, ...] = ()


@dataclass
class ScriptDef:
    id: str
    name: str
    description: str
    script_path: Path
    inputs: list[InputDef] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    batch: BatchDef | None = None
    system_excludes: SystemExcludes | None = None
    # Only kept to seed formatter_links.json the first time (migration); not used otherwise.
    legacy_manifest_formatters: list[str] = field(default_factory=list)

    @property
    def iterate_input(self) -> InputDef | None:
        return next((i for i in self.inputs if i.iterate), None)

    def template(self) -> str:
        return self.script_path.read_text(encoding="utf-8")


def _parse_input(raw: dict[str, Any], problems: list[str]) -> InputDef | None:
    key = str(raw.get("key") or "").strip()
    if not key:
        problems.append("An input is missing its 'key'")
        return None
    kind = str(raw.get("type") or "text")
    if kind not in INPUT_TYPES:
        problems.append(f"Input '{key}' has unknown type '{kind}' (allowed: {', '.join(INPUT_TYPES)})")
        kind = "text"
    iterate = bool(raw.get("iterate"))
    if iterate and kind not in LIST_TYPES:
        problems.append(f"Input '{key}' has iterate=true but isn't a list type")
        iterate = False
    return InputDef(
        key=key, label=str(raw.get("label") or key), type=kind, default=raw.get("default"),
        required=bool(raw.get("required")), iterate=iterate, advanced=bool(raw.get("advanced")),
        help=str(raw.get("help") or ""), placeholder=str(raw.get("placeholder") or ""),
        options=tuple(str(o) for o in raw.get("options") or ()), must_start_with=str(raw.get("must_start_with") or ""),
    )


def _parse_batch(raw: Any, inputs: list[InputDef], system_input: str | None,
                 problems: list[str]) -> BatchDef | None:
    """Every script whose iterate input is a path list can be batched; the
    manifest's "batch" section is only needed to override what's inferred:
    kind (folder under /content/dam, else page), and the exclusion input
    (systemExcludes' input, else the one other path_list input whose key or
    label says it excludes paths; never a guess, since a wrong pick would make
    a "direct items" part cover its whole subtree and duplicate rows).
    "batch": false switches batching off for a script."""
    if raw is False:
        return None
    raw = raw or {}
    if not isinstance(raw, dict):
        problems.append('"batch" must be an object or false')
        return None
    explicit = bool(raw)
    key = raw.get("input") or next((i.key for i in inputs if i.iterate and i.type == "path_list"), None)
    target = next((i for i in inputs if i.key == key), None)
    if target is None or not target.iterate or target.type != "path_list":
        if explicit:
            problems.append(f'"batch.input" must name a path_list input with iterate=true (got {raw.get("input")!r})')
        return None
    kind = str(raw.get("kind") or ("folder" if under(target.must_start_with or "/", "/content/dam") else "page"))
    if kind not in BATCH_KINDS:
        problems.append(f'"batch.kind" must be one of {", ".join(BATCH_KINDS)} (got {kind!r})')
        return None
    others = [i for i in inputs if i.type == "path_list" and i.key != target.key]
    named = [i.key for i in others if "exclud" in f"{i.key} {i.label}".lower()]
    exclude = raw.get("excludeInput") or system_input or (named[0] if len(named) == 1 else None)
    if exclude not in [i.key for i in others]:
        if explicit:
            problems.append('"batch.excludeInput" must name the script\'s excluded-paths input (a path_list): batching '
                            "needs it to cover content sitting directly in a folder above the split")
        return None
    root_input = str(raw.get("includeRootInput") or "")
    if root_input and not any(i.key == root_input and i.type == "bool" for i in inputs):
        problems.append(f'"batch.includeRootInput" must name a bool input (got {root_input!r})')
        root_input = ""
    keys = {i.key for i in inputs}
    not_with = tuple(str(k) for k in raw.get("notWith") or [])
    if unknown := [k for k in not_with if k not in keys]:
        problems.append(f'"batch.notWith" names unknown inputs: {", ".join(unknown)}')
        not_with = tuple(k for k in not_with if k in keys)
    merge = None
    if raw.get("mergeRows"):
        m = raw["mergeRows"]
        if not isinstance(m, dict) or not m.get("sum"):
            problems.append('"batch.mergeRows" needs "sum" (columns to add up) and optionally "groupBy"')
        else:
            merge = MergeRows(group_by=tuple(map(str, m.get("groupBy") or [])), sum=tuple(map(str, m["sum"])))
    return BatchDef(input=target.key, kind=kind, exclude_input=exclude, label=str(raw.get("label") or ""),
                    include_root_input=root_input, not_with=not_with, merge_rows=merge)


def _parse_system_excludes(raw: Any, inputs: list[InputDef], problems: list[str]) -> SystemExcludes | None:
    if not raw:
        return None
    if not isinstance(raw, dict):
        problems.append('"systemExcludes" must be an object')
        return None
    target = next((i for i in inputs if i.key == raw.get("input")), None)
    if target is None or target.type != "path_list":
        problems.append(f'"systemExcludes.input" must name a path_list input (got {raw.get("input")!r})')
        return None
    return SystemExcludes(input=target.key, paths=tuple(normalize_jcr_path(str(p)) for p in raw.get("paths") or []))


def _load_folder(folder: Path) -> ScriptDef:
    problems: list[str] = []
    manifest: dict[str, Any] = {}
    manifest_path = folder / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"manifest.json is not valid JSON: {exc}")
    inputs = [i for i in (_parse_input(r, problems) for r in manifest.get("inputs") or []) if i]
    if sum(i.iterate for i in inputs) > 1:
        problems.append("Only one input may have iterate=true")
    if len({i.key for i in inputs}) != len(inputs):
        problems.append("Duplicate input keys")
    manifest_formatters = [str(f) for f in manifest.get("formatters") or []]
    system_excludes = _parse_system_excludes(manifest.get("systemExcludes"), inputs, problems)
    batch = _parse_batch(manifest.get("batch"), inputs, system_excludes.input if system_excludes else None, problems)
    script = ScriptDef(
        id=folder.name, name=str(manifest.get("name") or folder.name), description=str(manifest.get("description") or ""),
        script_path=folder / "script.groovy", inputs=inputs, problems=problems,
        legacy_manifest_formatters=manifest_formatters, batch=batch, system_excludes=system_excludes,
    )
    if not script.script_path.exists():
        problems.append("script.groovy is missing")
    else:
        if inputs and PLACEHOLDER not in script.template():
            problems.append(f"Inputs are declared but script.groovy has no {PLACEHOLDER} placeholder")
        problems.extend(_readonly_problems(script.script_path))
    return script


def _readonly_problems(path: Path) -> list[str]:
    return [f"Read-only check, line {v.line}: {v.message}" for v in readonly.check(path.read_text(encoding="utf-8"))]


def discover(scripts_dir: Path = SCRIPTS_DIR) -> list[ScriptDef]:
    if not scripts_dir.exists():
        return []
    found: list[ScriptDef] = []
    for entry in sorted(scripts_dir.iterdir()):
        if entry.name.startswith("."):
            continue
        if entry.is_dir() and ((entry / "script.groovy").exists() or (entry / "manifest.json").exists()):
            found.append(_load_folder(entry))
        elif entry.is_file() and entry.suffix == ".groovy":
            found.append(ScriptDef(id=entry.stem, name=entry.stem, description="Plain script (no manifest.json).",
                                   script_path=entry, problems=_readonly_problems(entry)))
    return sorted(found, key=lambda s: s.name.lower())


def normalize_value(inp: InputDef, value: Any) -> Any:
    if inp.type == "path":
        return normalize_jcr_path(str(value or "")) if str(value or "").strip() else ""
    if inp.type == "path_list":
        return [normalize_jcr_path(v) for v in value or [] if str(v).strip()]
    if inp.type == "string_list":
        return [str(v).strip() for v in value or [] if str(v).strip()]
    if inp.type == "bool":
        return bool(value)
    if inp.type == "number":
        return float(value or 0)
    return str(value or "").strip() if inp.type == "text" else value


def under(path: str, prefix: str) -> bool:
    """Path-segment aware: /content/dam/x is under /content/dam, /content/damage is not."""
    prefix = prefix.rstrip("/")
    return path == prefix or path.startswith(prefix + "/")


def validate(script: ScriptDef, values: dict[str, Any]) -> list[str]:
    errors = list(script.problems)
    for inp in script.inputs:
        value = normalize_value(inp, values.get(inp.key, inp.empty_value()))
        if inp.required and value in ("", [], None):
            errors.append(f"“{inp.label}” is required")
        if inp.must_start_with:
            items = value if isinstance(value, list) else [value] if value else []
            bad = [v for v in items if not under(str(v), inp.must_start_with)]
            if bad:
                errors.append(f"“{inp.label}”: must start with {inp.must_start_with}: " + ", ".join(bad))
    return errors


def batch_conflicts(script: ScriptDef, values: dict[str, Any]) -> list[str]:
    """Inputs set to something Batch can't preserve (see BatchDef.not_with)."""
    if script.batch is None:
        return []
    by_key = {i.key: i for i in script.inputs}
    bad = [by_key[k].label for k in script.batch.not_with
           if k in by_key and normalize_value(by_key[k], values.get(k, by_key[k].empty_value())) not in ("", [], 0, 0.0, None, False)]
    return [f"Batch can't keep “{label}” exact (it's counted from the entered path, and each part has its own root): "
            "clear it, or untick Batch" for label in bad]


def validate_skips(script: ScriptDef, skips: list[str]) -> list[str]:
    """System-area skips must sit under the same prefix as the script's main
    path input (e.g. /content/dam for the DAM reports)."""
    main = next((i for i in script.inputs if i.iterate), None)
    prefix = main.must_start_with if main else ""
    bad = [p for p in skips if prefix and not under(normalize_jcr_path(p), prefix)]
    return [f"Skipped system areas must start with {prefix}: " + ", ".join(bad)] if bad else []


def with_skips(script: ScriptDef, values: dict[str, Any], skips: list[str]) -> dict[str, Any]:
    """`values` with the system-area skips merged into the script's own
    exclusion input, so they apply to every run, batch or not."""
    if script.system_excludes is None or not skips:
        return values
    key = script.system_excludes.input
    merged = list(dict.fromkeys([*(values.get(key) or []), *(normalize_jcr_path(p) for p in skips)]))
    return {**values, key: merged}


def expand_runs(script: ScriptDef, values: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """[(label, file slug, config)] — one per line of the iterate input,
    with that input set to the single value; otherwise exactly one run."""
    config = {inp.key: normalize_value(inp, values.get(inp.key, inp.empty_value())) for inp in script.inputs}
    it = script.iterate_input
    if it is None:
        return [(script.name, script.id, config)]
    runs = []
    for item in config[it.key]:
        slug = slug_for_path(item) if it.type == "path_list" else slug_for_path("/" + item)
        runs.append((item, slug, {**config, it.key: item}))
    return runs
