from __future__ import annotations

"""Run a script (once per line of its iterate input) and format results.

Each run's JSON is written to disk and tracked as a GeneratedFile. The UI
keeps the GeneratedFiles of the current browser session, and formatting
only ever takes those, never arbitrary files from disk or earlier sessions.
One run's failure never stops the rest of the batch.

Traceability: next to each result JSON the exact script text sent to AEM
is saved as <slug>.groovy, and the run row records who ran it (local actor
+ AEM identity), when, with which inputs, and SHA-256 hashes of the
template, executed script, JSON, and every formatted file — so any Excel
file can be traced back to the precise script and inputs that produced it,
and checked for later modification (Audit page).
"""

import concurrent.futures
import io
import json
import threading
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from . import audit, database, formatters, readonly
from .config import settings
from .groovy_script import build_script, parse_output, split_payload
from .logging_setup import get_logger
from .scripts_registry import MergeRows, ScriptDef, expand_runs
from .utils import harden_file_permissions, normalize_jcr_path


class ConsoleClient(Protocol):
    def run_script(self, script: str) -> Any: ...
    def identity(self) -> str: ...


DISCOVER_TEMPLATE = Path(__file__).resolve().parent / "builtin" / "discover_roots.groovy"


DIRECT_SUFFIX = " (direct items)"


def direct_label(path: str) -> str:
    """Run label of an internal direct-only run: only the content sitting directly
    at `path` (assets in the folder, or the page's own row), not its subtree."""
    return path + DIRECT_SUFFIX


@dataclass
class Discovery:
    root: str
    children: list[str] = field(default_factory=list)  # run with their whole subtree
    direct: list[str] = field(default_factory=list)  # run for their direct content only
    direct_excludes: dict[str, list[str]] = field(default_factory=dict)  # direct path -> its children to exclude
    skipped: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def labels(self) -> list[str]:
        """Every run label of this entered path, in path order (a folder's direct
        items before its subfolders)."""
        items = [(c, c) for c in self.children] + [(d, direct_label(d)) for d in self.direct]
        return [label for _, label in sorted(items)]


def discover_roots(client: "ConsoleClient", root: str, kind: str, excludes: list[str], levels: int = 1,
                   cancel: threading.Event | None = None) -> Discovery:
    """One small read-only Groovy request listing the batch roots under `root`."""
    config = {"root": normalize_jcr_path(root), "kind": kind, "levels": levels,
              "excludes": [normalize_jcr_path(e) for e in excludes if e.strip()]}
    try:
        console = _call_with_cancel(lambda: client.run_script(build_script(DISCOVER_TEMPLATE.read_text(encoding="utf-8"),
                                                                           config)), cancel)
        payload = parse_output(console.output)
    except RunCancelled:
        raise
    except Exception as exc:
        return Discovery(root=root, error=str(exc))
    if not isinstance(payload, dict) or payload.get("error"):
        return Discovery(root=root, error=str((payload or {}).get("error") or "unexpected discovery output"))
    direct = [d for d in payload.get("direct") or [] if isinstance(d, dict) and d.get("path")]
    return Discovery(root=root, children=list(payload.get("children") or []), direct=[d["path"] for d in direct],
                     direct_excludes={d["path"]: list(d.get("exclude") or []) for d in direct},
                     skipped=list(payload.get("skipped") or []))


class RunCancelled(Exception):
    """Raised inside a batch when the user cancelled it."""


# How often a cancel request is noticed while waiting on AEM.
CANCEL_POLL_SECONDS = 0.25


