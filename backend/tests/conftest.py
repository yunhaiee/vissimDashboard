from pathlib import Path
import sys

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


@pytest.fixture(scope="session", autouse=True)
def _isolate_ambient_machine_state(tmp_path_factory: pytest.TempPathFactory):
    """Pin the whole suite to the default ``data/`` demo dataset, regardless of
    this machine's persistent app state.

    ``services.datasets.get_active_*`` switch every reader to
    ``runtime/uploads/current/`` the moment a dataset has been uploaded through
    the dashboard UI, and ``services.simulation_runs`` lists completed COM runs
    from ``runtime/simulations/``. Both survive across sessions on a dev
    machine — a leftover manual upload (e.g. a live-demo rehearsal) once made
    every scenario-dependent test fail with empty scenario lists even though
    ``make_demo_data.py`` had seeded ``data/`` correctly. Redirecting the
    module constants at the source keeps all tests hermetic.

    Session-scoped deliberately: module-scoped fixtures (test_api_smoke.py's
    ``client``/``scenarios``) run before any function-scoped autouse patch
    could apply. Tests that exercise upload/run behaviour re-patch these same
    attributes onto their own tmp dirs, which composes fine — their
    function-scoped monkeypatch wins for the test, then restores these values.
    """
    from services import datasets, simulation_runs, workbook_mapping

    mp = pytest.MonkeyPatch()
    ambient_root = tmp_path_factory.mktemp("ambient-state")

    upload_dir = ambient_root / "uploads" / "current"
    mp.setattr(datasets, "UPLOAD_DIR", upload_dir)
    mp.setattr(datasets, "ACTIVE_INPX_PATH", upload_dir / "current.inpx")
    mp.setattr(datasets, "ACTIVE_PROJECT_DB_PATH", upload_dir / "project.vissimpdb")
    mp.setattr(datasets, "ACTIVE_SCENARIOS_DIR", upload_dir / "Scenarios")
    mp.setattr(datasets, "ACTIVE_MODIFICATIONS_DIR", upload_dir / "Modifications")
    mp.setattr(datasets, "ACTIVE_REAL_COUNTS_EXCEL", upload_dir / "real_counts.xlsx")
    mp.setattr(datasets, "METADATA_PATH", upload_dir / "metadata.json")

    simulation_root = ambient_root / "simulations"
    mp.setattr(simulation_runs, "SIMULATION_ROOT", simulation_root)
    mp.setattr(simulation_runs, "REGISTRY_PATH", simulation_root / "simulation_runs.json")

    # services/workbook_mapping.py stages uploaded workbooks and remembers
    # confirmed mappings outside of datasets.UPLOAD_DIR -- isolate those too
    # so a real dev machine's staged files/memory never leak into a test run
    # (and a test run never leaves stray files under the real runtime/ dir).
    mp.setattr(workbook_mapping, "STAGING_DIR", ambient_root / "uploads" / "staging")
    mp.setattr(workbook_mapping, "MAPPING_MEMORY_PATH", ambient_root / "uploads" / "mapping_memory.json")

    yield
    mp.undo()
