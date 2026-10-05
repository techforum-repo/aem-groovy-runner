from __future__ import annotations

"""A script batch running in a background thread, so the page stays
responsive and the run can be cancelled.

The worker never touches Streamlit (no st.* calls; it has no script-run
context). It only updates this plain object, which the Run page polls and
renders, and the page's own thread finalizes it once `done` is set.
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from . import runner
from .scripts_registry import ScriptDef


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


def start(client: runner.ConsoleClient, script: ScriptDef, values: dict[str, Any], *, session_id: str,
          formatter_id: str | None, only_labels: list[str] | None = None, total: int,
          batch: dict[str, Any] | None = None) -> RunJob:
    job = RunJob(script_id=script.id, script_name=script.name, values=values, formatter_id=formatter_id,
                 total=total, session_id=session_id, batch=batch)

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
                                            on_discovered=lambda found: job.discoveries.extend(found))
            if formatter_id:
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