def _call_with_cancel(fn: Callable[[], Any], cancel: threading.Event | None) -> Any:
    """Run one Groovy Console request, but stop *waiting* for it as soon as
    `cancel` is set. The Groovy Console has no API to stop a script already
    executing in AEM, so an abandoned script still runs to completion there
    (it's read-only); its response is simply discarded here."""
    if cancel is None:
        return fn()
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="groovy-request")
    future = pool.submit(fn)
    try:
        while True:
            try:
                return future.result(timeout=CANCEL_POLL_SECONDS)
            except concurrent.futures.TimeoutError:
                if cancel.is_set():
                    raise RunCancelled("Cancelled while waiting for AEM; the script may still finish on AEM, "
                                       "but its result is discarded") from None
    finally:
        pool.shutdown(wait=False)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class GeneratedFile:
    run_id: int
    session_id: str
    script_id: str
    script_name: str
    batch_id: str
    label: str
    status: str  # "ok" | "error" | "cancelled"
    json_path: str = ""
    row_count: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    running_time: str = ""
    elapsed_seconds: float = 0.0
    error: str = ""
    exception: BaseException | None = None
    outputs: dict[str, str] = field(default_factory=dict)  # formatter id -> file path
    output_hashes: dict[str, str] = field(default_factory=dict)  # formatter id -> sha256
    json_sha256: str = ""
    script_sha256: str = ""
    executed_script_path: str = ""
    aem_user: str = ""
    # Batch mode: the entered path(s) this run is a part of (two when entered paths
    # overlap), and the id of the batch it started in (a Retry keeps it), so the
    # parts of one entered path can be grouped.
    groups: list[str] = field(default_factory=list)
    origin: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def load(self) -> Any:
        return json.loads(Path(self.json_path).read_text(encoding="utf-8"))


def session_dir(session_id: str) -> Path:
    path = settings.output_path / session_id
    path.mkdir(parents=True, exist_ok=True)
    harden_file_permissions(settings.output_path, mode=0o700)
    return path


def _batch_runs(script: ScriptDef, values: dict[str, Any],
                discoveries: list[Discovery]) -> list[tuple[str, str, dict[str, Any]]]:
    """The runs of a batch, all ordinary calls of the unchanged script: each
    discovered part with its whole subtree, plus a "direct items" part for the
    content above the split level (assets directly in the entered/intermediate
    folders, or those pages' own rows): the script runs on that path with its
    children added to the script's excluded paths. Together they cover
    everything under the entered path, each item once."""
    b = script.batch
    runs: dict[str, tuple[str, str, dict[str, Any]]] = {}

    def part(path: str, extra_excludes: list[str] | None = None) -> tuple[str, str, dict[str, Any]]:
        [(label, slug, config)] = expand_runs(script, {**values, b.input: [path]})
        if b.include_root_input:  # a part's own root is in no other run: keep it
            config[b.include_root_input] = True
        if extra_excludes:
            config[b.exclude_input] = list(dict.fromkeys([*config.get(b.exclude_input, []), *extra_excludes]))
        return label, slug, config

    for d in discoveries:
        for path in d.children:
            runs.setdefault(path, part(path))
        for path in d.direct:
            _, slug, config = part(path, d.direct_excludes.get(path, []))
            runs.setdefault(direct_label(path), (direct_label(path), slug + "-direct", config))
    order = {label: i for i, label in enumerate(label for d in discoveries for label in d.labels)}
    return sorted(runs.values(), key=lambda r: order.get(r[0], len(order)))


