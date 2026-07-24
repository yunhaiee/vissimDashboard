"""M5 evaluation harness: the TR-C paper's benchmark substrate.

Everything here consumes the Phase 2 agent's PUBLIC seams only
(``services.agent.orchestrator.run_agent_turn``, ``services.agent.guardrails.
GuardrailEngine``, ``services.agent.trace.read_trace``,
``services.agent.tools_network.build_shadow_model``, ``services.agent.state.
ArtifactManager``) — see docs/phase2/04-EVAL-PLAN.md (M5.1/M5.2) and
docs/phase2/02-ROADMAP.md's M5 section. No code here imports
``services.agent`` internals beyond those public names, so additive growth in
``backend/services/agent/**`` (other milestones land concurrently) cannot
break this package by construction.

Package layout:

- ``types.py`` — ``TaskSpec``/``ExpectedOutcome``/``PostStateCheck`` (the
  benchmark task shape) and ``TaskResult`` (the per-task scored record).
- ``tasks_loader.py`` — YAML -> ``TaskSpec``, plus the ``network:`` alias
  table.
- ``scripted_provider.py`` — the CI-safe canned ``generate_structured``
  provider built from a task's ``scripted_turns``, plus the (never-used-in-
  pytest) real-provider adapter for live runs.
- ``fake_edit_session.py`` — a ``VissimEditSession``-shaped fake that mutates
  the session's working .inpx directly (no COM), for exercising the REAL
  ``tools_edit.py`` write tools in CI.
- ``fake_run_job.py`` — installs a canned ``tools_run._run_job_and_wait`` so
  the REAL ``run_simulation`` tool runs in CI without the real job queue.
- ``scoring.py`` — pure functions: post-state assertions, refusal
  correctness, warning yield, and the silent-failure detector (RQ2's killer
  metric).
- ``runner.py`` — drives one task (``run_task``) or a whole suite
  (``run_suite``) end-to-end, including the propose/approve round-trip, and
  writes ``results.jsonl`` + a markdown summary table. Also the CLI entry
  point (``python -m eval.runner``).
- ``tasks/*.yaml`` — the M5.1 seed benchmark suite (~15 tasks).
"""
