"""Manage the currently selected local Vissim dataset."""
from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
import json
import shutil
from pathlib import Path
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile

from config import INPX_PATH, MODIFICATIONS_DIR, PROJECT_DB_PATH, REAL_COUNTS_EXCEL, REPO_ROOT, SCENARIOS_DIR

UPLOAD_DIR = REPO_ROOT / "runtime" / "uploads" / "current"
ACTIVE_INPX_PATH = UPLOAD_DIR / "current.inpx"
ACTIVE_PROJECT_DB_PATH = UPLOAD_DIR / "project.vissimpdb"
ACTIVE_SCENARIOS_DIR = UPLOAD_DIR / "Scenarios"
ACTIVE_MODIFICATIONS_DIR = UPLOAD_DIR / "Modifications"
ACTIVE_REAL_COUNTS_EXCEL = UPLOAD_DIR / "real_counts.xlsx"
METADATA_PATH = UPLOAD_DIR / "metadata.json"


def get_active_inpx_path() -> Path:
    return ACTIVE_INPX_PATH if ACTIVE_INPX_PATH.exists() else INPX_PATH


def get_active_project_db_path() -> Path:
    return ACTIVE_PROJECT_DB_PATH if ACTIVE_INPX_PATH.exists() else PROJECT_DB_PATH


def get_active_scenarios_dir() -> Path:
    return ACTIVE_SCENARIOS_DIR if ACTIVE_INPX_PATH.exists() else SCENARIOS_DIR


def get_active_modifications_dir() -> Path:
    return ACTIVE_MODIFICATIONS_DIR if ACTIVE_INPX_PATH.exists() else MODIFICATIONS_DIR


def get_active_real_counts_excel() -> Path:
    return ACTIVE_REAL_COUNTS_EXCEL if ACTIVE_REAL_COUNTS_EXCEL.exists() else REAL_COUNTS_EXCEL


def get_active_dataset() -> dict:
    if not ACTIVE_INPX_PATH.exists():
        return {
            "source": "default",
            "kind": "default",
            "filename": INPX_PATH.name,
            "path": str(INPX_PATH),
            "uploaded_at": None,
            "revision": _revision_for_paths([INPX_PATH, PROJECT_DB_PATH, SCENARIOS_DIR, ACTIVE_REAL_COUNTS_EXCEL]),
            "has_project_db": PROJECT_DB_PATH.exists(),
            "has_scenarios": SCENARIOS_DIR.exists(),
            "has_modifications": MODIFICATIONS_DIR.exists(),
            "has_real_counts": get_active_real_counts_excel().exists(),
        }

    metadata = _read_metadata()
    return {
        "source": "uploaded",
        "kind": metadata.get("kind") or "inpx",
        "filename": metadata.get("filename") or ACTIVE_INPX_PATH.name,
        "path": str(ACTIVE_INPX_PATH),
        "uploaded_at": metadata.get("uploaded_at"),
        "revision": _revision_for_paths([ACTIVE_INPX_PATH, ACTIVE_PROJECT_DB_PATH, ACTIVE_SCENARIOS_DIR, ACTIVE_MODIFICATIONS_DIR, ACTIVE_REAL_COUNTS_EXCEL]),
        "has_project_db": ACTIVE_PROJECT_DB_PATH.exists(),
        "has_scenarios": ACTIVE_SCENARIOS_DIR.exists(),
        "has_modifications": ACTIVE_MODIFICATIONS_DIR.exists(),
        "has_real_counts": get_active_real_counts_excel().exists(),
    }


def save_active_dataset(filename: str, content: bytes) -> dict:
    """Persist an uploaded INPX or zipped Vissim project as the active dataset."""
    lowered = filename.lower()
    if lowered.endswith(".inpx"):
        return _save_single_inpx(filename, content)
    if lowered.endswith(".zip"):
        return _save_zip_dataset(filename, content)
    raise ValueError("Only .inpx and .zip files are supported.")


def reset_active_dataset() -> dict:
    if UPLOAD_DIR.exists():
        shutil.rmtree(UPLOAD_DIR)
    return get_active_dataset()