def run_script(
    client: ConsoleClient,
    script: ScriptDef,
    values: dict[str, Any],
    *,
    session_id: str,
    on_progress: Callable[[int, int, str, GeneratedFile | None], None] | None = None,
    only_labels: list[str] | None = None,
    cancel: threading.Event | None = None,
    batch: dict[str, Any] | None = None,
    on_discovered: Callable[[list[Discovery]], None] | None = None,
    origin: str | None = None,
) -> list[GeneratedFile]:
    """`cancel`, when set from another thread, stops the batch: the run in
    flight is abandoned (see _call_with_cancel) and the rest are recorded
    as cancelled without being sent."""
    logger = get_logger()
    batch_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = session_dir(session_id) / script.id / batch_id
    out_dir.mkdir(parents=True, exist_ok=True)
    env = getattr(client, "environment", None)  # the AEM environment this client is bound to (None = mock)
    aem_host = env.host if env is not None else "mock"
    env_name = env.name if env is not None else "mock"
    template = script.template()
    template_sha = audit.sha256_text(template)
    results: list[GeneratedFile] = []
    group_of: dict[str, list[str]] = {}
    origin = origin or batch_id
    try:
        aem_user = client.identity()
    except Exception as exc:
        aem_user = f"unknown ({str(exc)[:80]})"

    # Batch mode: replace the entered roots with the roots discovered under them
    # (e.g. /content -> each site), so each becomes its own small request.
    if batch is not None and script.batch is not None:
        key = script.batch.input
        discoveries: list[Discovery] = []
        for root in [normalize_jcr_path(r) for r in values.get(key) or [] if str(r).strip()]:
            if on_progress:
                on_progress(0, 0, f"discovering roots under {root}", None)
            try:
                discoveries.append(discover_roots(client, root, script.batch.kind, list(batch.get("excludes") or []),
                                                  int(batch.get("levels") or 1), cancel))
            except RunCancelled:
                break
        if script.batch.include_root_input and values.get(script.batch.include_root_input) is False:
            for d in discoveries:  # the user doesn't want the entered root's own content: not a part at all
                d.direct = [p for p in d.direct if p != d.root]
        if on_discovered:
            on_discovered(discoveries)
        audit.log("run.batch_discovered", session_id=session_id, aem_user=aem_user, target=script.id, details={
            "batch_id": batch_id, "kind": script.batch.kind, "excludes": batch.get("excludes"),
            "levels": batch.get("levels"),
            "roots": [{"root": d.root, "found": len(d.children), "direct": d.direct, "skipped": d.skipped,
                       "error": d.error} for d in discoveries]})
        for d in discoveries:
            if d.error:  # surfaces as a failed row the user can see and retry
                results.append(GeneratedFile(run_id=0, session_id=session_id, script_id=script.id, script_name=script.name,
                                             batch_id=batch_id, label=f"{d.root} (discovery)", status="error",
                                             error=f"Discovering roots failed: {d.error}", aem_user=aem_user,
                                             groups=[d.root], origin=origin))
        for d in discoveries:
            for label in d.labels:
                group_of.setdefault(label, []).append(d.root)
        all_runs = _batch_runs(script, values, discoveries)
    else:
        all_runs = expand_runs(script, values)

    runs = [r for r in all_runs if only_labels is None or r[0] in only_labels]
    used: set[str] = set()
    audit.log("run.batch_started", session_id=session_id, aem_user=aem_user, target=script.id, details={
        "batch_id": batch_id, "environment": env_name, "aem_host": aem_host, "runs": [r[0] for r in runs],
        "inputs": values,
        "template_sha256": template_sha, "script_path": str(script.script_path)})

    for index, (label, slug, config) in enumerate(runs):
        if on_progress:
            on_progress(index, len(runs), label, None)
        base, n = slug, 2
        while slug in used:
            slug, n = f"{base}-{n}", n + 1
        used.add(slug)
        result = GeneratedFile(run_id=0, session_id=session_id, script_id=script.id, script_name=script.name,
                               batch_id=batch_id, label=label, status="error", aem_user=aem_user,
                               groups=list(group_of.get(label, [])), origin=origin if label in group_of else "")
        started, started_at = time.monotonic(), _utc_now()
        logger.info("Run %s/%s: starting %s", script.id, batch_id, label)
        try:
            if cancel is not None and cancel.is_set():
                raise RunCancelled("Cancelled before it started; nothing was sent to AEM")
            executed = build_script(template, config)
            script_file = out_dir / f"{slug}.groovy"
            try:
                # Exactly what the client will send (it runs the same deterministic prepare()).
                sent = readonly.prepare(executed)
            except readonly.ReadOnlyViolationError:
                script_file.write_text(executed, encoding="utf-8")  # keep the blocked script for the audit trail
                result.executed_script_path, result.script_sha256 = str(script_file), audit.sha256_text(executed)
                raise
            script_file.write_text(sent, encoding="utf-8")
            result.executed_script_path, result.script_sha256 = str(script_file), audit.sha256_text(sent)
            console = _call_with_cancel(lambda: client.run_script(executed), cancel)
            data, meta = split_payload(parse_output(console.output))
            if meta.get("error"):
                raise RuntimeError(str(meta["error"]))
            json_file = out_dir / f"{slug}.json"
            json_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            result.json_path, result.meta, result.running_time = str(json_file), meta, console.running_time
            result.json_sha256 = audit.sha256_file(json_file)
            result.row_count = len(data) if isinstance(data, list) else None
            result.status = "ok"
            logger.info("Run %s/%s: %s -> %s rows (%s)", script.id, batch_id, label, result.row_count, console.running_time)
        except RunCancelled as exc:
            result.status, result.error, result.exception = "cancelled", str(exc), exc
            logger.info("Run %s/%s: %s cancelled: %s", script.id, batch_id, label, exc)
        except Exception as exc:
            result.error, result.exception = str(exc), exc
            logger.warning("Run %s/%s: %s failed: %s", script.id, batch_id, label, str(exc)[:500])
        result.elapsed_seconds = time.monotonic() - started
        result.run_id = database.record_run(
            session_id=session_id, batch_id=batch_id, aem_host=aem_host, script_id=script.id, label=label,
            status=result.status, row_count=result.row_count, running_time=result.running_time,
            json_path=result.json_path, meta=result.meta, error=result.error[:2000], config=config,
            started_at=started_at, finished_at=_utc_now(), actor=audit.local_actor(), aem_user=aem_user,
            template_sha256=template_sha, script_sha256=result.script_sha256,
            executed_script_path=result.executed_script_path, json_sha256=result.json_sha256,
        )
        blocked = isinstance(result.exception, readonly.ReadOnlyViolationError)
        action = ("run.blocked" if blocked else "run.cancelled" if result.status == "cancelled"
                  else f"run.{'completed' if result.ok else 'failed'}")
        audit.log(action, session_id=session_id, aem_user=aem_user,
                  target=f"{script.id}: {label}", status=result.status, details={
                      "run_id": result.run_id, "batch_id": batch_id, "rows": result.row_count,
                      "aem_running_time": result.running_time, "elapsed_seconds": round(result.elapsed_seconds, 1),
                      "json_path": result.json_path, "json_sha256": result.json_sha256,
                      "script_sha256": result.script_sha256, "error": result.error[:500],
                      "violations": [v.__dict__ for v in result.exception.violations] if blocked else []})
        results.append(result)
        if on_progress:
            on_progress(index + 1, len(runs), label, result)
    return results


