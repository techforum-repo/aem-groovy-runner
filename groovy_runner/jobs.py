from __future__ import annotations

"""A script batch running in a background thread, so the page stays
responsive and the run can be cancelled.

The worker never touches Streamlit (no st.* calls; it has no script-run
context). It only updates this plain object, which the Run page polls and
renders, and the page's own thread finalizes it once `done` is set.
"""

import threading
from pathlib import Path
import time
from dataclasses import dataclass, field
from typing import Any

from . import runner
from .scripts_registry import ScriptDef
from .utils import slug_for_path


@dataclass
class RunJob:
    script_id: str
    script_name: str
    values: dict[str, Any]
    formatter_id: str | None
    total: int
    session_id: str
    batch: dict[str, Any] | None = None
    discoveries: list[runner.Discovery] = field(default_factory=list)
    combined: list[tuple[str, str]] = field(default_factory=list)  # batch mode: (entered path, combined file)
    lineage: list[str] = field(default_factory=list)  # earlier batch ids this run continues (Retry)
    cancel: threading.Event = field(default_factory=threading.Event)
    started: float = field(default_factory=time.monotonic)
    current: str = ""  # label of the run in flight
    finished: int = 0
    lines: list[str] = field(default_factory=list)
    results: list[runner.GeneratedFile] = field(default_factory=list)
    messages: list[tuple[str, str]] = field(default_factory=list)  # (kind, text) shown after finishing
    error: BaseException | None = None  # unexpected failure of the whole batch
    done: bool = False
    thread: threading.Thread | None = None

    @property
    def cancelling(self) -> bool:
        return self.cancel.is_set() and not self.done

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started


def _progress_line(result: runner.GeneratedFile) -> str:
    if result.ok:
        mark = f"✅ {result.row_count if result.row_count is not None else '—'} rows"
    elif result.status == "cancelled":
        mark = "⏹ cancelled" + (" (abandoned while AEM was running it)" if "waiting for AEM" in result.error else "")
    else:
        mark = f"❌ {result.error[:150]}"
    return f"- `{result.label}`: {mark} ({result.elapsed_seconds:.0f}s)"


def _combine_per_entered_path(job: RunJob, script: ScriptDef, results: list[runner.GeneratedFile],
                              prior: list[runner.GeneratedFile]) -> None:
    """Batch mode: one file per entered path, built from all its discovered
    roots. `prior` are earlier successful results in this session (a Retry
    only re-runs the failed roots, so the file is rebuilt from both)."""
    latest: dict[str, runner.GeneratedFile] = {}
    for result in [*prior, *results]:  # later wins: a retried root replaces its older result
        if result.ok and result.script_id == job.script_id:
            latest[result.label] = result
    for d in job.discoveries:
        labels = d.labels
        if d.error or not labels:
            continue
        group = [latest[c] for c in labels if c in latest]
        missing = [c for c in labels if c not in latest]
        if not group:
            job.messages.append(("warning", f"No combined file for `{d.root}`: none of its {len(labels)} "
                                            "batch part(s) succeeded. See the errors below, then Retry."))
            continue
        job.current = f"combining results for {d.root}"
        slug = slug_for_path(d.root)
        try:
            path = runner.format_combined(group, job.formatter_id, job.session_id, min_files=1,
                                          name=slug, merge=script.batch.merge_rows)
        except ValueError as exc:  # e.g. more rows than one Excel sheet holds: fall back to one file per root
            for result in group:
                try:
                    runner.format_file(result, job.formatter_id)
                except Exception as inner:
                    job.messages.append(("warning", f"`{result.label}`: not formatted ({inner})"))
            job.messages.append(("warning", f"`{d.root}`: couldn't combine into one file ({exc}), so each root "
                                            "was formatted separately instead."))
            continue
        job.combined.append((d.root, path))
        text = f"One file for `{d.root}`: **{Path(path).name}** (combined from {len(group)} batch part(s))."
        if missing:
            text += (f" ⚠️ {len(missing)} part(s) not included (failed or cancelled): use Retry, and this file is "
                     "rebuilt with them.")
        job.messages.append(("success" if not missing else "warning", text))


def start(client: runner.ConsoleClient, script: ScriptDef, values: dict[str, Any], *, session_id: str,
          formatter_id: str | None, only_labels: list[str] | None = None, total: int,
          batch: dict[str, Any] | None = None, prior_results: list[runner.GeneratedFile] | None = None,
          lineage: list[str] | None = None) -> RunJob:
    """`prior_results` must come only from the same batch and its retries
    (`lineage`), never from unrelated runs with different inputs."""
    job = RunJob(script_id=script.id, script_name=script.name, values=values, formatter_id=formatter_id,
                 total=total, session_id=session_id, batch=batch, lineage=list(lineage or []))

    def on_progress(done: int, _total: int, label: str, result: runner.GeneratedFile | None) -> None:
        if _total:
            job.total = _total  # in batch mode the real count is only known after discovery
        if result is None:
            job.current = label
            return
        job.finished = done
        job.lines.append(_progress_line(result))

    def work() -> None:
        try:
            job.results = runner.run_script(client, script, values, session_id=session_id, on_progress=on_progress,
                                            only_labels=only_labels, cancel=job.cancel, batch=batch,
                                            on_discovered=lambda found: job.discoveries.extend(found),
                                            origin=(lineage or [None])[0])
            if formatter_id and batch is not None and script.batch is not None:
                _combine_per_entered_path(job, script, job.results, prior_results or [])
            elif formatter_id:
                job.current = "formatting"
                for result in job.results:
                    if result.ok:  # completed runs are formatted even if the batch was cancelled later
                        try:
                            runner.format_file(result, formatter_id)
                        except Exception as exc:  # incompatible data: leave it for the Format step
                            job.messages.append(("warning", f"`{result.label}`: not formatted ({exc})"))
        except BaseException as exc:  # never let the worker die silently
            job.error = exc
        finally:
            job.current = ""
            job.done = True

    job.thread = threading.Thread(target=work, name=f"run-{script.id}", daemon=True)
    job.thread.start()
    return job
