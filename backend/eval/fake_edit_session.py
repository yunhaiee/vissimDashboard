"""A :class:`~services.agent.edit_session.VissimEditSession`-shaped fake, with
no COM and no live Vissim, for exercising the REAL ``tools_edit.py`` write
tools in CI.

This is the "fake-or-real edit-session seam" the M5.2 brief asks for: the
runner installs an instance of :class:`FakeEditSession` via
``services.agent.edit_session.set_active_edit_session`` exactly as
``routers/assistant.py`` installs a real ``VissimEditSession`` for a live
turn — ``tools_edit.py``'s tool functions cannot tell the difference (they
only ever call the set_*/get_*/persist duck-typed methods). Every write
therefore goes through the REAL ``services.agent.tools_edit`` module (not a
reimplementation registered on a throwaway registry, unlike the existing
``test_agent_orchestrator_write_gate.py`` fixtures) — the actual production
code path is what M5's benchmark scores.

**Persistence model.** Vehicle-input volume is the only write this fake
reflects onto the working .inpx's actual XML text (via
:func:`set_vehicle_input_volume_in_xml`), because it is the only M2 write
target ``build_shadow_model`` (``tools_network.py``) parses back out — see
that module's docstring: ``vehicle_inputs[].volume`` reads
``<timeIntervalVehVolume volume="...">``, a child element the synthetic demo
network's raw XML does not carry until this fake injects it. Signal cycle/
offset and sim-duration/random-seed writes are tracked in-memory only (no
XML mutation attempted): the demo network's signal controllers carry no
``intSupplyData`` (verified directly against ``data/practice.inpx`` while
building this suite — every signal-controller shadow-model entry is empty),
so ``build_shadow_model`` can never observe a signal edit either way
regardless of what this fake writes to disk; sim-duration/random-seed are
not shadow-model fields at all (``guardrails._WRITE_TARGET_FIELD_SPEC`` has
no entry for either tool), so ``postwrite``'s target-persisted check never
inspects them. See this task's drafted DECISIONS.md entry for the exact gap
and why it blocks a real signal-cycle happy-path task today.

**Failure injection.** Two knobs, both keyed by vehicle-input id (as a
string), simulate the Vissim-specific "COM write clamped/ignored" fault
from docs/phase2/04-EVAL-PLAN.md §4(b) -- in both cases
:meth:`get_vehicle_input_volume` (the in-memory "read-back") still returns
whatever was ``set_vehicle_input_volume``'d, so
``write_verification_failed``'s VALUE-COMPARE half never fires either way;
they differ in what actually reaches the saved file on :meth:`persist`:

- :attr:`simulate_clamped_write` (``{input_no: value}``) — persists
  ``value`` instead of what was requested. Produces a concrete, DIFFERENT
  saved value guardrails.py's separate postwrite target-persisted check
  (docs/phase2/DECISIONS.md, "postwrite gains a target-persisted check")
  WILL catch when guardrails are enabled (``write_verification_failed``
  fires, the plan is rolled back) — and, symmetrically, will NOT catch when
  ``AGENT_GUARDRAILS_DISABLED``/``GuardrailEngine(disabled=True)`` (the
  M5.3 ablation arm) skips that check entirely. This is the "textbook"
  fixture ``backend/tests/test_eval_runner.py`` uses to prove
  ``scoring.detect_silent_failure`` fires under the ablation and stays
  silent under the full stack.
- :attr:`simulate_ignored_write` (a set of input ids) — the write never
  reaches the file at all, leaving whatever was already there. Against
  THIS package's synthetic demo network specifically, a vehicle input's
  volume starts completely unset (no ``<timeIntervalVehVolume>`` element —
  see this module's docstring above), so "whatever was already there"
  reparses as ``volume: None`` — and guardrails.py's target-persisted check
  explicitly DEGRADES SILENTLY (returns ``None``, no finding) whenever the
  parsed field is ``None``, by design, to avoid a false positive against a
  genuine parser gap (e.g. a program-driven signal's opaque cycle time, the
  actual case that rule exists for). The consequence, confirmed by
  ``test_silent_failure_fixture_ignored_write_is_a_genuine_blind_spot_even_
  with_guardrails_enabled`` in ``backend/tests/test_eval_runner.py``: THIS
  specific failure shape slips past the full stack too, not just the
  ablation — a real, confirmed gap, not a hypothetical one. See this task's
  drafted DECISIONS.md entry.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def _self_closing_pattern(input_no: str) -> "re.Pattern[str]":
    return re.compile(
        r'<vehicleInput\b([^>]*\bno="%s"[^>]*?)/>' % re.escape(str(input_no))
    )


def _expanded_block_pattern(input_no: str) -> "re.Pattern[str]":
    return re.compile(
        r'(<vehicleInput\b[^>]*\bno="%s"[^>]*>)(.*?)(</vehicleInput>)' % re.escape(str(input_no)),
        re.S,
    )


_TIME_INTERVAL_VEH_VOLUME_RE = re.compile(r"<timeIntervalVehVolume\b[^>]*/>")


def set_vehicle_input_volume_in_xml(
    text: str, input_no: str, volume: float, interval_no: int = 1
) -> str:
    """Return ``text`` with ``<vehicleInput no="{input_no}">``'s
    ``interval_no``-th (1-based, document order) ``<timeIntervalVehVolume
    volume="...">`` child set to ``volume``.

    ``interval_no=1`` (default, byte-compatible with the original
    single-interval helper): injects a child if the element is currently
    self-closing (the synthetic demo network's actual shape — see module
    docstring) or has no ``<timeIntervalVehVolume>`` children yet; otherwise
    edits the FIRST existing one.

    ``interval_no > 1`` (v2 unit ⑤) edits an EXISTING later interval only —
    this fake never invents new intervals, matching the real COM behavior
    verified live (backend/tools/vehicle_input_interval_probe.py:
    ``SetAttValue`` on a genuinely non-existent interval index raises, never
    creates one). Raises :class:`ValueError` if the target ``<vehicleInput>``
    has fewer than ``interval_no`` existing children (a task-authoring bug,
    same precedent as the missing-input_no case below).

    Raises :class:`ValueError` if no ``<vehicleInput>`` with that ``no``
    exists in ``text`` at all — a task-authoring bug (the guardrail catalog's
    ``object_not_found`` check should have refused before this fake ever
    runs for a genuinely absent id).
    """
    expanded = _expanded_block_pattern(input_no)
    match = expanded.search(text)
    if match is not None:
        inner = match.group(2)
        children = list(_TIME_INTERVAL_VEH_VOLUME_RE.finditer(inner))
        if interval_no <= len(children):
            target = children[interval_no - 1]
            child_text = target.group(0)
            if 'volume="' in child_text:
                new_child_text = re.sub(r'volume="[^"]*"', f'volume="{volume}"', child_text, count=1)
            else:
                new_child_text = child_text[:-2] + f' volume="{volume}"/>'
            new_inner = inner[: target.start()] + new_child_text + inner[target.end() :]
        elif interval_no == 1:
            # No children at all yet -- inject one (identical to the
            # original single-interval behavior).
            new_inner = inner + f'<timeIntervalVehVolume timeInt="1" volume="{volume}"/>'
        else:
            raise ValueError(
                f'<vehicleInput no="{input_no}"> only has {len(children)} '
                f"<timeIntervalVehVolume> interval(s); interval_no={interval_no} does not exist"
            )
        return text[: match.start()] + match.group(1) + new_inner + match.group(3) + text[match.end() :]

    self_closing = _self_closing_pattern(input_no)
    match2 = self_closing.search(text)
    if match2 is None:
        raise ValueError(f"No <vehicleInput no=\"{input_no}\"> element found in the working .inpx")
    if interval_no != 1:
        raise ValueError(
            f'<vehicleInput no="{input_no}"> has no <timeIntervalVehVolume> children at all; '
            f"interval_no={interval_no} does not exist"
        )
    attrs = match2.group(1)
    replacement = (
        f"<vehicleInput{attrs}><timeIntervalVehVolume timeInt=\"1\" volume=\"{volume}\"/></vehicleInput>"
    )
    return text[: match2.start()] + replacement + text[match2.end() :]


class FakeEditSession:
    """Duck-typed stand-in for ``services.agent.edit_session.VissimEditSession``.

    Implements every ``set_*``/``get_*`` pair ``tools_edit.py`` calls plus
    :meth:`persist` — no COM, no Vissim process. Construct with the SAME
    ``working_inpx`` path the session's ``ArtifactManager`` clones to
    (``artifact_manager.working_path``), install via
    ``services.agent.edit_session.set_active_edit_session(session)``.

    ``simulate_ignored_write``/``simulate_clamped_write`` are the deliberate
    failure-injection hooks — see module docstring.
    """

    def __init__(
        self,
        working_inpx: Path | str,
        *,
        simulate_ignored_write: set[str] | None = None,
        simulate_clamped_write: dict[str, float] | None = None,
    ) -> None:
        self.working_inpx = Path(working_inpx)
        self.simulate_ignored_write = set(simulate_ignored_write or set())
        self.simulate_clamped_write = dict(simulate_clamped_write or {})

        # v2 unit ⑤: keyed by (input_no, interval_no) so interval_no=1 (the
        # byte-compatible default) and any other interval on the same input
        # coexist independently.
        self._pending_volume: dict[tuple[str, int], float] = {}
        self._pending_continuous: dict[tuple[str, int], bool] = {}
        self._pending_cycle: dict[str, int] = {}
        self._pending_offset: dict[str, int] = {}
        self._sim_duration: int | None = None
        self._random_seed: int | None = None

        self.persist_calls = 0
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    # -----------------------------------------------------------------
    # M2.1 paired verbs — same names/signatures as VissimEditSession
    # -----------------------------------------------------------------

    def set_vehicle_input_volume(self, input_no: int, volume: float, interval_no: int = 1) -> None:
        self.calls.append(
            ("set_vehicle_input_volume", (input_no, volume))
            if interval_no == 1
            else ("set_vehicle_input_volume", (input_no, volume, interval_no))
        )
        self._pending_volume[(str(input_no), interval_no)] = float(volume)
        if interval_no != 1:
            # Simulates the real Cont-clearing side effect
            # (VissimController.set_vehicle_input's docstring, v2 unit ⑤
            # probe): a non-first interval is no longer "continuous" once
            # explicitly written.
            self._pending_continuous[(str(input_no), interval_no)] = False

    def get_vehicle_input_volume(self, input_no: int, interval_no: int = 1) -> float:
        self.calls.append(
            ("get_vehicle_input_volume", (input_no,))
            if interval_no == 1
            else ("get_vehicle_input_volume", (input_no, interval_no))
        )
        return self._pending_volume[(str(input_no), interval_no)]

    def get_vehicle_input_continuous(self, input_no: int, interval_no: int) -> bool:
        self.calls.append(("get_vehicle_input_continuous", (input_no, interval_no)))
        return self._pending_continuous.get((str(input_no), interval_no), False)

    def set_signal_cycle(self, sc_no: int, cycle: int) -> None:
        self.calls.append(("set_signal_cycle", (sc_no, cycle)))
        self._pending_cycle[str(sc_no)] = int(cycle)

    def get_signal_cycle(self, sc_no: int) -> int:
        self.calls.append(("get_signal_cycle", (sc_no,)))
        return self._pending_cycle[str(sc_no)]

    def set_signal_offset(self, sc_no: int, offset: int) -> None:
        self.calls.append(("set_signal_offset", (sc_no, offset)))
        self._pending_offset[str(sc_no)] = int(offset)

    def get_signal_offset(self, sc_no: int) -> int:
        self.calls.append(("get_signal_offset", (sc_no,)))
        return self._pending_offset[str(sc_no)]

    def set_sim_duration(self, seconds: int) -> None:
        self.calls.append(("set_sim_duration", (seconds,)))
        self._sim_duration = int(seconds)

    def get_sim_duration(self) -> int:
        self.calls.append(("get_sim_duration", ()))
        assert self._sim_duration is not None, "get_sim_duration called before set_sim_duration"
        return self._sim_duration

    def set_random_seed(self, seed: int) -> None:
        self.calls.append(("set_random_seed", (seed,)))
        self._random_seed = int(seed)

    def get_random_seed(self) -> int:
        self.calls.append(("get_random_seed", ()))
        assert self._random_seed is not None, "get_random_seed called before set_random_seed"
        return self._random_seed

    # -----------------------------------------------------------------
    # Persistence — only vehicle-input volume reaches the file; see
    # module docstring for why signal/sim/seed writes are memory-only.
    # -----------------------------------------------------------------

    def persist(self) -> None:
        self.persist_calls += 1
        if not self._pending_volume:
            return
        text = self.working_inpx.read_text(encoding="utf-8")
        changed = False
        for (input_no, interval_no), volume in self._pending_volume.items():
            if input_no in self.simulate_ignored_write:
                # Failure injection: acknowledge the write (get_* above
                # already returned the "landed" value) but never actually
                # save it — see module docstring's §"Failure injection".
                # Scoped by input_no only (not interval_no) -- v2 unit ⑤
                # does not extend this injection hook to the interval
                # dimension.
                continue
            effective_volume = self.simulate_clamped_write.get(input_no, volume)
            text = set_vehicle_input_volume_in_xml(text, input_no, effective_volume, interval_no)
            changed = True
        if changed:
            self.working_inpx.write_text(text, encoding="utf-8")

    def close(self) -> None:
        """No-op: no COM connection, nothing to release. Present so callers
        that unconditionally call ``session.close()`` in a ``finally`` block
        (matching real ``VissimEditSession`` usage) work unchanged."""


def default_fake_edit_session_factory(artifact_manager: Any) -> FakeEditSession:
    """The default ``edit_session_factory`` ``runner.py`` uses: one fresh
    :class:`FakeEditSession` per task, over the task's own working copy."""
    return FakeEditSession(artifact_manager.working_path)


__all__ = [
    "FakeEditSession",
    "default_fake_edit_session_factory",
    "set_vehicle_input_volume_in_xml",
]
