"""A canned ``tools_run._run_job_and_wait`` so the REAL ``run_simulation``
tool (``services.agent.tools_run``) runs to completion in CI — no real
simulation job queue, no real background thread, no live Vissim.

``tools_run.py``'s own module docstring names this exact seam:
``_run_job_and_wait`` is "a thin, injectable seam ... named and shaped ...
specifically so tests can ``monkeypatch.setattr(tools_run,
"_run_job_and_wait", ...)`` with a fake runner." This module is that fake,
promoted to a reusable adapter with a plain attribute-patching context
manager (works identically inside and outside pytest — ``runner.py``'s CLI
path is not a pytest fixture context, so ``monkeypatch`` itself is not
available there).
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any, Iterator


def canned_terminal_job_record(
    *,
    tables: list[str],
    job_id: str = "eval-fake-job",
    status: str = "done",
    error: str | None = None,
) -> dict[str, Any]:
    """A terminal job record shaped exactly like
    ``services.simulation_runs.get_job_record``'s real return value, as far
    as ``tools_run.run_simulation`` inspects it (``id``, ``status``,
    ``result_tables``, ``error``)."""
    return {"id": job_id, "status": status, "result_tables": list(tables), "error": error}


@contextlib.contextmanager
def install_fake_run_job(
    record_or_factory: dict[str, Any]
    | Any,  # dict, or callable(*, source_inpx, duration_s, random_seeds, timeout_s) -> dict
) -> Iterator[None]:
    """Monkeypatch ``services.agent.tools_run._run_job_and_wait`` for the
    duration of the ``with`` block, restoring the original function on exit
    (or on any exception — this is a real ``finally``, not best-effort).

    ``record_or_factory`` is either a fixed terminal record dict (the common
    case — build one with :func:`canned_terminal_job_record`) or a callable
    receiving the same keyword arguments the real
    ``_run_job_and_wait(*, source_inpx, duration_s, random_seeds, timeout_s)``
    does, for a task that wants to vary its canned result by the requested
    duration.
    """
    from services.agent import tools_run

    original = tools_run._run_job_and_wait

    def _fake(
        *, source_inpx: Path, duration_s: int, random_seeds: list[int | None], timeout_s: float
    ) -> dict[str, Any]:
        if callable(record_or_factory):
            return record_or_factory(
                source_inpx=source_inpx,
                duration_s=duration_s,
                random_seeds=random_seeds,
                timeout_s=timeout_s,
            )
        return record_or_factory

    tools_run._run_job_and_wait = _fake
    try:
        yield
    finally:
        tools_run._run_job_and_wait = original


__all__ = ["canned_terminal_job_record", "install_fake_run_job"]