def batch_groups(files: list[GeneratedFile]) -> dict[tuple[str, str, str], list[GeneratedFile]]:
    """Batch parts grouped per entered path: (script id, entered path, origin
    batch) -> the latest result of each part (a retried part replaces its
    earlier failure), in run order."""
    groups: dict[tuple[str, str, str], dict[str, GeneratedFile]] = {}
    for f in files:
        for group in f.groups:
            groups.setdefault((f.script_id, group, f.origin), {})[f.label] = f
    return {key: list(parts.values()) for key, parts in groups.items()}


def format_file(file: GeneratedFile, formatter_id: str) -> str:
    """Formats one session file; returns the output path. Raises ValueError
    when the data doesn't fit the formatter."""
    formatter = formatters.get(formatter_id)
    target = f"{file.script_id}: {file.label}"
    try:
        if not file.ok:
            raise ValueError("run failed — nothing to format")
        if file.json_sha256 and audit.verify_file(file.json_path, file.json_sha256) != "match":
            raise ValueError("the result JSON changed on disk since the run — refusing to format it")
        data = file.load()
        problem = formatter.check(data)
        if problem:
            raise ValueError(f"{formatter.name} {problem}")
        stem = Path(file.json_path).with_suffix("")
        out = stem.parent / f"{stem.name}.{formatter_id}{formatter.extension}"
        formatter.write(data, out)
    except Exception as exc:
        audit.log("format.failed", session_id=file.session_id, aem_user=file.aem_user, target=target, status="error",
                  details={"run_id": file.run_id, "formatter": formatter_id, "error": str(exc)[:500]})
        raise
    file.outputs[formatter_id] = str(out)
    file.output_hashes[formatter_id] = audit.sha256_file(out)
    database.record_outputs(file.run_id, {
        fid: {"path": path, "sha256": file.output_hashes.get(fid, "")} for fid, path in file.outputs.items()})
    audit.log("format.completed", session_id=file.session_id, aem_user=file.aem_user, target=target, details={
        "run_id": file.run_id, "formatter": formatter_id, "source_json_sha256": file.json_sha256,
        "output": str(out), "output_sha256": file.output_hashes[formatter_id]})
    return str(out)