def save_active_real_counts_excel(filename: str, content: bytes) -> dict:
    """Persist a standalone validation workbook without replacing the active network."""
    if not content.strip():
        raise ValueError("Uploaded workbook is empty.")
    if not filename.lower().endswith(".xlsx"):
        raise ValueError("Only .xlsx validation workbooks are supported.")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    ACTIVE_REAL_COUNTS_EXCEL.write_bytes(content)

    metadata = _read_metadata()
    metadata.update(
        {
            "real_counts_filename": Path(filename).name,
            "real_counts_uploaded_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    _write_metadata_payload(metadata)
    return get_active_dataset()


def clear_active_real_counts_excel() -> dict:
    """Remove only the standalone validation workbook, preserving the active network."""
    ACTIVE_REAL_COUNTS_EXCEL.unlink(missing_ok=True)

    metadata = _read_metadata()
    metadata.pop("real_counts_filename", None)
    metadata.pop("real_counts_uploaded_at", None)
    _write_metadata_payload(metadata)
    return get_active_dataset()


def _save_single_inpx(filename: str, content: bytes) -> dict:
    if not content.strip():
        raise ValueError("Uploaded file is empty.")
    _validate_inpx(content)
    _prepare_upload_dir()
    ACTIVE_INPX_PATH.write_bytes(content)
    _write_metadata(filename, "inpx")
    return get_active_dataset()


def _save_zip_dataset(filename: str, content: bytes) -> dict:
    if not content.strip():
        raise ValueError("Uploaded file is empty.")

    try:
        with ZipFile(BytesIO(content)) as archive:
            members = [info for info in archive.infolist() if not info.is_dir()]
            inpx_member = _find_first_member(members, ".inpx")
            project_member = _find_first_member(members, ".vissimpdb")
            real_counts_member = _find_named_member(members, "real_counts.xlsx")

            if inpx_member is None:
                raise ValueError("Zip dataset must contain an .inpx file.")

            inpx_bytes = archive.read(inpx_member)
            _validate_inpx(inpx_bytes)
            _prepare_upload_dir()
            ACTIVE_INPX_PATH.write_bytes(inpx_bytes)

            if project_member is not None:
                ACTIVE_PROJECT_DB_PATH.write_bytes(archive.read(project_member))

            _extract_tree_from_zip(archive, members, "Scenarios", ACTIVE_SCENARIOS_DIR)
            _extract_tree_from_zip(archive, members, "Modifications", ACTIVE_MODIFICATIONS_DIR)

            if real_counts_member is not None:
                ACTIVE_REAL_COUNTS_EXCEL.write_bytes(archive.read(real_counts_member))
    except BadZipFile as exc:
        raise ValueError("Uploaded file is not a valid zip archive.") from exc

    _write_metadata(filename, "zip")
    return get_active_dataset()


def _prepare_upload_dir() -> None:
    if UPLOAD_DIR.exists():
        shutil.rmtree(UPLOAD_DIR)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _validate_inpx(content: bytes) -> None:
    try:
        ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError("Uploaded file is not a valid INPX/XML file.") from exc


def _find_first_member(members: list, suffix: str):
    for member in members:
        if _safe_member_name(member.filename).lower().endswith(suffix):
            return member
    return None


def _find_named_member(members: list, filename: str):
    target = filename.lower()
    for member in members:
        if Path(_safe_member_name(member.filename)).name.lower() == target:
            return member
    return None


def _extract_tree_from_zip(archive: ZipFile, members: list, folder_name: str, destination: Path) -> None:
    folder_lower = folder_name.lower()
    selected: list[tuple[object, Path]] = []
    for member in members:
        safe_name = _safe_member_name(member.filename)
        parts = Path(safe_name).parts
        lower_parts = [part.lower() for part in parts]
        if folder_lower not in lower_parts:
            continue
        folder_index = lower_parts.index(folder_lower)
        relative = Path(*parts[folder_index + 1 :])
        if not str(relative):
            continue
        selected.append((member, relative))

    if not selected:
        return

    destination.mkdir(parents=True, exist_ok=True)
    for member, relative in selected:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(archive.read(member))


def _safe_member_name(name: str) -> str:
    normalized = name.replace("\\", "/").lstrip("/")
    parts = [part for part in normalized.split("/") if part and part not in {".", ".."}]
    return str(Path(*parts)) if parts else ""


def _write_metadata(filename: str, kind: str) -> None:
    metadata = {
        "filename": Path(filename).name,
        "kind": kind,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_metadata_payload(metadata)


def _write_metadata_payload(metadata: dict) -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    METADATA_PATH.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_metadata() -> dict:
    if not METADATA_PATH.exists():
        return {}
    try:
        return json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _revision_for_paths(paths: list[Path]) -> str:
    chunks: list[str] = []
    for path in paths:
        if path.is_dir():
            chunks.append(_directory_revision(path))
        else:
            chunks.append(_file_revision(path))
    return "|".join(chunks)


def _file_revision(path: Path) -> str:
    try:
        stat = path.stat()
    except OSError:
        return f"{path.name}:missing"
    return f"{path.name}:{stat.st_mtime_ns}:{stat.st_size}"


def _directory_revision(path: Path) -> str:
    if not path.exists():
        return f"{path.name}:missing"
    latest = 0
    total_size = 0
    count = 0
    for child in path.rglob("*"):
        if not child.is_file():
            continue
        try:
            stat = child.stat()
        except OSError:
            continue
        latest = max(latest, stat.st_mtime_ns)
        total_size += stat.st_size
        count += 1
    return f"{path.name}:{latest}:{total_size}:{count}"
