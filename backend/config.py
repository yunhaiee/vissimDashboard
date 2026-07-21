import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

REPO_ROOT = Path(__file__).resolve().parent.parent


def _default_data_dir() -> Path:
    preferred = REPO_ROOT / "data"
    if preferred.exists():
        return preferred

    # Backward compatibility for older local layout where data lived one level up.
    return REPO_ROOT.parent


DATA_DIR = Path(os.getenv("VISSIM_DATA_DIR", str(_default_data_dir())))

INPX_PATH = DATA_DIR / "practice.inpx"
SIMULATION_INPX_PATH = Path(os.getenv("VISSIM_SIMULATION_INPX_PATH", str(INPX_PATH)))
SCENARIOS_DIR = DATA_DIR / "Scenarios"
PROJECT_DB_PATH = DATA_DIR / "practice.vissimpdb"
MODIFICATIONS_DIR = DATA_DIR / "Modifications"

# Optional real-world count excel used by validation endpoints.
REAL_COUNTS_EXCEL = Path(os.getenv("VISSIM_REAL_COUNTS_EXCEL", str(DATA_DIR / "real_counts.xlsx")))
