"""Flexible base-workbook import: inspect an arbitrary spreadsheet, propose a
column mapping, preview it, and (once the user confirms) import it through
the exact same row-building code the standard ``real_counts.xlsx`` template
importer uses.

Why this exists (docs/phase2/DECISIONS.md has no entry for this yet -- record
one if the interface gap resurfaces): the validation tab's original importer
(``services/validation.py``) only understands workbooks shaped like the
shipped template -- sheets literally named ``Counts``/``TravelTimes`` with one
of three known header layouts. Real consulting workbooks are never shaped
like that. This module lets a user upload ANY workbook, pick the sheet/header
row, and map each required field to whatever column actually holds it.

THE TRAP THIS MODULE IS BUILT TO CATCH: a sheet of *modelled* output can look
exactly like a sheet of *observed* counts. If a user (by mistake, or because
a workbook's only numeric column happens to be the model's own turning-
movement output) maps model data into the observed field, GEH compares the
model against itself and passes 100% forever -- a permanently-green
validation screen that means nothing. ``validate_counts_mapping``/
``validate_travel_times_mapping`` refuse that import outright, with a
message the user can act on, per this repo's governing rule: a refused edit
with a clear reason beats a completed edit with wrong numbers.

Two-phase flow so the browser never re-uploads the file on every UI tweak:

1. ``inspect_workbook`` -- parses the uploaded bytes once, STAGES them under
   a random token (``runtime/uploads/staging/<token>/source.xlsx``), and
   returns bounded per-sheet previews (header-row guess, headers, a few
   sample rows) plus an alias-based proposed mapping (reusing
   ``services.validation.COUNT_HEADER_ALIASES`` so the shipped template still
   auto-maps with zero user interaction -- callers are expected to try the
   existing template-shaped upload path FIRST and only fall back to this
   flow when that comes back with warnings).
2. ``preview_mapping`` / ``import_mapping`` -- take ``{sheet, header_row,
   fields}`` for counts and/or travel_times, re-open the STAGED file (no
   re-upload), and either return a bounded preview + any refusal reasons, or
   (import, no refusals) persist a freshly-built canonical workbook via
   ``services.datasets.save_active_real_counts_excel`` -- so
   ``GET /api/validation/base-workbook`` keeps working completely unchanged
   for every existing consumer, because it is reading a workbook whose
   sheets are literally named ``Counts``/``TravelTimes`` with the canonical
   headers, built from rows parsed by the SAME
   ``_parse_counts_sheet``/``_parse_travel_times_standard`` functions the
   template path uses (just given an explicit header mapping instead of
   auto-detecting one).

A confirmed mapping is remembered (``runtime/uploads/mapping_memory.json``,
keyed by a signature of the sheet's header text) so re-uploading a workbook
with the same layout later proposes the human-confirmed mapping instead of
just the alias guess.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
import shutil
import time
from datetime import timezone
from io import BytesIO
from pathlib import Path
from typing import Any, TypedDict
from uuid import uuid4

try:
    import openpyxl
    from openpyxl.utils import get_column_letter
    _HAS_OPENPYXL = True
except ImportError:  # pragma: no cover - exercised indirectly via _require_openpyxl
    _HAS_OPENPYXL = False

from config import REPO_ROOT
from services import datasets
from services import validation as validation_module

STAGING_DIR = REPO_ROOT / "runtime" / "uploads" / "staging"
MAPPING_MEMORY_PATH = REPO_ROOT / "runtime" / "uploads" / "mapping_memory.json"
STAGING_TTL_SECONDS = 2 * 60 * 60  # 2 hours -- an abandoned mapping session is cheap to just re-upload.

# Bounded output everywhere (this repo has a scar about unbounded list responses).
MAX_INSPECT_SHEETS = 25
MAX_SAMPLE_ROWS = 8
MAX_PREVIEW_ROWS = 10
MAX_HEADER_SCAN_ROWS = 30
MAX_COLUMNS_REPORTED = 60
MAX_SAMPLE_SCAN_ROWS = 2000  # defense-in-depth: bound how many blank rows we'll walk past looking for samples.

# The field vocabulary the mapping UI exposes -- a deliberate subset of
# services.validation.COUNT_HEADER_ALIASES's full key set (that dict also
# carries fields like queue_length/persons that this UI doesn't map).
MAPPABLE_COUNT_FIELDS = ["period", "node_id", "node_name", "movement", "observed_volume", "model_volume"]
REQUIRED_COUNT_FIELDS = ["period", "movement", "observed_volume"]  # + at least one of node_id/node_name

MAPPABLE_TRAVEL_TIME_FIELDS = [
    "period",
    "route_id",
    "route_name",
    "sample_order",
    "time_label",
    "observed_travel_time_sec",
    "model_travel_time_sec",
]
REQUIRED_TRAVEL_TIME_FIELDS = ["period", "observed_travel_time_sec"]  # + at least one of route_id/route_name

COUNT_ALIASES_FOR_MAPPING = {k: v for k, v in validation_module.COUNT_HEADER_ALIASES.items() if k in MAPPABLE_COUNT_FIELDS}

# Aliases for the travel-time mapping UI's own canonical field names (distinct
# from services.validation.TRAVEL_TIME_VISSIM_ALIASES, which is keyed for the
# stacked-AM/PM Vissim export parser, not this flat single-header-row UI).
TRAVEL_TIME_HEADER_ALIASES: dict[str, set[str]] = {
    "period": {"period", "시간대구분", "구분", "peak", "peak_period"},
    "route_id": {"route_id", "route", "routeid", "루트", "루트id", "구간id", "구간번호"},
    "route_name": {
        "route_name", "routename", "measurement", "vehicletraveltimemeasurement",
        "vehicletravletimemeasurement", "구간명", "루트명", "통행시간측정",
    },
    "sample_order": {"sample_order", "order", "순번"},
    "time_label": {"time_label", "time_range", "timeint", "시간대", "interval"},
    "observed_travel_time_sec": {
        "observed_travel_time_sec", "observed", "actual", "real", "실측", "관측", "실제",
        "실제통행시간", "tomtom avg", "tomtom_avg", "tomtom average",
    },
    "model_travel_time_sec": {
        "model_travel_time_sec", "traveltime", "travel_time", "travtm", "model", "simulation",
        "sim", "통행시간", "model output", "model_output",
    },
}


class StagedWorkbookNotFoundError(Exception):
    """Raised when an ``upload_token`` is unknown, malformed, or expired."""


class MappingRefusedError(Exception):
    """Raised by :func:`import_mapping` when one or more refusal reasons fire.

    ``issues`` is a list of ``{code, ko, en, ja}`` dicts -- see
    :func:`validate_counts_mapping`/:func:`validate_travel_times_mapping`.
    """

    def __init__(self, issues: list[dict[str, str]]):
        super().__init__("Workbook mapping was refused: " + ", ".join(i["code"] for i in issues))
        self.issues = issues


def _require_openpyxl() -> None:
    if not _HAS_OPENPYXL:
        raise ValueError("openpyxl is not installed.")


# --------------------------------------------------------------------------
# Staging: hold the uploaded bytes across the multi-step mapping UI without
# re-uploading the file on every tweak.
# --------------------------------------------------------------------------

def stage_workbook(filename: str, content: bytes) -> str:
    _require_openpyxl()
    if not content or not content.strip():
        raise ValueError("Uploaded file is empty.")
    try:
        openpyxl.load_workbook(BytesIO(content), data_only=True)
    except Exception as exc:
        raise ValueError(f"Uploaded file could not be read as an Excel workbook: {exc}") from exc

    _purge_stale_staging()
    token = uuid4().hex
    staging_dir = STAGING_DIR / token
    staging_dir.mkdir(parents=True, exist_ok=True)
    (staging_dir / "source.xlsx").write_bytes(content)
    (staging_dir / "meta.json").write_text(
        json.dumps({"filename": Path(filename).name or "upload.xlsx", "staged_at": datetime.datetime.now(timezone.utc).isoformat()}),
        encoding="utf-8",
    )
    return token


def _staging_path(token: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
        raise StagedWorkbookNotFoundError("Unknown or expired upload token. Please re-upload the file.")
    source = STAGING_DIR / token / "source.xlsx"
    if not source.exists():
        raise StagedWorkbookNotFoundError("Unknown or expired upload token. Please re-upload the file.")
    return source


def load_staged_workbook(token: str):
    _require_openpyxl()
    source = _staging_path(token)
    try:
        return openpyxl.load_workbook(source, data_only=True)
    except Exception as exc:
        raise ValueError(f"Staged workbook could not be read: {exc}") from exc


def discard_staged_workbook(token: str) -> None:
    staging_dir = STAGING_DIR / re.sub(r"[^0-9a-f]", "", token or "")
    if staging_dir.exists() and staging_dir.parent == STAGING_DIR:
        shutil.rmtree(staging_dir, ignore_errors=True)


def _staged_filename(token: str) -> str:
    meta_path = STAGING_DIR / token / "meta.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return meta.get("filename") or "upload.xlsx"
    except Exception:
        return "upload.xlsx"


def _purge_stale_staging(max_age_seconds: int = STAGING_TTL_SECONDS) -> None:
    if not STAGING_DIR.exists():
        return
    now = time.time()
    for child in STAGING_DIR.iterdir():
        try:
            if not child.is_dir():
                continue
            if now - child.stat().st_mtime > max_age_seconds:
                shutil.rmtree(child, ignore_errors=True)
        except OSError:
            continue


# --------------------------------------------------------------------------
# Inspect: bounded per-sheet preview + alias-based proposed mapping.
# --------------------------------------------------------------------------

def _looks_numeric(value: Any) -> bool:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return True
    text = validation_module._clean_string(value)
    if not text:
        return False
    try:
        float(text)
        return True
    except (TypeError, ValueError):
        return False


def _jsonable_cell(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, str)):
        return value
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return str(value)
    return str(value)


_ALL_MAPPING_ALIAS_SETS: list[set[str]] = [
    {validation_module._normalize_header(c) for c in candidates}
    for candidates in list(COUNT_ALIASES_FOR_MAPPING.values()) + list(TRAVEL_TIME_HEADER_ALIASES.values())
]


def _detect_header_row(ws, max_scan_rows: int = MAX_HEADER_SCAN_ROWS) -> int:
    """Best-effort header-row guess. Must never raise or hang: a hostile
    input (e.g. a raw turning-movement export with no header text at all,
    data starting mid-sheet) just gets an imperfect guess -- the caller is
    expected to let the user override it, never to crash on it."""
    try:
        scan_limit = max(1, min(max_scan_rows, ws.max_row or 1))
    except Exception:
        return 1

    best_row = 1
    best_score = -1
    for row_idx in range(1, scan_limit + 1):
        try:
            row = next(ws.iter_rows(min_row=row_idx, max_row=row_idx, values_only=True), ())
        except Exception:
            continue
        normalized_cells = {validation_module._normalize_header(v) for v in row if validation_module._clean_string(v)}
        if not normalized_cells:
            continue
        text_cell_count = sum(1 for v in row if validation_module._clean_string(v) and not _looks_numeric(v))
        alias_hits = sum(1 for cell in normalized_cells if any(cell in alias_set for alias_set in _ALL_MAPPING_ALIAS_SETS))
        score = alias_hits * 100 + text_cell_count
        if score > best_score:
            best_score = score
            best_row = row_idx
    return best_row


def _propose_field_mapping_row(header_values: list[Any], aliases: dict[str, set[str]]) -> dict[str, dict[str, Any]]:
    proposal: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(header_values):
        normalized = validation_module._normalize_header(value)
        if not normalized:
            continue
        for key, candidates in aliases.items():
            if key in proposal:
                continue
            normalized_candidates = {validation_module._normalize_header(c) for c in candidates}
            if normalized in normalized_candidates:
                proposal[key] = {
                    "column": index,
                    "header": validation_module._clean_string(value),
                    "source": "alias",
                    "matched_alias": normalized,
                }
                break
    return proposal


def _apply_remembered_mapping(proposal: dict[str, dict[str, Any]], kind: str, header_values: list[Any]) -> None:
    remembered = recall_mapping(kind, [validation_module._clean_string(v) for v in header_values])
    if not remembered:
        return
    lookup = {validation_module._clean_string(v).strip().lower(): idx for idx, v in enumerate(header_values) if validation_module._clean_string(v)}
    for field, header_text in remembered.items():
        idx = lookup.get(str(header_text).strip().lower())
        if idx is None:
            continue
        proposal[field] = {
            "column": idx,
            "header": validation_module._clean_string(header_values[idx]),
            "source": "remembered",
            "matched_alias": None,
        }


def _inspect_sheet(ws) -> dict[str, Any]:
    try:
        header_row = _detect_header_row(ws)
    except Exception:
        header_row = 1

    max_col_actual = ws.max_column or 1
    max_col = max(1, min(max_col_actual, MAX_COLUMNS_REPORTED))
    columns_truncated = max_col_actual > MAX_COLUMNS_REPORTED

    header_values = list(next(ws.iter_rows(min_row=header_row, max_row=header_row, values_only=True), ()))[:max_col]
    header_values += [None] * max(0, max_col - len(header_values))
    headers = [validation_module._clean_string(v) for v in header_values]

    sample_rows: list[list[Any]] = []
    sample_truncated = False
    scanned = 0
    for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
        scanned += 1
        if scanned > MAX_SAMPLE_SCAN_ROWS:
            break
        trimmed = list(row)[:max_col]
        if all(validation_module._clean_string(v) == "" for v in trimmed):
            continue
        if len(sample_rows) >= MAX_SAMPLE_ROWS:
            sample_truncated = True
            break
        sample_rows.append([_jsonable_cell(v) for v in trimmed])

    proposed_counts = _propose_field_mapping_row(header_values, COUNT_ALIASES_FOR_MAPPING)
    _apply_remembered_mapping(proposed_counts, "counts", header_values)
    proposed_travel = _propose_field_mapping_row(header_values, TRAVEL_TIME_HEADER_ALIASES)
    _apply_remembered_mapping(proposed_travel, "travel_times", header_values)

    return {
        "name": ws.title,
        "header_row": header_row,
        "headers": headers,
        "columns": [get_column_letter(i + 1) for i in range(max_col)],
        "sample_rows": sample_rows,
        "sample_truncated": sample_truncated,
        "max_row": ws.max_row or 0,
        "max_col": max_col_actual,
        "columns_truncated": columns_truncated,
        "proposed_counts_mapping": proposed_counts,
        "proposed_travel_times_mapping": proposed_travel,
    }


def _looks_like_sheet_name(name: str, kind: str) -> bool:
    lowered = (name or "").strip().lower()
    if kind == "counts":
        return "count" in lowered
    return "travel" in lowered


def _propose_workbook_mapping(sheets_out: list[dict[str, Any]]) -> dict[str, Any]:
    def score(entry: dict[str, Any], proposal_key: str, kind: str, required: list[str]) -> tuple[int, int]:
        proposal = entry[proposal_key]
        matched = sum(1 for f in required if f in proposal)
        name_bonus = 1 if _looks_like_sheet_name(entry["name"], kind) else 0
        return (matched, name_bonus)

    best_counts = max(
        sheets_out,
        key=lambda e: score(e, "proposed_counts_mapping", "counts", REQUIRED_COUNT_FIELDS),
        default=None,
    )
    best_travel = max(
        sheets_out,
        key=lambda e: score(e, "proposed_travel_times_mapping", "travel_times", REQUIRED_TRAVEL_TIME_FIELDS),
        default=None,
    )

    def as_block(entry: dict[str, Any] | None, proposal_key: str) -> dict[str, Any] | None:
        if entry is None:
            return None
        return {
            "sheet": entry["name"],
            "header_row": entry["header_row"],
            "fields": entry[proposal_key],
        }

    return {
        "counts": as_block(best_counts, "proposed_counts_mapping"),
        "travel_times": as_block(best_travel, "proposed_travel_times_mapping"),
    }


def inspect_workbook(filename: str, content: bytes) -> dict[str, Any]:
    _require_openpyxl()
    token = stage_workbook(filename, content)
    wb = load_staged_workbook(token)

    sheet_names = wb.sheetnames
    sheets_truncated = len(sheet_names) > MAX_INSPECT_SHEETS
    sheets_out: list[dict[str, Any]] = []
    for name in sheet_names[:MAX_INSPECT_SHEETS]:
        try:
            sheets_out.append(_inspect_sheet(wb[name]))
        except Exception as exc:  # a single malformed sheet must not sink the whole inspect
            sheets_out.append({
                "name": name,
                "header_row": 1,
                "headers": [],
                "columns": [],
                "sample_rows": [],
                "sample_truncated": False,
                "max_row": 0,
                "max_col": 0,
                "columns_truncated": False,
                "proposed_counts_mapping": {},
                "proposed_travel_times_mapping": {},
                "error": str(exc),
            })

    return {
        "upload_token": token,
        "filename": Path(filename).name or "upload.xlsx",
        "sheets": sheets_out,
        "sheets_truncated": sheets_truncated,
        "proposed_mapping": _propose_workbook_mapping(sheets_out),
    }


def resolve_sheet_mapping(token: str, sheet: str, header_row: int) -> dict[str, Any]:
    """Recompute headers/sample rows/proposed mapping for a sheet at a
    user-chosen header row, without re-uploading the file or committing
    anything."""
    wb = load_staged_workbook(token)
    if sheet not in wb.sheetnames:
        raise ValueError(f"Sheet '{sheet}' was not found in the uploaded workbook.")
    ws = wb[sheet]
    max_row = ws.max_row or 1
    if header_row < 1 or header_row > max_row:
        raise ValueError(f"header_row {header_row} is outside the sheet's row range (1-{max_row}).")

    # Reuse _inspect_sheet's body at a forced header row by monkey-shaping a
    # tiny local override -- simplest is to inline the same steps here since
    # the detection step is the only difference.
    max_col_actual = ws.max_column or 1
    max_col = max(1, min(max_col_actual, MAX_COLUMNS_REPORTED))
    header_values = list(next(ws.iter_rows(min_row=header_row, max_row=header_row, values_only=True), ()))[:max_col]
    header_values += [None] * max(0, max_col - len(header_values))
    headers = [validation_module._clean_string(v) for v in header_values]

    sample_rows: list[list[Any]] = []
    sample_truncated = False
    scanned = 0
    for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
        scanned += 1
        if scanned > MAX_SAMPLE_SCAN_ROWS:
            break
        trimmed = list(row)[:max_col]
        if all(validation_module._clean_string(v) == "" for v in trimmed):
            continue
        if len(sample_rows) >= MAX_SAMPLE_ROWS:
            sample_truncated = True
            break
        sample_rows.append([_jsonable_cell(v) for v in trimmed])

    proposed_counts = _propose_field_mapping_row(header_values, COUNT_ALIASES_FOR_MAPPING)
    _apply_remembered_mapping(proposed_counts, "counts", header_values)
    proposed_travel = _propose_field_mapping_row(header_values, TRAVEL_TIME_HEADER_ALIASES)
    _apply_remembered_mapping(proposed_travel, "travel_times", header_values)

    return {
        "name": sheet,
        "header_row": header_row,
        "headers": headers,
        "columns": [get_column_letter(i + 1) for i in range(max_col)],
        "sample_rows": sample_rows,
        "sample_truncated": sample_truncated,
        "max_row": ws.max_row or 0,
        "max_col": max_col_actual,
        "proposed_counts_mapping": proposed_counts,
        "proposed_travel_times_mapping": proposed_travel,
    }


# --------------------------------------------------------------------------
# Mapping memory: remember a confirmed mapping by header-text signature so a
# re-upload of the same layout doesn't require redoing the mapping step.
# --------------------------------------------------------------------------

def _header_signature(headers: list[str]) -> str:
    normalized = sorted({h.strip().lower() for h in headers if h and h.strip()})
    return hashlib.sha1("\x1f".join(normalized).encode("utf-8")).hexdigest()


def _load_mapping_memory() -> dict[str, Any]:
    if not MAPPING_MEMORY_PATH.exists():
        return {}
    try:
        return json.loads(MAPPING_MEMORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_mapping_memory(memory: dict[str, Any]) -> None:
    MAPPING_MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    MAPPING_MEMORY_PATH.write_text(json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8")


def remember_mapping(kind: str, headers: list[str], field_to_header_text: dict[str, str]) -> None:
    if not field_to_header_text:
        return
    signature = _header_signature(headers)
    memory = _load_mapping_memory()
    memory.setdefault(kind, {})[signature] = field_to_header_text
    _save_mapping_memory(memory)


def recall_mapping(kind: str, headers: list[str]) -> dict[str, str] | None:
    signature = _header_signature(headers)
    memory = _load_mapping_memory()
    return memory.get(kind, {}).get(signature)


# --------------------------------------------------------------------------
# Refusal checks -- see this module's docstring for the trap this exists to
# catch. Every issue is {code, ko, en, ja} so the frontend renders it through
# the same t()/lang-keyed plumbing every other localized message uses.
# --------------------------------------------------------------------------

class MappingIssue(TypedDict):
    code: str
    ko: str
    en: str
    ja: str


def _issue(code: str, ko: str, en: str, ja: str) -> MappingIssue:
    return {"code": code, "ko": ko, "en": en, "ja": ja}


_COUNT_FIELD_LABELS = {
    "period": {"ko": "시간대(Period)", "en": "period", "ja": "時間帯(Period)"},
    "node": {"ko": "교차로(Node)", "en": "node", "ja": "ノード(Node)"},
    "movement": {"ko": "이동류(Movement)", "en": "movement", "ja": "方向別交通量(Movement)"},
    "observed_volume": {"ko": "실측 교통량(Observed)", "en": "observed volume", "ja": "実測交通量(Observed)"},
}

_TRAVEL_FIELD_LABELS = {
    "period": {"ko": "시간대(Period)", "en": "period", "ja": "時間帯(Period)"},
    "route": {"ko": "루트/구간(Route)", "en": "route", "ja": "ルート/区間(Route)"},
    "observed_travel_time_sec": {"ko": "실측 통행시간(Observed)", "en": "observed travel time", "ja": "実測旅行時間(Observed)"},
}


def _values_identical_row_for_row(pairs: list[tuple[float, float]]) -> bool:
    return len(pairs) >= 2 and all(abs(a - b) < 1e-9 for a, b in pairs)


def validate_counts_mapping(field_columns: dict[str, int], rows: list[dict[str, Any]]) -> list[MappingIssue]:
    issues: list[MappingIssue] = []
    missing = [k for k in REQUIRED_COUNT_FIELDS if k not in field_columns]
    if "node_id" not in field_columns and "node_name" not in field_columns:
        missing.append("node")

    if missing:
        labels = {lang: ", ".join(_COUNT_FIELD_LABELS[k][lang] for k in missing) for lang in ("ko", "en", "ja")}
        issues.append(_issue(
            "counts_required_field_unmapped",
            f"다음 필수 항목이 매핑되지 않았습니다: {labels['ko']}. 교차로, 이동류, 시간대, 실측 교통량 열을 모두 지정해야 합니다.",
            f"The following required fields are not mapped: {labels['en']}. Node, movement, period, and observed volume must all be mapped.",
            f"次の必須項目がマッピングされていません: {labels['ja']}。ノード、方向別交通量、時間帯、実測交通量の列をすべて指定してください。",
        ))
        return issues  # further checks need these fields to mean anything

    observed_col = field_columns.get("observed_volume")
    model_col = field_columns.get("model_volume")

    if model_col is not None and observed_col == model_col:
        issues.append(_issue(
            "counts_observed_model_same_column",
            "실측 교통량과 모델 교통량이 같은 열로 매핑되었습니다. 서로 다른 열을 선택해주세요.",
            "Observed volume and model volume are mapped to the same column. Please choose different columns for each.",
            "実測交通量とモデル交通量が同じ列にマッピングされています。それぞれ別の列を選択してください。",
        ))

    observed_values = [row["observed_volume"] for row in rows if row.get("observed_volume") is not None]
    if not observed_values:
        issues.append(_issue(
            "counts_observed_no_numeric_values",
            "매핑한 실측 교통량 열에서 사용 가능한 숫자 값을 찾을 수 없습니다 (빈 값이거나 숫자로 인식되지 않습니다). 다른 열을 선택했는지 확인해주세요.",
            "No usable numeric values were found in the mapped observed volume column (it is empty or not numeric). Please check that the correct column is selected.",
            "マッピングした実測交通量列に有効な数値が見つかりませんでした(空欄か数値として認識できません)。正しい列を選択しているか確認してください。",
        ))

    if model_col is not None and observed_col != model_col:
        pairs = [
            (row["observed_volume"], row["model_volume"])
            for row in rows
            if row.get("observed_volume") is not None and row.get("model_volume") is not None
        ]
        if _values_identical_row_for_row(pairs):
            issues.append(_issue(
                "counts_self_comparison_trap",
                "실측 교통량과 모델 교통량이 서로 다른 열로 매핑되었지만, 값이 행마다 완전히 동일합니다. 모델 결과를 실측값으로 잘못 매핑했을 가능성이 큽니다 — 이 상태로 가져오면 GEH가 모델을 자기 자신과 비교하여 항상 통과로만 나타나고, 검증이 아무 의미가 없어집니다. 실제 현장에서 관측한 값이 들어있는 열을 선택해주세요.",
                "Observed and model volume are mapped to different columns, but every value is identical row-for-row. This usually means modelled output was mapped into the observed column by mistake — importing as-is would make GEH compare the model against itself and always pass, which defeats the purpose of validation. Please map the column that actually holds field-observed counts.",
                "実測交通量とモデル交通量は別の列にマッピングされていますが、値が全ての行で完全に一致しています。モデルの出力を誤って実測値としてマッピングした可能性が高いです — このまま取り込むとGEH検証はモデルを自分自身と比較することになり、常に合格となり検証の意味がなくなります。実際に現地で観測した値が入っている列を選択してください。",
            ))

    return issues


def validate_travel_times_mapping(field_columns: dict[str, int], rows: list[dict[str, Any]]) -> list[MappingIssue]:
    issues: list[MappingIssue] = []
    missing = [k for k in REQUIRED_TRAVEL_TIME_FIELDS if k not in field_columns]
    if "route_id" not in field_columns and "route_name" not in field_columns:
        missing.append("route")

    if missing:
        labels = {lang: ", ".join(_TRAVEL_FIELD_LABELS[k][lang] for k in missing) for lang in ("ko", "en", "ja")}
        issues.append(_issue(
            "travel_times_required_field_unmapped",
            f"다음 필수 항목이 매핑되지 않았습니다: {labels['ko']}. 루트, 시간대, 실측 통행시간 열을 모두 지정해야 합니다.",
            f"The following required fields are not mapped: {labels['en']}. Route, period, and observed travel time must all be mapped.",
            f"次の必須項目がマッピングされていません: {labels['ja']}。ルート、時間帯、実測旅行時間の列をすべて指定してください。",
        ))
        return issues

    observed_col = field_columns.get("observed_travel_time_sec")
    model_col = field_columns.get("model_travel_time_sec")

    if model_col is not None and observed_col == model_col:
        issues.append(_issue(
            "travel_times_observed_model_same_column",
            "실측 통행시간과 모델 통행시간이 같은 열로 매핑되었습니다. 서로 다른 열을 선택해주세요.",
            "Observed travel time and model travel time are mapped to the same column. Please choose different columns for each.",
            "実測旅行時間とモデル旅行時間が同じ列にマッピングされています。それぞれ別の列を選択してください。",
        ))

    observed_values = [row["observed_travel_time_sec"] for row in rows if row.get("observed_travel_time_sec") is not None]
    if not observed_values:
        issues.append(_issue(
            "travel_times_observed_no_numeric_values",
            "매핑한 실측 통행시간 열에서 사용 가능한 숫자 값을 찾을 수 없습니다 (빈 값이거나 숫자로 인식되지 않습니다). 다른 열을 선택했는지 확인해주세요.",
            "No usable numeric values were found in the mapped observed travel time column (it is empty or not numeric). Please check that the correct column is selected.",
            "マッピングした実測旅行時間列に有効な数値が見つかりませんでした(空欄か数値として認識できません)。正しい列を選択しているか確認してください。",
        ))

    if model_col is not None and observed_col != model_col:
        pairs = [
            (row["observed_travel_time_sec"], row["model_travel_time_sec"])
            for row in rows
            if row.get("observed_travel_time_sec") is not None and row.get("model_travel_time_sec") is not None
        ]
        if _values_identical_row_for_row(pairs):
            issues.append(_issue(
                "travel_times_self_comparison_trap",
                "실측 통행시간과 모델 통행시간이 서로 다른 열로 매핑되었지만, 값이 행마다 완전히 동일합니다. 모델 결과를 실측값으로 잘못 매핑했을 가능성이 큽니다 — 이 상태로 가져오면 검증이 모델을 자기 자신과 비교하게 되어 항상 통과로만 나타나고 아무 의미가 없어집니다. 실제 현장에서 측정한 값이 들어있는 열을 선택해주세요.",
                "Observed and model travel time are mapped to different columns, but every value is identical row-for-row. This usually means modelled output was mapped into the observed column by mistake — importing as-is would make validation compare the model against itself and always pass, which is meaningless. Please map the column that actually holds field-measured travel times.",
                "実測旅行時間とモデル旅行時間は別の列にマッピングされていますが、値が全ての行で完全に一致しています。モデルの出力を誤って実測値としてマッピングした可能性が高いです — このまま取り込むと検証はモデルを自分自身と比較することになり、常に合格となり意味がなくなります。実際に現地で計測した値が入っている列を選択してください。",
            ))

    return issues


# --------------------------------------------------------------------------
# Row resolution + preview/import.
# --------------------------------------------------------------------------

def _resolve_field_columns(ws, spec_fields: dict[str, int], allowed_fields: list[str]) -> dict[str, int]:
    max_col = ws.max_column or 0
    result: dict[str, int] = {}
    for field, col in spec_fields.items():
        if field not in allowed_fields:
            continue
        if col is None or col < 0 or col >= max_col:
            continue
        result[field] = col
    return result


def _header_row_text(ws, header_row: int) -> list[str]:
    row = next(ws.iter_rows(min_row=header_row, max_row=header_row, values_only=True), ())
    return [validation_module._clean_string(v) for v in row]


def _text_for_column(headers_text: list[str], col_idx: int) -> str:
    return headers_text[col_idx] if 0 <= col_idx < len(headers_text) else ""


class MappingSpec(TypedDict):
    sheet: str
    header_row: int
    fields: dict[str, int]


def _build_counts_rows(wb, spec: MappingSpec) -> tuple[dict[str, int], list[dict[str, Any]], list[str]]:
    if spec["sheet"] not in wb.sheetnames:
        raise ValueError(f"Sheet '{spec['sheet']}' was not found in the uploaded workbook.")
    ws = wb[spec["sheet"]]
    max_row = ws.max_row or 1
    if spec["header_row"] < 1 or spec["header_row"] > max_row:
        raise ValueError(f"header_row {spec['header_row']} is outside sheet '{spec['sheet']}''s row range (1-{max_row}).")
    field_columns = _resolve_field_columns(ws, spec["fields"], MAPPABLE_COUNT_FIELDS)
    warnings: list[str] = []
    rows = validation_module._parse_counts_sheet(ws, warnings, headers=field_columns, min_row=spec["header_row"] + 1)
    headers_text = _header_row_text(ws, spec["header_row"])
    return field_columns, rows, headers_text


def _build_travel_times_rows(wb, spec: MappingSpec) -> tuple[dict[str, int], list[dict[str, Any]], list[str]]:
    if spec["sheet"] not in wb.sheetnames:
        raise ValueError(f"Sheet '{spec['sheet']}' was not found in the uploaded workbook.")
    ws = wb[spec["sheet"]]
    max_row = ws.max_row or 1
    if spec["header_row"] < 1 or spec["header_row"] > max_row:
        raise ValueError(f"header_row {spec['header_row']} is outside sheet '{spec['sheet']}''s row range (1-{max_row}).")
    field_columns = _resolve_field_columns(ws, spec["fields"], MAPPABLE_TRAVEL_TIME_FIELDS)
    rows = validation_module._parse_travel_times_standard(ws, headers=field_columns, min_row=spec["header_row"] + 1)
    headers_text = _header_row_text(ws, spec["header_row"])
    return field_columns, rows, headers_text


def preview_mapping(token: str, counts: MappingSpec | None, travel_times: MappingSpec | None) -> dict[str, Any]:
    if counts is None and travel_times is None:
        raise ValueError("At least one of counts or travel_times mapping must be provided.")

    wb = load_staged_workbook(token)
    result: dict[str, Any] = {"counts_preview": None, "travel_times_preview": None}

    if counts is not None:
        field_columns, rows, _headers_text = _build_counts_rows(wb, counts)
        issues = validate_counts_mapping(field_columns, rows)
        result["counts_preview"] = {
            "field_columns": field_columns,
            "total_rows": len(rows),
            "rows": rows[:MAX_PREVIEW_ROWS],
            "truncated": len(rows) > MAX_PREVIEW_ROWS,
            "issues": issues,
        }

    if travel_times is not None:
        field_columns, rows, _headers_text = _build_travel_times_rows(wb, travel_times)
        issues = validate_travel_times_mapping(field_columns, rows)
        result["travel_times_preview"] = {
            "field_columns": field_columns,
            "total_rows": len(rows),
            "rows": rows[:MAX_PREVIEW_ROWS],
            "truncated": len(rows) > MAX_PREVIEW_ROWS,
            "issues": issues,
        }

    return result


def build_canonical_workbook_bytes(
    counts_rows: list[dict[str, Any]],
    travel_time_rows: list[dict[str, Any]],
) -> bytes:
    """Build a workbook shaped exactly like the standard template (sheets
    literally named ``Counts``/``TravelTimes``, canonical headers) from
    already-mapped rows, so re-parsing it through the UNCHANGED
    ``load_base_workbook`` path is guaranteed to reproduce these exact rows."""
    _require_openpyxl()
    wb = openpyxl.Workbook()
    counts_ws = wb.active
    counts_ws.title = "Counts"
    counts_ws.append(list(validation_module.STANDARD_COUNTS_HEADERS))
    for row in counts_rows:
        counts_ws.append([row.get(h) for h in validation_module.STANDARD_COUNTS_HEADERS])

    travel_ws = wb.create_sheet("TravelTimes")
    travel_ws.append(list(validation_module.STANDARD_TRAVEL_TIME_HEADERS))
    for row in travel_time_rows:
        travel_ws.append([row.get(h) for h in validation_module.STANDARD_TRAVEL_TIME_HEADERS])

    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def import_mapping(token: str, counts: MappingSpec | None, travel_times: MappingSpec | None) -> dict[str, Any]:
    if counts is None and travel_times is None:
        raise ValueError("At least one of counts or travel_times mapping must be provided.")

    wb = load_staged_workbook(token)
    all_issues: list[MappingIssue] = []

    counts_field_columns: dict[str, int] = {}
    counts_rows: list[dict[str, Any]] = []
    counts_headers_text: list[str] = []
    if counts is not None:
        counts_field_columns, counts_rows, counts_headers_text = _build_counts_rows(wb, counts)
        all_issues.extend(validate_counts_mapping(counts_field_columns, counts_rows))

    travel_field_columns: dict[str, int] = {}
    travel_rows: list[dict[str, Any]] = []
    travel_headers_text: list[str] = []
    if travel_times is not None:
        travel_field_columns, travel_rows, travel_headers_text = _build_travel_times_rows(wb, travel_times)
        all_issues.extend(validate_travel_times_mapping(travel_field_columns, travel_rows))

    if all_issues:
        raise MappingRefusedError(all_issues)

    workbook_bytes = build_canonical_workbook_bytes(counts_rows, travel_rows)
    original_filename = _staged_filename(token)
    persisted_name = Path(original_filename).stem + ".xlsx"
    datasets.save_active_real_counts_excel(persisted_name, workbook_bytes)
    validation_module.clear_validation_caches()

    if counts is not None and counts_field_columns:
        remember_mapping(
            "counts",
            counts_headers_text,
            {field: _text_for_column(counts_headers_text, col) for field, col in counts_field_columns.items()},
        )
    if travel_times is not None and travel_field_columns:
        remember_mapping(
            "travel_times",
            travel_headers_text,
            {field: _text_for_column(travel_headers_text, col) for field, col in travel_field_columns.items()},
        )

    discard_staged_workbook(token)
    return validation_module.load_base_workbook(str(datasets.get_active_real_counts_excel()))