def merge_rows(rows: list[Any], merge: "MergeRows | None") -> list[Any]:
    """Batch parts' rows that are already totals: same `group_by` values ->
    one row with the `sum` columns added up. Rows without the sum columns
    (e.g. a list-mode result) are left as they are."""
    if merge is None or not rows or not all(isinstance(r, dict) and all(c in r for c in merge.sum) for r in rows):
        return rows
    merged: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        key = tuple(row.get(c) for c in merge.group_by)
        if key not in merged:
            merged[key] = dict(row)
            continue
        for c in merge.sum:
            total = (merged[key][c] or 0) + (row[c] or 0)
            merged[key][c] = round(total, 2) if isinstance(total, float) else total
    return list(merged.values())


def load_rows(files: list[GeneratedFile], merge: "MergeRows | None" = None) -> list[Any]:
    """The rows of several results, concatenated in order (merged when the
    script says its rows are totals), with the same checks for preview and
    formatting. Raises ValueError."""
    combined: list[Any] = []
    for file in files:
        if not file.ok:
            raise ValueError(f"{file.label}: run failed, nothing to combine")
        if file.json_sha256 and audit.verify_file(file.json_path, file.json_sha256) != "match":
            raise ValueError(f"{file.label}: the result JSON changed on disk since the run, refusing to use it")
        data = file.load()
        if not isinstance(data, list):
            raise ValueError(f"{file.label}: only list results (rows) can be combined")
        combined.extend(data)
    return merge_rows(combined, merge)


def format_combined(files: list[GeneratedFile], formatter_id: str, session_id: str, *, name: str | None = None,
                    min_files: int = 2, merge: "MergeRows | None" = None) -> str:
    """Formats several session results as ONE file (rows concatenated, in the
    order given), e.g. every site of a batched page report. Returns its path."""
    formatter = formatters.get(formatter_id)
    if len(files) < min_files:
        raise ValueError("select at least two results to combine")
    combined = load_rows(files, merge)
    problem = formatter.check(combined)
    if problem:
        raise ValueError(f"{formatter.name} {problem}")
    out_dir = session_dir(session_id) / "combined"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = name or f"combined_{files[0].script_id}_{len(files)}-results"
    out = out_dir / f"{stem}_{stamp}.{formatter_id}{formatter.extension}"
    n = 2
    while out.exists():  # two entered paths with the same short name, combined in the same second
        out = out_dir / f"{stem}_{stamp}-{n}.{formatter_id}{formatter.extension}"
        n += 1
    formatter.write(combined, out)
    digest = audit.sha256_file(out)
    audit.log("format.combined", session_id=session_id, aem_user=files[0].aem_user, target=out.name, details={
        "formatter": formatter_id, "rows": len(combined), "output": str(out), "output_sha256": digest,
        "sources": [{"run_id": f.run_id, "label": f.label, "json_sha256": f.json_sha256} for f in files]})
    get_logger().info("Combined %d results with %s -> %s", len(files), formatter_id, out.name)
    return str(out)


def zip_files(paths: list[str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        seen: set[str] = set()
        for path in dict.fromkeys(paths):  # same file listed twice: once
            p = Path(path)
            if not p.exists():
                continue
            name = p.name
            if name in seen:  # same slug from two runs: keep both, prefixed with the run id folder
                name = f"{p.parent.name}_{p.name}"
            seen.add(name)
            zf.write(p, arcname=name)
    return buffer.getvalue()
